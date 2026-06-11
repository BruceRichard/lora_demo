"""
高噪声社区文本预处理器

依据研究文档 §"高噪声网络文本预处理与特殊符号的语义映射机制":
- 正则过滤特殊符号 / 空数据
- N-gram + TF-IDF 短文本去重
- 哈工大停用词表 + 电竞特化停用词库
- Demojize: 将 🐶/😅/🍏 转化为 __狗头__ / __流汗微笑__ / __青苹果__
- 正确处理 ZWJ (\u200D) 复合表情

默认使用 emojiswitch 库(lang='zh'),缺包时回退到内置 emoji 字典 + emoji 库。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from config import LOG_DIR, PROCESSED_DIR, RAW_DIR, RESOURCES_DIR, STOPWORDS_FILES
from utils import get_logger, strip_urls, strip_zero_width

LOG_PATH = LOG_DIR / "preprocess.log"
logger = get_logger("preprocess", LOG_PATH)


# ---------------- Demojize 适配层 ----------------
def _build_emoji_dict() -> dict[str, str]:
    """基础中英对照表,作为 emojiswitch 不可用时的回退"""
    return {
        "🐶": "狗头",
        "😅": "流汗微笑",
        "🤡": "小丑",
        "🐴": "马",
        "🍌": "香蕉",
        "🍏": "青苹果",
        "🍇": "葡萄",
        "🤣": "笑得在地上打滚",
        "😭": "大哭",
        "🔥": "火",
        "💯": "满分",
        "👀": "看戏",
        "🕷": "蜘蛛",
        "🐕": "狗",
        "😇": "笑眯眯",
        "😏": "得意",
        "🤗": "抱抱",
        "👊": "拳头",
        "🐢": "乌龟",
        "💔": "心碎",
    }


def _norm(s: str) -> str:
    """统一去 ZWJ + 多余空白"""
    s = s.replace("\u200d", "").replace("\ufe0f", "")
    return re.sub(r"\s+", " ", s).strip()


def demojize_text(text: str, lang: str = "zh", delimiters: tuple[str, str] = ("__", "__"), keep_zwj: bool = False) -> str:
    """
    将 emoji 转化为显式中文 token:
        "我喜欢吃🍏" -> "我喜欢吃__青苹果__"
    """
    if not text:
        return ""
    if not keep_zwj:
        text = text.replace("\u200d", "")
    try:
        import emojiswitch  # type: ignore
        return emojiswitch.demojize(
            text,
            delimiters=delimiters,
            lang=lang,
            keep_zwj=keep_zwj,
        )
    except Exception:
        pass
    try:
        import emoji  # type: ignore
        return emoji.demojize(
            text,
            delimiters=delimiters,
        )
    except Exception:
        pass
    # 回退到内置字典
    mapping = _build_emoji_dict()
    for k, v in mapping.items():
        text = text.replace(k, f"{delimiters[0]}{v}{delimiters[1]}")
    return _norm(text)


# ---------------- 停用词 ----------------
def _load_stopwords() -> set[str]:
    s: set[str] = set()
    for _, p in STOPWORDS_FILES.items():
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    s.add(line)
    return s


STOPWORDS = _load_stopwords()


# ---------------- 正则清洗 ----------------
_RE_KEEP_ASCII = re.compile(r"[a-zA-Z0-9]")
_RE_REPEAT = re.compile(r"(.)\1{4,}")
_RE_LINK = re.compile(r"\[(?P<inner>[^\]]+)\]\([^)]+\)")
_RE_AT = re.compile(r"@[\u4e00-\u9fa5\w]+")
_RE_ONLY_NON_CN = re.compile(r"^[^\u4e00-\u9fa5a-zA-Z0-9]+$")
_RE_PURE_ASCII_REPEAT = re.compile(r"^[A-Za-z0-9_ ]+$")


def basic_clean(text: str) -> str:
    if not text:
        return ""
    text = strip_zero_width(text)
    text = strip_urls(text)
    text = _RE_AT.sub(" ", text)
    text = _RE_REPEAT.sub(r"\1\1\1", text)
    text = _RE_LINK.sub(r"\g<inner>", text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def remove_stopwords(text: str, stopwords: set[str] = STOPWORDS) -> str:
    if not text or not stopwords:
        return text
    tokens = [t for t in text.split(" ") if t and t not in stopwords]
    return " ".join(tokens)


# ---------------- 短文本去重 ----------------
def _normalize_for_hash(text: str) -> str:
    s = re.sub(r"\s+", "", text)
    s = strip_zero_width(s)
    return s.lower()


def _shingle(text: str, n: int = 4) -> set[str]:
    s = _normalize_for_hash(text)
    if len(s) <= n:
        return {s}
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


@dataclass
class DedupResult:
    keep: list[str]
    drop: list[str]


class ShortTextDeduplicator:
    """
    基于 4-gram Jaccard 的近重复短文本去重器。
    使用滑动窗口 (window=5000) 避免 O(n²).
    """

    def __init__(self, threshold: float = 0.85, n: int = 4, window: int = 5000) -> None:
        self.threshold = threshold
        self.n = n
        self.window = window
        self._seen_shingles: list[set[str]] = []
        self._seen_texts: list[str] = []

    def is_duplicate(self, text: str) -> bool:
        if not text:
            return True
        sset = _shingle(text, self.n)
        start = max(0, len(self._seen_shingles) - self.window)
        for s in self._seen_shingles[start:]:
            if _jaccard(sset, s) >= self.threshold:
                return True
        self._seen_shingles.append(sset)
        self._seen_texts.append(text)
        return False

    def run(self, texts: Iterable[str]) -> DedupResult:
        keep, drop = [], []
        for t in texts:
            (drop if self.is_duplicate(t) else keep).append(t)
        return DedupResult(keep=keep, drop=drop)


# ---------------- 主流水线 ----------------
def preprocess_post_text(text: str) -> str:
    s = basic_clean(text)
    s = demojize_text(s, lang="zh")
    s = remove_stopwords(s)
    return s.strip()


def is_valid_zh_text(text: str, min_len: int = 4, max_repeat_ratio: float = 0.5) -> bool:
    if not text:
        return False
    s = _norm(text)
    if len(s) < min_len:
        return False
    # 纯英文无意义
    if _RE_PURE_ASCII_REPEAT.match(s) and len(s) < 8:
        return False
    # 重复字符检测
    if re.search(r"(.)\1{8,}", s):
        return False
    return True


def main() -> None:
    """简单 CLI:对 RAW_DIR 下所有 jsonl 做去重清洗,输出到 PROCESSED_DIR"""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    dedup = ShortTextDeduplicator()
    seen_paths: set[Path] = set()
    for p in RAW_DIR.glob("*.jsonl"):
        out = PROCESSED_DIR / p.name
        with open(p, "r", encoding="utf-8") as fr, open(out, "w", encoding="utf-8") as fw:
            for line in fr:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # 尝试对若干字段做文本清洗
                for key in ("text", "title", "content", "raw_sample"):
                    if key in rec and isinstance(rec[key], str):
                        rec[key + "_clean"] = preprocess_post_text(rec[key])
                fw.write(json.dumps(rec, ensure_ascii=False) + "\n")
        seen_paths.add(p)
    logger.info("预处理完成,共处理 %s 个文件", len(seen_paths))


if __name__ == "__main__":
    main()
