"""
Riot Data Dragon 静态知识库下载

依据研究文档 §"英雄联盟静态知识本体的结构化抽取与映射":
- Data Dragon 提供 JSON 静态数据文件,记录英雄、技能、装备等结构化参数
- 支持指定版本号(如 en_US-10.15.1)下载特定历史版本的英雄强弱生态
- 抓取后转换为 SFT 指令对:Question->Answer 双向映射
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Optional

import aiohttp
from tqdm import tqdm

from config import DATADRAGON_BASE, DATADRAGON_DIR, DATADRAGON_VERSIONS, LOG_DIR
from utils import get_logger, jitter_sleep

LOG_PATH = LOG_DIR / "datadragon.log"
logger = get_logger("datadragon", LOG_PATH)

DEFAULT_LANG = "en_US"


async def fetch_versions(session: aiohttp.ClientSession) -> list[str]:
    async with session.get(DATADRAGON_VERSIONS) as resp:
        text = await resp.text()
    return json.loads(text)


async def fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    for attempt in range(3):
        try:
            async with session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                logger.warning("DataDragon HTTP %s %s", resp.status, url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DataDragon 重试 %s: %s", attempt, exc)
        jitter_sleep(1.0, 0.5)
    return {}


async def download_version(version: str, lang: str = DEFAULT_LANG, only_champions: bool = True) -> Path:
    """
    下载指定版本的 champion.json / item.json
    返回版本目录
    """
    out_dir = DATADRAGON_DIR / f"{lang}-{version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as session:
        targets = ["champion.json", "item.json"] if not only_champions else ["champion.json"]
        for fname in targets:
            url = f"{DATADRAGON_BASE}/cdn/{version}/data/{lang}/{fname}"
            local = out_dir / fname
            if local.exists():
                continue
            data = await fetch_json(session, url)
            if data:
                local.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("已保存 %s (%s bytes)", local, local.stat().st_size)
            else:
                logger.warning("%s 抓取失败", url)
    return out_dir


async def download_latest(versions_to_keep: int = 3) -> list[Path]:
    async with aiohttp.ClientSession() as session:
        versions = await fetch_versions(session)
    keep = versions[:versions_to_keep]
    out_dirs: list[Path] = []
    for v in tqdm(keep, desc="DataDragon"):
        try:
            out_dirs.append(await download_version(v))
        except Exception as exc:  # noqa: BLE001
            logger.warning("版本 %s 下载失败: %s", v, exc)
    return out_dirs


def main() -> None:
    parser = argparse.ArgumentParser(description="Riot Data Dragon 下载")
    parser.add_argument("--version", default=None, help="指定版本,如 14.1.1")
    parser.add_argument("--lang", default=DEFAULT_LANG, help="语言,默认 en_US")
    parser.add_argument("--keep", type=int, default=3, help="未指定 version 时保留最新多少版本")
    args = parser.parse_args()

    if args.version:
        asyncio.run(download_version(args.version, args.lang))
    else:
        asyncio.run(download_latest(args.keep))


if __name__ == "__main__":
    main()
