"""
loL_crawler 配置文件
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
SFT_DIR = DATA_DIR / "sft"
DATADRAGON_DIR = DATA_DIR / "datadragon"
LOG_DIR = PROJECT_ROOT / "logs"
RESOURCES_DIR = PROJECT_ROOT / "resources"

for d in (RAW_DIR, PROCESSED_DIR, SFT_DIR, DATADRAGON_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)


# 目标贴吧列表
TARGET_TIEBAS = {
    "lol": "英雄联盟吧",
    "rng": "rng吧",
    "ig": "ig吧",
    "tes": "tes吧",
    "blg": "blg吧",
    "t1": "t1吧",
    "geng": "geng吧",
    "lpl": "lpl吧"
}

# 排序方式
TIEBA_SORT_NEW = 5
TIEBA_SORT_HOT = 1

# 抓取规模
TIEBA_PN_MAX_THREADS = 30
TIEBA_PN_MAX_POSTS = 50
TIEBA_PN_MAX_COMMENTS = 10
TIEBA_RN_PER_PAGE = 30
TIEBA_CONCURRENCY = 3


# NGA 板块(LOL 相关)
NGA_BOARDS = {
    "-152678": "英雄联盟",
    "479": "联盟赛事"
}
NGA_RAW_URL = "https://bbs.nga.cn/read.php"
NGA_REFERER = "https://bbs.nga.cn/"
NGA_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
NGA_REQUEST_DELAY = 1.2  # 每次请求前等待秒数


# Bilibili 直播间/视频相关
BILI_API_VIDEO_INFO = "https://api.bilibili.com/x/web-interface/view"
BILI_API_DANMAKU_XML = "https://api.bilibili.com/x/v1/dm/list.so"  # 旧版,返回 XML
BILI_API_DANMAKU_SEG = "https://api.bilibili.com/x/v2/dm/web/seg.so"  # 历史弹幕 proto
BILI_API_LIVEROOM = "https://api.live.bilibili.com/room/v1/Room/get_info"
BILI_API_LIVE_DANMAKU = "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory"
BILI_HEADERS = {
    "User-Agent": NGA_USER_AGENT,
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}
BILI_REQUEST_DELAY = 3.0  # 每次请求前等待秒数(含抖动), 降低 412


# Data Dragon
DATADRAGON_BASE = "https://ddragon.leagueoflegends.com"
DATADRAGON_VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"


# 停用词表(社区 + 通用)
STOPWORDS_FILES = {
    "general": RESOURCES_DIR / "stopwords_general.txt",
    "esports": RESOURCES_DIR / "stopwords_esports.txt",
}
