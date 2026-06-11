"""
loL_crawler 主管线

一键运行:抓取 -> 预处理 -> SFT 构造
"""
from __future__ import annotations

import os
import argparse
import asyncio
import json
from pathlib import Path

from builders.sft_builder import SFTBuilder
from config import (
    LOG_DIR, PROCESSED_DIR, RAW_DIR, RESOURCES_DIR, SFT_DIR, TARGET_TIEBAS,
    DATADRAGON_DIR, TIEBA_CONCURRENCY,
)
from processors.preprocess import preprocess_post_text
from spiders.bili_crawler import BilibiliCrawler, MockBilibiliCrawler
from spiders.datadragon_downloader import download_latest, download_version
from spiders.nga_crawler import NGACrawler, MockNGACrawler
from spiders.tieba_crawler import MockTiebaCrawler, TiebaCrawler
from utils import get_logger
from dotenv import load_dotenv

LOG_PATH = LOG_DIR / "orchestrator.log"
logger = get_logger("orchestrator", LOG_PATH)


async def run_tieba(bduss: str | None, fbars: list[str], mock: bool, pages: int, concurrency: int = TIEBA_CONCURRENCY) -> None:
    """Run Tieba crawlers for each fbar. If bduss is None -> runs mock crawlers unless mock=True."""
    if mock or not bduss:
        for fb in fbars:
            await MockTiebaCrawler(fb).run()
        return
    for fb in fbars:
        crawler = TiebaCrawler(bduss=bduss, fname=fb, max_threads_pages=pages, concurrency=concurrency)
        await crawler.run()


async def run_nga(cookie: str | None, fids: list[str], mock: bool, pages: int) -> None:
    if mock or not cookie:
        for fid in fids:
            await MockNGACrawler(fid).run()
        return
    for fid in fids:
        crawler = NGACrawler(fid, cookie=cookie, max_pages=pages)
        await crawler.run()


async def run_bili(bvids: list[str], cookie: str | None, mock: bool) -> None:
    if mock or not bvids:
        await MockBilibiliCrawler().run()
        return
    crawler = BilibiliCrawler(cookie=cookie)
    await crawler.run(bvids)


async def run_datadragon(version: str | None, keep: int, lang: str = "en_US") -> None:
    if version:
        await download_version(version, lang=lang)
    else:
        await download_latest(versions_to_keep=keep)


def run_preprocess() -> None:
    """同步:对 RAW_DIR 做去重 + Demojize + 停用词, 输出 PROCESSED_DIR"""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    from processors.preprocess import ShortTextDeduplicator
    dedup = ShortTextDeduplicator()
    cnt = 0
    for p in sorted(RAW_DIR.glob("*.jsonl")):
        out = PROCESSED_DIR / p.name
        kept: list[str] = []
        n_total = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            n_total += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            matched = False
            for key in ("text", "title", "content", "raw_sample", "name"):
                v = rec.get(key)
                if isinstance(v, str):
                    matched = True
                    rec[key + "_clean"] = preprocess_post_text(v)
                    if rec[key + "_clean"] and not dedup.is_duplicate(rec[key + "_clean"]):
                        kept.append(json.dumps(rec, ensure_ascii=False))
            if not matched:
                kept.append(json.dumps(rec, ensure_ascii=False))
        out.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        if n_total:
            logger.info("  %-50s %6d lines → %d kept (%.1f%%)", p.name, n_total, len(kept), 100 * len(kept) / n_total)
        cnt += len(kept)
    logger.info("预处理完成,共保留 %s 条", cnt)


