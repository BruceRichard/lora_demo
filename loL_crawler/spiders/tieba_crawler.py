"""
基于 aiotieba 的异步高并发贴吧爬虫

依据研究文档 §"异步并发与事件循环机制" 以及 §"贴吧复杂数据拓扑的层级化解构":
- 三级拓扑:Thread(主题帖) -> Post(回复帖) -> Comment(楼中楼)
- 通过 client.get_threads / get_posts / get_comments 解构
- 楼中楼的 contents.ats 用于还原多轮辩论对话

输出:JSONL 文件,每行一个样本 dict,字段包含 tid/pid/text/author/level/comments 等
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional

from tqdm import tqdm

try:
    import aiotieba  # type: ignore
    HAS_AIOTIEBA = True
except ImportError:  # 提供离线降级,方便在没有安装 aiotieba 的环境下测试
    HAS_AIOTIEBA = False

from config import (
    DATA_DIR, LOG_DIR, PROCESSED_DIR, RAW_DIR, TARGET_TIEBAS,
    TIEBA_CONCURRENCY, TIEBA_PN_MAX_COMMENTS, TIEBA_PN_MAX_POSTS,
    TIEBA_PN_MAX_THREADS, TIEBA_RN_PER_PAGE, TIEBA_SORT_HOT, TIEBA_SORT_NEW,
)
from utils import ProgressTracker, get_logger, async_jitter_sleep, truncate_bytes

LOG_PATH = LOG_DIR / "tieba_crawler.log"
logger = get_logger("tieba_crawler", LOG_PATH)


# ---------------- 数据提取 ----------------
def _post_to_dict(post: Any) -> dict:
    """将 aiotieba Post / Thread / Comment 转字典(尽量使用属性访问,容错处理)"""
    # helper to coerce values to JSON-serializable primitives
    def _safe(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (str, int, float, bool)):
            return v
        if isinstance(v, bytes):
            try:
                return v.decode("utf-8")
            except Exception:
                return str(v)
        # fallback to str() for unknown objects (e.g. URL objects)
        try:
            return str(v)
        except Exception:
            return None

    out: dict[str, Any] = {
        "pid": _safe(getattr(post, "pid", None)),
        "tid": _safe(getattr(post, "tid", None)),
        "floor": _safe(getattr(post, "floor", None)),
        "time": _safe(getattr(post, "time", None)),
        "user_id": _safe(getattr(getattr(post, "user", None), "user_id", None)),
        "user_name": _safe(getattr(getattr(post, "user", None), "user_name", None)),
        "level": _safe(getattr(getattr(post, "user", None), "level", None)),
        "ip": _safe(getattr(post, "ip", None)),
    }
    title = getattr(post, "title", None)
    if title:
        out["title"] = _safe(title)
    contents: list[dict[str, Any]] = []
    for c in getattr(post, "contents", []) or []:
        if isinstance(c, str):
            contents.append({"type": "text", "text": _safe(c)})
        elif isinstance(c, dict):
            # ensure dict values are safe
            contents.append({k: _safe(v) for k, v in c.items()})
        else:
            contents.append({
                "type": _safe(getattr(c, "type", "text")),
                "text": _safe(getattr(c, "text", "")) or "",
                "url": _safe(getattr(c, "url", None)),
                "src": _safe(getattr(c, "src", None)),
            })
    out["contents"] = contents
    return out


def _comment_to_dict(comment: Any) -> dict:
    def _safe(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (str, int, float, bool)):
            return v
        if isinstance(v, bytes):
            try:
                return v.decode("utf-8")
            except Exception:
                return str(v)
        try:
            return str(v)
        except Exception:
            return None

    out = {
        "cid": _safe(getattr(comment, "cid", None)),
        "pid": _safe(getattr(comment, "pid", None)),
        "tid": _safe(getattr(comment, "tid", None)),
        "floor": _safe(getattr(comment, "floor", None)),
        "time": _safe(getattr(comment, "time", None)),
        "user_id": _safe(getattr(getattr(comment, "user", None), "user_id", None)),
        "user_name": _safe(getattr(getattr(comment, "user", None), "user_name", None)),
    }
    out["text"] = _safe(getattr(comment, "text", None))
    return out


# ---------------- 主爬虫 ----------------
class TiebaCrawler:
    """基于 aiotieba 的异步高并发贴吧爬虫"""

    def __init__(
        self,
        bduss: str,
        fname: str,
        output_dir: Path = RAW_DIR,
        max_threads_pages: int = TIEBA_PN_MAX_THREADS,
        max_posts_pages: int = TIEBA_PN_MAX_POSTS,
        max_comments_pages: int = TIEBA_PN_MAX_COMMENTS,
        rn_per_page: int = TIEBA_RN_PER_PAGE,
        concurrency: int = TIEBA_CONCURRENCY,
    ) -> None:
        if not HAS_AIOTIEBA:
            raise RuntimeError(
                "aiotieba 未安装,请先 `pip install aiotieba`。"
                "或者使用 mock 模式:python spiders/tieba_crawler.py --mock"
            )
        self.bduss = bduss
        self.fname = fname
        self.output_dir = output_dir
        self.max_threads_pages = max_threads_pages
        self.max_posts_pages = max_posts_pages
        self.max_comments_pages = max_comments_pages
        self.rn = rn_per_page
        self.concurrency = concurrency
        self.sem = asyncio.Semaphore(concurrency)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress = ProgressTracker(output_dir / f"{fname}_threads.done")
        self.posts_progress = ProgressTracker(output_dir / f"{fname}_posts.done")
        self.comments_progress = ProgressTracker(output_dir / f"{fname}_comments.done")

    def _client(self) -> "aiotieba.Client":
        return aiotieba.Client(self.bduss)

    async def fetch_threads(self, sort: int) -> list[dict]:
        out: list[dict] = []
        async with self._client() as client:
            for pn in range(1, self.max_threads_pages + 1):
                threads = None
                # retry with increasing timeout
                for attempt in range(3):
                    try:
                        threads = await asyncio.wait_for(
                            client.get_threads(self.fname, pn=pn, sort=sort),
                            timeout=15 + attempt * 5,
                        )
                        break
                    except asyncio.TimeoutError:
                        logger.warning("get_threads pn=%s attempt=%s timeout", pn, attempt + 1)
                        await async_jitter_sleep(1.0 * (attempt + 1), 0.3)
                    except Exception as exc:  # 网络/限流
                        logger.warning("get_threads pn=%s 失败: %s", pn, exc)
                        await async_jitter_sleep(2.0, 0.5)
                if threads is None:
                    logger.warning("get_threads pn=%s 连续失败,跳过此页", pn)
                    continue
                for th in threads:
                    d = _post_to_dict(th)
                    d["fname"] = self.fname
                    d["sort"] = sort
                    out.append(d)
                logger.info("[%s] 主题帖 pn=%s -> %s 条", self.fname, pn, len(threads))
                await async_jitter_sleep(0.4, 0.4)
        return out

    async def fetch_posts(self, tid: int) -> list[dict]:
        out: list[dict] = []
        async with self._client() as client:
            for pn in range(1, self.max_posts_pages + 1):
                posts = None
                for attempt in range(3):
                    try:
                        posts = await asyncio.wait_for(
                            client.get_posts(tid, pn=pn, rn=self.rn, with_comments=True),
                            timeout=20 + attempt * 10,
                        )
                        break
                    except asyncio.TimeoutError:
                        logger.warning("get_posts tid=%s pn=%s attempt=%s timeout", tid, pn, attempt + 1)
                        await async_jitter_sleep(1.5 * (attempt + 1), 0.3)
                    except Exception as exc:
                        logger.warning("get_posts tid=%s pn=%s 失败: %s", tid, pn, exc)
                        await async_jitter_sleep(2.0, 0.5)
                if posts is None:
                    # total failure fetching this page: signal upstream (None) so caller can retry later
                    logger.warning("get_posts tid=%s pn=%s 连续失败,返回 None 以便重试", tid, pn)
                    return None
                if not posts:
                    break
                for p in posts:
                    d = _post_to_dict(p)
                    out.append(d)
                logger.info("tid=%s pn=%s -> %s 楼", tid, pn, len(posts))
                await async_jitter_sleep(1.0, 0.5)
        return out

    async def fetch_comments(self, tid: int, pid: int) -> list[dict]:
        out: list[dict] = []
        async with self._client() as client:
            for pn in range(1, self.max_comments_pages + 1):
                cmts = None
                for attempt in range(3):
                    try:
                        cmts = await asyncio.wait_for(client.get_comments(tid, pid, pn=pn), timeout=15 + attempt * 5)
                        break
                    except asyncio.TimeoutError:
                        logger.warning("get_comments tid=%s pid=%s pn=%s attempt=%s timeout", tid, pid, pn, attempt + 1)
                        await async_jitter_sleep(1.0 * (attempt + 1), 0.3)
                    except Exception as exc:
                        logger.warning("get_comments tid=%s pid=%s pn=%s: %s", tid, pid, pn, exc)
                        await async_jitter_sleep(2.0, 0.5)
                if cmts is None:
                    logger.warning("get_comments tid=%s pid=%s pn=%s 连续失败,跳过该 pid", tid, pid, pn)
                    return []
                if not cmts:
                    break
                for c in cmts:
                    d = _comment_to_dict(c)
                    out.append(d)
                await async_jitter_sleep(0.3, 0.4)
        return out

    async def process_thread(self, thread: dict) -> dict:
        """处理单条主题帖 -> 拉取所有楼层 + 楼中楼"""
        tid = thread.get("tid")
        if not tid:
            return {"thread": thread, "posts": [], "comments": []}
        if str(tid) in self.posts_progress:
            return {"thread": thread, "skipped": True}
        async with self.sem:
            posts = await self.fetch_posts(tid)
        # fetch_posts may return None to signal repeated failures/timeouts
        if posts is None:
            logger.warning("tid=%s fetch_posts 连续失败,稍后可重试", tid)
            # do not mark this tid as done so it can be retried later
            return {"thread": thread, "skipped": False, "failed": True}
        all_comments: list[dict] = []
        for p in posts:
            pid = p.get("pid")
            if pid and str(pid) not in self.comments_progress:
                async with self.sem:
                    cmts = await self.fetch_comments(tid, pid)
                all_comments.extend(cmts)
                self.comments_progress.add(str(pid))
        self.posts_progress.add(str(tid))
        return {"thread": thread, "posts": posts, "comments": all_comments}

    async def run(self, sort: int = TIEBA_SORT_HOT) -> None:
        logger.info("开始抓取 %s,sort=%s", self.fname, sort)
        threads = await self.fetch_threads(sort)
        thread_path = self.output_dir / f"{self.fname}_threads.jsonl"
        with open(thread_path, "w", encoding="utf-8") as f:
            for th in threads:
                f.write(json.dumps(th, ensure_ascii=False) + "\n")
        # 并发处理每个 thread
        tasks = [asyncio.create_task(self.process_thread(th)) for th in threads]
        for fut in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc=f"{self.fname} posts"):
            res = await fut
            tid = res.get("thread", {}).get("tid")
            if not tid or res.get("skipped"):
                continue
            out_path = self.output_dir / f"{self.fname}_tid_{tid}.jsonl"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
        logger.info("%s 抓取完成,共 %s 个主题帖", self.fname, len(threads))


# ---------------- 离线 mock 模式(便于联调) ----------------
class MockTiebaCrawler:
    """未安装 aiotieba 或无 BDUSS 时的离线测试模式,用于打通流水线"""

    def __init__(self, fname: str, output_dir: Path = RAW_DIR):
        self.fname = fname
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def run(self, n_threads: int = 5) -> None:
        sample = {
            "fname": self.fname,
            "thread": {
                "tid": 100001,
                "title": f"[{self.fname}] 如何评价今天 RNG 的表现?",
                "user_name": "mock_user",
                "contents": [{"type": "text", "text": "RNG 输得彻底,中辅两个人真的在梦游😅"}],
            },
            "posts": [
                {"pid": 1, "floor": 1, "text": "RNG 中辅 一直在送,真的抽象"},
                {"pid": 2, "floor": 2, "text": "对面的 LPL 战神 T1 给你点赞🐶"},
                {"pid": 3, "floor": 3, "text": "舒服了舒服了舒服了"},
            ],
            "comments": [
                {"cid": 11, "pid": 1, "text": "@mock_user 你是不是云玩家?"},
                {"cid": 12, "pid": 1, "text": "回楼上的,输了就是输了还要硬洗?"},
            ],
        }
        for i in range(n_threads):
            data = json.loads(json.dumps(sample))
            data["thread"]["tid"] = 100000 + i
            data["thread"]["title"] = f"[{self.fname}] 样例主题 {i+1}"
            out = self.output_dir / f"{self.fname}_tid_{data['thread']['tid']}.jsonl"
            with open(out, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        logger.info("[mock] %s 生成 %s 条样本", self.fname, n_threads)


# ---------------- CLI ----------------
def main() -> None:
    parser = argparse.ArgumentParser(description="抗压背锅吧异步爬虫")
    parser.add_argument("--bduss", default=None, help="百度 BDUSS cookie")
    parser.add_argument("--fbar", default="kangya", help="贴吧英文短名(默认抗压背锅吧)")
    parser.add_argument("--sort", type=int, default=TIEBA_SORT_HOT, help="排序方式 1=热度 5=时间")
    parser.add_argument("--mock", action="store_true", help="离线 mock 模式,无需 BDUSS")
    parser.add_argument("--pages", type=int, default=3, help="主题帖页数")
    args = parser.parse_args()

    if args.mock or not args.bduss:
        c = MockTiebaCrawler(args.fbar)
        asyncio.run(c.run(n_threads=4))
        return
    c = TiebaCrawler(
        bduss=args.bduss,
        fname=args.fbar,
        max_threads_pages=args.pages,
    )
    asyncio.run(c.run(sort=args.sort))


if __name__ == "__main__":
    main()
