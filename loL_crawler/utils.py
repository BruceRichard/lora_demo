"""
通用工具:日志、限流、断点续爬、JSONL IO
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import aiofiles


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"


def get_logger(name: str, log_file: Optional[Path] = None, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter(_LOG_FORMAT)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    logger.propagate = False
    return logger


# ---------------- JSONL 读写 ----------------
def read_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return iter(())
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def write_jsonl(path: Path, rows: Iterable[dict], append: bool = True) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    n = 0
    with open(path, mode, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


# ---------------- 限流器 ----------------
class AsyncRateLimiter:
    """轻量令牌桶,用于异步协程间的速率控制"""

    def __init__(self, rate_per_sec: float, burst: Optional[int] = None) -> None:
        self.interval = 1.0 / max(rate_per_sec, 0.01)
        self.burst = burst or max(int(rate_per_sec * 2), 1)
        self._last = 0.0
        self._tokens = self.burst

    def reset(self) -> None:
        self._last = time.monotonic()
        self._tokens = self.burst


def jitter_sleep(base: float, jitter: float = 0.3) -> None:
    """同步随机抖动"""
    time.sleep(base + random.uniform(0, jitter * base))


async def async_jitter_sleep(base: float, jitter: float = 0.3) -> None:
    await _asleep(base + random.uniform(0, jitter * base))


async def _asleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


# ---------------- 断点续爬 ----------------
class ProgressTracker:
    """记录已处理的 id,支持中断后跳过已采集项"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        if self.path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._seen.add(line.strip())

    def __contains__(self, item: str) -> bool:
        return item in self._seen

    def add(self, item: str) -> None:
        if item in self._seen:
            return
        self._seen.add(item)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(item + "\n")

    def discard(self, item: str) -> None:
        self._seen.discard(item)
        if not self.path.exists():
            return
        kept = [s for s in self._seen if s]
        self.path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")

    def __len__(self) -> int:
        return len(self._seen)


# ---------------- 文本清洗 ----------------
_URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fa5]+", re.IGNORECASE)
_WS_RE = re.compile(r"[\u200B-\u200D\uFEFF]")


def strip_urls(text: str) -> str:
    return _URL_RE.sub(" ", text)


def strip_zero_width(text: str) -> str:
    return _WS_RE.sub("", text)


def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", name).strip()
    if len(name) > max_len:
        name = name[:max_len]
    return name or "unnamed"


def truncate_bytes(s: str, limit: int = 6000) -> str:
    """防御性截断,避免超长文本污染 LLM 输入"""
    if len(s) <= limit:
        return s
    return s[:limit] + "...[TRUNC]"