def run_build_sft(max_per_file: int) -> dict:
    builder = SFTBuilder(
        raw_dir=RAW_DIR,
        processed_dir=PROCESSED_DIR,
        sft_dir=SFT_DIR,
        meme_dict=RESOURCES_DIR / "meme_dict.json",
        datadragon_dir=DATADRAGON_DIR,
        max_samples_per_file=max_per_file,
    )
    return builder.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="LoL 观赛模型数据管线")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_all = sub.add_parser("all", help="全流程:抓取 -> 清洗 -> SFT")
    p_all.add_argument("--bduss", default=None)
    p_all.add_argument("--nga-cookie", default=None)
    p_all.add_argument("--bili-cookie", default=None)
    p_all.add_argument("--bvid", action="append", default=[])
    p_all.add_argument("--fbar", action="append", default=list(TARGET_TIEBAS.keys()))
    p_all.add_argument("--nga-fid", action="append", default=["-7"])
    p_all.add_argument("--pages", type=int, default=3)
    p_all.add_argument("--concurrency", type=int, default=TIEBA_CONCURRENCY,
                       help=f"Tieba concurrency (default from config: {TIEBA_CONCURRENCY})")
    p_all.add_argument("--max-sft", type=int, default=0)
    p_all.add_argument("--ddragon-version", default=None)
    p_all.add_argument("--ddragon-keep", type=int, default=2)
    p_all.add_argument("--ddragon-lang", default="en_US")
    p_all.add_argument("--mock", action="store_true", help="全部走 mock 离线模式")

    p_tieba = sub.add_parser("tieba", help="仅跑贴吧")
    p_tieba.add_argument("--bduss", default=None)
    p_tieba.add_argument("--fbar", action="append", default=list(TARGET_TIEBAS.keys()))
    p_tieba.add_argument("--pages", type=int, default=3)
    p_tieba.add_argument("--concurrency", type=int, default=TIEBA_CONCURRENCY,
                         help="Tieba concurrency (lower is safer)")
    p_tieba.add_argument("--mock", action="store_true")

    p_nga = sub.add_parser("nga", help="仅跑 NGA")
    p_nga.add_argument("--cookie", default=None)
    p_nga.add_argument("--fid", action="append", default=["-152678", "479"])
    p_nga.add_argument("--pages", type=int, default=3)
    p_nga.add_argument("--mock", action="store_true")

    p_bili = sub.add_parser("bili", help="仅跑 B 站弹幕")
    p_bili.add_argument("--cookie", default=None)
    p_bili.add_argument("--bvid", action="append", default=[])
    p_bili.add_argument("--mock", action="store_true")

    p_dd = sub.add_parser("datadragon", help="下载 Riot 静态数据")
    p_dd.add_argument("--version", default=None)
    p_dd.add_argument("--keep", type=int, default=3)
    p_dd.add_argument("--lang", default="en_US", help="语言,默认 en_US")

    p_pp = sub.add_parser("preprocess", help="对 raw/ 跑清洗")

    p_sft = sub.add_parser("build-sft", help="生成 SFT 数据")
    p_sft.add_argument("--max", type=int, default=0)

    args = parser.parse_args()
    # 优先 CLI 参数, 未提供则从 .env 获取(由 load_dotenv 已写入 os.environ)
    env_bduss = os.getenv("BDUSS")
    env_nga = os.getenv("NGA_COOKIE")
    env_bili = os.getenv("BILI_COOKIE")
    if args.cmd == "tieba":
        bduss = args.bduss or env_bduss
        asyncio.run(run_tieba(bduss, args.fbar, args.mock, args.pages, args.concurrency))
    elif args.cmd == "nga":
        cookie = args.cookie or env_nga
        asyncio.run(run_nga(cookie, args.fid, args.mock, args.pages))
    elif args.cmd == "bili":
        cookie = args.cookie or env_bili
        asyncio.run(run_bili(args.bvid, cookie, args.mock))
    elif args.cmd == "datadragon":
        asyncio.run(run_datadragon(args.version, args.keep, args.lang))
    elif args.cmd == "preprocess":
        run_preprocess()
    elif args.cmd == "build-sft":
        run_build_sft(args.max)
    elif args.cmd == "all":
        bduss = args.bduss or env_bduss
        nga_cookie = args.nga_cookie or env_nga
        bili_cookie = args.bili_cookie or env_bili
        asyncio.run(run_datadragon(args.ddragon_version, args.ddragon_keep, args.ddragon_lang))
        asyncio.run(run_tieba(bduss, args.fbar, args.mock, args.pages, args.concurrency))
        asyncio.run(run_nga(nga_cookie, args.nga_fid, args.mock, args.pages))
        asyncio.run(run_bili(args.bvid, bili_cookie, args.mock))
        run_preprocess()
        run_build_sft(args.max_sft)


if __name__ == "__main__":
    # load .env into process env so main() can pick up BDUSS / NGA_COOKIE / BILI_COOKIE if not provided on CLI
    load_dotenv('.env')
    # sanitize common cookie env vars: remove accidental line breaks / surrounding quotes
    for _k in ("BDUSS", "NGA_COOKIE", "BILI_COOKIE"):
        v = os.getenv(_k)
        if not v:
            continue
        v2 = v.replace('\r', '').replace('\n', '').strip()
        if (v2.startswith('"') and v2.endswith('"')) or (v2.startswith("'") and v2.endswith("'")):
            v2 = v2[1:-1]
        os.environ[_k] = v2
    main()
