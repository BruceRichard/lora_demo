"""
Bilibili 弹幕 / 视频元信息爬虫

依据研究文档 §"多元观赛生态数据的横向扩展: NGA 论坛与 Bilibili 弹幕池":
- 借助 plugin-bilibili-bangumi 等 API 组件框架
- 抓取目标视频 oid 对应的历史弹幕分段(protobuf / xml)
- 输出:每条弹幕 {timestamp, content, user_hash, mode, font_size, color}

为了避免引入额外的 proto 解析依赖,默认采用老版 https://api.bilibili.com/x/v1/dm/list.so (XML)
同时给出 v2 proto 抓取的 URL 模板,使用可选用依赖 `protobuf` 时启用。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional

import aiohttp
from tqdm import tqdm

from config import (
    BILI_API_DANMAKU_SEG, BILI_API_DANMAKU_XML, BILI_API_VIDEO_INFO,
    BILI_HEADERS, BILI_REQUEST_DELAY, LOG_DIR, RAW_DIR,
)
from utils import get_logger, async_jitter_sleep

LOG_PATH = LOG_DIR / "bili_crawler.log"
logger = get_logger("bili_crawler", LOG_PATH)


DANMAKU_XML_D_TAG = "d"
DANMAKU_XML_ATTRS = ("p",)  # p 属性包含 playtime,mode,fontsize,color,user_hash,rowid

# 检查 protobuf 包是否可用 (默认无, 走 XML 旧接口)
try:
    from google.protobuf.json_format import MessageToJson  # type: ignore  # noqa: F401
    from danmaku_proto.dm_web_seg_pb2 import DmSegMobileReply  # type: ignore  # noqa: F401
    HAS_PROTO = True
except ImportError:
    HAS_PROTO = False


@dataclass
class Danmaku:
    timestamp: float
    mode: int
    font_size: int
    color: int
    user_hash: str
    row_id: int
    text: str


def parse_danmaku_xml(xml_bytes: bytes) -> list[Danmaku]:
    out: list[Danmaku] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("弹幕 XML 解析失败: %s", exc)
        return out
    for node in root.findall(DANMAKU_XML_D_TAG):
        p_attr = node.attrib.get("p", "")
        parts = p_attr.split(",")
        if len(parts) < 6:
            continue
        try:
            ts = float(parts[0])
            mode = int(parts[1])
            font_size = int(parts[2])
            color = int(parts[3])
            user_hash = parts[6] if len(parts) > 6 else ""
            row_id = int(parts[7]) if len(parts) > 7 else 0
        except ValueError:
            continue
        out.append(
            Danmaku(
                timestamp=ts,
                mode=mode,
                font_size=font_size,
                color=color,
                user_hash=user_hash,
                row_id=row_id,
                text=(node.text or "").strip(),
            )
        )
    return out


def _sanitize_bvid(bvid: str) -> str:
    """去除尾部可能误带入的 / 或空白"""
    return re.sub(r'[/\\\s]+$', '', bvid)


class BilibiliCrawler:
    def __init__(
        self,
        output_dir: Path = RAW_DIR,
        cookie: Optional[str] = None,
        concurrency: int = 4,
        max_segments_per_video: int = 1000,
        request_delay: float = BILI_REQUEST_DELAY,
    ) -> None:
        self.output_dir = output_dir
        self.cookie = cookie
        self.concurrency = concurrency
        self.max_segments_per_video = max_segments_per_video
        self.request_delay = request_delay
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sem = asyncio.Semaphore(concurrency)
        self.headers = dict(BILI_HEADERS)
        if cookie:
            self.headers["Cookie"] = cookie

    async def _get(self, session: aiohttp.ClientSession, url: str, params: Optional[dict] = None, referer: Optional[str] = None) -> Optional[bytes]:
        headers = dict(self.headers)
        if referer:
            headers["Referer"] = referer
        for attempt in range(3):
            await async_jitter_sleep(self.request_delay, 0.3)
            try:
                async with session.get(url, params=params, headers=headers, timeout=30) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    if resp.status == 304:
                        return None  # 无更多段,正常停止
                    logger.warning("Bili HTTP %s %s: 重试剩余 %s", resp.status, url, 2 - attempt)
            except Exception as exc:
                logger.warning("Bili 抓取失败 attempt=%s: %s", attempt, exc)
        return b""

    async def get_video_info(self, session: aiohttp.ClientSession, bvid: str) -> dict:
        bvid = _sanitize_bvid(bvid)
        url = f"{BILI_API_VIDEO_INFO}?bvid={bvid}"
        referer = f"https://www.bilibili.com/video/{bvid}"
        raw = await self._get(session, url, referer=referer)
        if not raw:
            return {}
        try:
            j = json.loads(raw.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return {}
        if j.get("code") != 0:
            logger.warning("Bili video info code=%s msg=%s", j.get("code"), j.get("message"))
            return {}
        return j.get("data", {})

    async def fetch_segment(self, session: aiohttp.ClientSession, oid: int, seg_index: int, referer: Optional[str] = None) -> list[Danmaku]:
        if HAS_PROTO:
            params = {"type": 1, "oid": oid, "segment_index": seg_index}
            raw = await self._get(session, BILI_API_DANMAKU_SEG, params=params, referer=referer)
            if not raw:
                return []
            try:
                reply = DmSegMobileReply()
                reply.ParseFromString(raw)
                return [
                    Danmaku(
                        timestamp=e.progress / 1000.0,
                        mode=e.mode,
                        font_size=e.fontsize,
                        color=e.color,
                        user_hash=e.midHash,
                        row_id=e.dm_id,
                        text=e.content,
                    )
                    for e in reply.elems
                ]
            except Exception as exc:
                logger.warning("proto 解析失败(seg=%s): %s", seg_index, exc)
                return []
        else:
            # protobuf 未安装 -> 使用旧版 XML 接口
            params = {"oid": oid, "type": 1}
            raw = await self._get(session, BILI_API_DANMAKU_XML, params=params, referer=referer)
            if not raw:
                return []
            return parse_danmaku_xml(raw)

    async def fetch_video_oid(self, session: aiohttp.ClientSession, bvid: str) -> Optional[int]:
        info = await self.get_video_info(session, bvid)
        return info.get("cid")

    async def crawl_video(self, session: aiohttp.ClientSession, bvid: str, title: Optional[str] = None) -> Path:
        bvid = _sanitize_bvid(bvid)
        oid = await self.fetch_video_oid(session, bvid)
        if not oid:
            logger.warning("[%s] 未获取到 cid,跳过", bvid)
            return self.output_dir / f"bili_{bvid}_empty.jsonl"
        referer = f"https://www.bilibili.com/video/{bvid}"
        seg_results: list[Danmaku] = []
        idx = 1
        pbar = tqdm(desc=f"bili {bvid}", total=self.max_segments_per_video)
        while idx <= self.max_segments_per_video:
            async with self.sem:
                seg = await self.fetch_segment(session, oid, idx, referer=referer)
            if not seg:
                break
            seg_results.extend(seg)
            idx += 1
            pbar.update(1)
            if len(seg) == 0:
                break
        pbar.close()
        out_path = self.output_dir / f"bili_{bvid}.jsonl"
        with open(out_path, "w", encoding="utf-8") as f:
            meta = {
                "_meta": True,
                "bvid": bvid,
                "oid": oid,
                "title": title,
                "danmaku_count": len(seg_results),
            }
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for d in seg_results:
                f.write(json.dumps(asdict(d), ensure_ascii=False) + "\n")
        logger.info("[%s] 弹幕 %s 条 -> %s", bvid, len(seg_results), out_path)
        return out_path

    async def run(self, bvids: Iterable[str]) -> None:
        async with aiohttp.ClientSession() as session:
            for bvid in bvids:
                await self.crawl_video(session, bvid)


class MockBilibiliCrawler:
    def __init__(self, output_dir: Path = RAW_DIR) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def run(self, n_videos: int = 3, danmaku_per_video: int = 50) -> None:
        for i in range(n_videos):
            bvid = f"mockBV{i+1:02d}"
            out_path = self.output_dir / f"bili_{bvid}.jsonl"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "_meta": True,
                    "bvid": bvid,
                    "oid": 123456 + i,
                    "title": f"样例视频 {i+1}",
                    "danmaku_count": danmaku_per_video,
                }, ensure_ascii=False) + "\n")
                for j in range(danmaku_per_video):
                    f.write(json.dumps({
                        "timestamp": j * 1.2,
                        "mode": 1,
                        "font_size": 25,
                        "color": 16777215,
                        "user_hash": "hash",
                        "row_id": j,
                        "text": ["舒服了", "这波拉胯", "牛逼!🐶", "T1 永远的神"][j % 4],
                    }, ensure_ascii=False) + "\n")
            logger.info("[mock] %s 生成 %s 条弹幕", bvid, danmaku_per_video)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bilibili 弹幕爬虫")
    parser.add_argument("--bvid", action="append", help="视频 BV id(可多次传参)", default=[])
    parser.add_argument("--cookie", default=None, help="B 站 cookie(选填)")
    parser.add_argument("--mock", action="store_true", help="离线 mock 模式")
    args = parser.parse_args()

    if args.mock or not args.bvid:
        asyncio.run(MockBilibiliCrawler().run())
    else:
        asyncio.run(BilibiliCrawler(cookie=args.cookie).run(args.bvid))


if __name__ == "__main__":
    main()
