"""
NGA 论坛爬虫

依据研究文档 §"多元观赛生态数据的横向扩展":
- NGA read.php 携带 lite=js 等参数获取 JSON 格式纯数据响应
- 通过 Cookies 进行身份认证
- 战术分析、长图文复盘语料

注意:NGA 反爬较强,实际抓取需要登录 Cookie(ngaPassportCid / ngaPassportUid)。
脚本默认走 aiohttp 的异步方式抓 read.php 并解析 lite=js 响应;若失败,降级为 HTML 解析。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import aiohttp
from tqdm import tqdm

from config import LOG_DIR, NGA_BOARDS, NGA_RAW_URL, NGA_REFERER, NGA_REQUEST_DELAY, NGA_USER_AGENT, RAW_DIR
from utils import ProgressTracker, get_logger, async_jitter_sleep, truncate_bytes

LOG_PATH = LOG_DIR / "nga_crawler.log"
logger = get_logger("nga_crawler", LOG_PATH)


# 1=普通主题 2=合集 主题列表接口
NGA_THREAD_API = "https://bbs.nga.cn/thread.php"


def build_session_headers(cookie: Optional[str] = None) -> dict[str, str]:
    h = {
        "User-Agent": NGA_USER_AGENT,
        "Referer": NGA_REFERER,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
    }
    if cookie:
        h["Cookie"] = cookie
    return h


def parse_litejs_payload(text: str) -> list[dict]:
    """
    NGA lite=js 响应:
      旧版: window.script_muti_get_var_value([{...}]);
      新版: window.script_muti_get_var_store={\"data\":{\"__T\":{\"0\":{...},\"1\":{...}...}},...};
    同时兼容两种格式。
    """
    # 新版: window.script_muti_get_var_store={"data":{...}}
    m = re.search(r'window\.script_muti_get_var_store\s*=\s*(\{.*\})\s*;?\s*$', text, re.DOTALL)
    if m:
        try:
            raw = json.loads(m.group(1))
            # 主题列表在 data.__T 中, 是 {"0": {...}, "1": {...}, ...}
            threads_container = raw.get("data", {}).get("__T")
            if isinstance(threads_container, dict):
                out = []
                for k in sorted(threads_container.keys(), key=int):
                    t = threads_container[k]
                    if isinstance(t, dict):
                        out.append(t)
                return out
            # 也可能是 __T 直接在顶层
            threads_container = raw.get("__T")
            if isinstance(threads_container, dict):
                out = []
                for k in sorted(threads_container.keys(), key=int):
                    t = threads_container[k]
                    if isinstance(t, dict):
                        out.append(t)
                return out
        except json.JSONDecodeError as exc:
            logger.warning("parse_litejs_payload JSON 解析失败 (store): %s", exc)

    # 旧版: window.script_muti_get_var_value([{...}]);
    m = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                return json.loads(candidate.encode().decode("unicode_escape", errors="ignore"))
            except json.JSONDecodeError as exc:
                logger.warning("parse_litejs_payload JSON 二次解析失败 (value): %s", exc)

    snippet = text[:200].replace('\n', ' ').replace('\r', '')
    logger.warning("parse_litejs_payload 未匹配到任何已知格式, 响应开头: %s ...", snippet)
    return []


def _post_text_to_str(content: list) -> str:
    out = []
    for seg in content or []:
        if isinstance(seg, str):
            out.append(seg)
    return "".join(out)


class NGACrawler:
    def __init__(
        self,
        fid: str,
        cookie: Optional[str] = None,
        output_dir: Path = RAW_DIR,
        max_pages: int = 30,
        concurrency: int = 4,
    ) -> None:
        if fid not in NGA_BOARDS:
            logger.warning("未知 fid %s,仍尝试访问", fid)
        self.fid = fid
        self.board_name = NGA_BOARDS.get(fid, fid)
        self.cookie = cookie
        self.output_dir = output_dir
        self.max_pages = max_pages
        self.concurrency = concurrency
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress = ProgressTracker(output_dir / f"nga_{fid}_threads.done")

    async def _fetch(self, session: aiohttp.ClientSession, url: str, params: dict[str, Any]) -> str:
        for attempt in range(3):
            await async_jitter_sleep(NGA_REQUEST_DELAY, 0.3)
            try:
                async with session.get(url, params=params, timeout=30) as resp:
                    raw = await resp.read()
                    if resp.status != 200:
                        logger.warning("NGA 非 200: %s (url=%s), len=%s", resp.status, url, len(raw))
                        continue
                    # NGA 可能返回 GBK 编码页面, 优先 utf-8, 回退 gbk
                    for enc in ("utf-8", "gbk", "gb2312"):
                        try:
                            return raw.decode(enc)
                        except (UnicodeDecodeError, LookupError):
                            continue
                    return raw.decode("utf-8", errors="replace")
            except Exception as exc:
                logger.warning("NGA 抓取失败 attempt=%s: %s", attempt, exc)
        return ""

    async def list_threads(self, session: aiohttp.ClientSession, page: int) -> list[dict]:
        """抓取主题列表"""
        params = {
            "fid": self.fid,
            "page": page,
            "lite": "js",
        }
        text = await self._fetch(session, NGA_THREAD_API, params)
        items = parse_litejs_payload(text)
        if not items:
            return []
        return [it for it in items if isinstance(it, dict)]

    async def fetch_thread(self, session: aiohttp.ClientSession, tid: str, page: int = 1) -> dict:
        params = {
            "tid": tid,
            "page": page,
            "lite": "js",
        }
        text = await self._fetch(session, NGA_RAW_URL, params)
        # 尝试解析帖子正文
        content = ""
        m = re.search(r'window\.script_muti_get_var_store\s*=\s*(\{.*\})\s*;?\s*$', text, re.DOTALL)
        if m:
            try:
                raw_json = json.loads(m.group(1))
            except json.JSONDecodeError:
                raw_json = {}
            # 帖子回复在 __R 或 __U
            for key in ("__R", "__U"):
                container = raw_json.get("data", {}).get(key) or raw_json.get(key)
                if not isinstance(container, dict):
                    continue
                texts = []
                for k in sorted((k for k in container if k.isdigit()), key=int):
                    entry = container[k]
                    if not isinstance(entry, dict):
                        continue
                    c = entry.get("content") or []
                    if isinstance(c, list):
                        for seg in c:
                            if isinstance(seg, str):
                                texts.append(seg)
                            elif isinstance(seg, dict):
                                t = seg.get("text") or seg.get("content", "")
                                if isinstance(t, str):
                                    texts.append(t)
                    elif isinstance(c, str):
                        texts.append(c)
                if texts:
                    content = "".join(texts)
                    break
        return {
            "tid": tid,
            "page": page,
            "raw_len": len(text),
            "raw_sample": text[:400],
            "content": content[:2000],
        }

    async def run(self) -> None:
        async with aiohttp.ClientSession(headers=build_session_headers(self.cookie)) as session:
            all_threads: list[dict] = []
            for p in range(1, self.max_pages + 1):
                await async_jitter_sleep(NGA_REQUEST_DELAY, 0.2)
                tds = await self.list_threads(session, p)
                logger.info("[%s] 第 %s 页: %s 条", self.board_name, p, len(tds))
                all_threads.extend(tds)
                await async_jitter_sleep(0.6, 0.4)
            (self.output_dir / f"nga_{self.fid}_threads.jsonl").write_text(
                "\n".join(json.dumps(t, ensure_ascii=False) for t in all_threads) + "\n",
                encoding="utf-8",
            )
            sem = asyncio.Semaphore(self.concurrency)

            async def _worker(t: dict) -> Optional[dict]:
                tid = t.get("tid") or t.get("__T") or t.get("id")
                if not tid:
                    return None
                if str(tid) in self.progress:
                    return None
                async with sem:
                    res = await self.fetch_thread(session, str(tid), page=1)
                    self.progress.add(str(tid))
                return res

            tasks = [asyncio.create_task(_worker(t)) for t in all_threads if t]
            out_path = self.output_dir / f"nga_{self.fid}_posts.jsonl"
            with open(out_path, "w", encoding="utf-8") as f:
                for fut in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc=f"NGA {self.board_name}"):
                    res = await fut
                    if res:
                        f.write(json.dumps(res, ensure_ascii=False) + "\n")
            logger.info("NGA [%s] 完成,主题 %s 个", self.board_name, len(all_threads))


class MockNGACrawler:
    def __init__(self, fid: str, output_dir: Path = RAW_DIR):
        self.fid = fid
        self.output_dir = output_dir
        self.board_name = NGA_BOARDS.get(fid, fid)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def run(self, n_threads: int = 5) -> None:
        threads = [
            {"tid": 900000 + i, "title": f"[{self.board_name}] T1 vs GEN 战术复盘样例 {i+1}"}
            for i in range(n_threads)
        ]
        (self.output_dir / f"nga_{self.fid}_threads.jsonl").write_text(
            "\n".join(json.dumps(t, ensure_ascii=False) for t in threads) + "\n",
            encoding="utf-8",
        )
        sample = {
            "tid": 900000,
            "page": 1,
            "raw_len": 2000,
            "raw_sample": "<div class='post'><div class='postcontent'>本局 GEN 选择了 4 1 分推,前期控下小龙,中期利用...</div></div>",
        }
        with open(self.output_dir / f"nga_{self.fid}_posts.jsonl", "w", encoding="utf-8") as f:
            for t in threads:
                d = json.loads(json.dumps(sample))
                d["tid"] = t["tid"]
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        logger.info("[mock] NGA %s 生成 %s 条", self.board_name, n_threads)


def main() -> None:
    parser = argparse.ArgumentParser(description="NGA 论坛异步爬虫")
    parser.add_argument("--fid", default="-7", help="板块 id(默认 -7 英雄联盟)")
    parser.add_argument("--cookie", default=None, help="NGA 登录 cookie")
    parser.add_argument("--pages", type=int, default=5, help="主题列表页数")
    parser.add_argument("--mock", action="store_true", help="离线 mock 模式")
    args = parser.parse_args()

    if args.mock or not args.cookie:
        asyncio.run(MockNGACrawler(args.fid).run())
    else:
        asyncio.run(NGACrawler(args.fid, args.cookie, max_pages=args.pages).run())


if __name__ == "__main__":
    main()
