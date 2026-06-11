import asyncio, json, os
from pathlib import Path
import aiohttp
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
cookie = os.getenv("BILI_COOKIE", "")

# 排行榜API 和 分区视频API
RANKING_URL = "https://api.bilibili.com/x/web-interface/ranking/v2"
REGION_URL = "https://api.bilibili.com/x/web-interface/ranking/region"
# 英雄联盟分区rid=17 (游戏->电子竞技) 或 搜索API
SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/all/v2"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}
if cookie:
    HEADERS["Cookie"] = cookie

# 尝试多个渠道凑齐20个
SEARCH_WORDS = [
    "英雄联盟 S赛",
    "LPL 季后赛",
    "MSI 季中赛",
    "英雄联盟 世界赛",
]


async def fetch_json(session, url, params=None):
    try:
        async with session.get(url, params=params, headers=HEADERS, timeout=15) as resp:
            if resp.status != 200:
                return None
            raw = await resp.read()
            return json.loads(raw)
    except Exception:
        return None


async def main():
    seen_bvids = set()
    videos = []

    async with aiohttp.ClientSession() as session:
        # 1) 全站排行榜
        data = await fetch_json(session, RANKING_URL, {"type": "all"})
        if data and data.get("code") == 0:
            for v in data.get("data", {}).get("list", []):
                bvid = v.get("bvid", "")
                title = v.get("title", "")
                # 只保留英雄联盟相关
                if bvid and bvid not in seen_bvids and any(kw in title for kw in ["英雄联盟","LOL","LPL","LCK","MSI","S赛","世界赛","季中","总决赛"]):
                    seen_bvids.add(bvid)
                    videos.append({
                        "bvid": bvid,
                        "title": title,
                        "author": v.get("owner", {}).get("name", ""),
                        "play": v.get("stat", {}).get("view", 0),
                        "danmaku": v.get("stat", {}).get("danmaku", 0),
                    })

        # 2) 游戏分区排行榜
        data = await fetch_json(session, "https://api.bilibili.com/x/web-interface/ranking/region",
                                {"rid": 4, "day": 7})  # rid=4 游戏
        if data and data.get("code") == 0:
            for v in data.get("data", []):
                bvid = v.get("bvid", "")
                title = v.get("title", "")
                if bvid and bvid not in seen_bvids:
                    seen_bvids.add(bvid)
                    videos.append({
                        "bvid": bvid,
                        "title": title,
                        "author": v.get("author", ""),
                        "play": v.get("play", 0),
                        "danmaku": v.get("video_review", 0),
                    })

        # 3) 搜索（多个关键词）
        for kw in SEARCH_WORDS:
            data = await fetch_json(session, SEARCH_URL, {"keyword": kw, "page": 1})
            if not data or data.get("code") != 0:
                continue
            for section in data.get("data", {}).get("result", []):
                if section.get("result_type") != "video":
                    continue
                for v in section.get("data", []):
                    bvid = v.get("bvid", "")
                    if bvid and bvid not in seen_bvids:
                        seen_bvids.add(bvid)
                        videos.append({
                            "bvid": bvid,
                            "title": v.get("title", ""),
                            "author": v.get("author", ""),
                            "play": v.get("play", 0),
                            "danmaku": v.get("danmaku", 0),
                        })

    # 去重排序取前20
    videos.sort(key=lambda x: x["play"], reverse=True)
    top20 = videos[:20]

    if not top20:
        print("所有API均未返回结果，可能需要更新B站cookie")
        return

    print(f"找到 {len(top20)} 个高热度视频:")
    for i, r in enumerate(top20, 1):
        title_clean = r["title"].replace("<em class=\"keyword\">", "").replace("</em>", "")
        print(f"  {i:2d}. {r['bvid']} | {r['play']:>8}播放 | {r['danmaku']:>5}弹幕 | {title_clean[:50]}")

    bvids = [r["bvid"] for r in top20]
    print("\nBV号列表:")
    for b in bvids:
        print(b)

    out_dir = Path("data")
    out_dir.mkdir(exist_ok=True)
    (out_dir / "lol_hot_bvids.json").write_text(
        json.dumps({"bvids": bvids, "videos": top20}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已保存到 {out_dir / 'lol_hot_bvids.json'}")


if __name__ == "__main__":
    asyncio.run(main())
