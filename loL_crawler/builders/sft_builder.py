"""
SFT 数据集构造器

依据研究文档:
- §通用基础数据集的底层逻辑(指令+回答双轨,合并 COIG-CQIA/BELLE/HC3/InstructWild/COIG-P)
- §静态知识本体结构化抽取:Data Dragon -> 自然语言问答对
- §高噪声社区数据 -> 多轮辩论对话样本(楼中楼)
- §核心梗文化解析: 构造 (System, Instruction, Output) 三元组
- §动态角色扮演(阵营打标)

输出:JSONL 训练样本, 字段:
{
  "system": "...",
  "instruction": "...",
  "input": "...",  # 可选
  "output": "...",
  "source": "tieba|nga|bili|datadragon|meme_dict|mixed",
  "tags": ["abstract", "multi_turn", "role_play", "knowledge"]
}
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from config import (
    DATADRAGON_DIR, LOG_DIR, PROCESSED_DIR, RESOURCES_DIR, SFT_DIR,
)
from processors.preprocess import (
    demojize_text, is_valid_zh_text, preprocess_post_text,
)
from utils import ProgressTracker, get_logger, read_jsonl, truncate_bytes, write_jsonl

LOG_PATH = LOG_DIR / "sft_builder.log"
logger = get_logger("sft_builder", LOG_PATH)


DEFAULT_SYSTEM_PROMPT = (
    "你是一个精通 LPL 历史与抗压背锅吧抽象文化的资深英雄联盟观众, "
    "请以幽默、戏谑、客观的口吻解答用户的疑问。"
    "可以使用网络社区常用的表情符号与阴阳怪气表达(如狗头🐶、流汗黄豆😅), "
    "但不要输出违规、辱骂、煽动性言论。"
)


# ---------------- 1. 贴吧 -> 多轮对话 / 主题-回复问答 ----------------
def _extract_text(rec: dict) -> Optional[str]:
    """统一从贴吧/弹幕 record 中抽取文本字段"""
    for key in ("text", "title", "content", "raw_sample", "name"):
        v = rec.get(key)
        if isinstance(v, str) and v.strip():
            return v
    contents = rec.get("contents")
    if isinstance(contents, list):
        segs = []
        for c in contents:
            if isinstance(c, dict):
                t = c.get("text")
                if t:
                    segs.append(t)
            elif isinstance(c, str):
                segs.append(c)
        if segs:
            return "".join(segs)
    return None


def _thread_text(thread: dict) -> Optional[str]:
    """从 thread 的 contents 字段提取文本"""
    contents = thread.get("contents")
    if not isinstance(contents, list):
        return None
    segs = []
    for c in contents:
        if isinstance(c, dict):
            t = c.get("text")
            if isinstance(t, str) and t.strip():
                segs.append(t.strip())
        elif isinstance(c, str):
            segs.append(c.strip())
    return " ".join(segs) if segs else None


def build_tieba_samples(raw_dir: Path) -> Iterable[dict]:
    """
    主题帖 -> instruction; 楼层(post) -> output
    楼中楼(comment) -> 拼接为多轮 output
    无楼层数据时降级用主题帖正文 contents.text 作为 output
    """
    seen_tids: set[int] = set()

    for fp in sorted(raw_dir.glob("*_tid_*.jsonl")):
        for rec in read_jsonl(fp):
            thread = rec.get("thread") or {}
            posts = rec.get("posts") or []
            comments = rec.get("comments") or []
            tid = thread.get("tid")
            if tid:
                seen_tids.add(tid)
            title = thread.get("title") or _extract_text(thread)
            if not title:
                continue
            instruction = demojize_text(title, lang="zh").strip()
            if not is_valid_zh_text(instruction):
                continue
            instruction = truncate_bytes(instruction, 600)

            if posts or comments:
                # 1) 主楼层 -> 单轮回答
                for p in posts[:5]:
                    ptext = (
                        p.get("text")
                        or _extract_text(p)
                        or ""
                    )
                    ptext = demojize_text(ptext, lang="zh").strip()
                    if not is_valid_zh_text(ptext, min_len=6):
                        continue
                    yield {
                        "system": DEFAULT_SYSTEM_PROMPT,
                        "instruction": instruction,
                        "output": truncate_bytes(ptext, 1200),
                        "source": "tieba_post",
                        "tags": ["forum_reply"],
                    }
                # 2) 楼中楼拼接为多轮辩论
                if comments:
                    grouped: dict[Optional[int], list[dict]] = {}
                    for c in comments:
                        pid = c.get("pid")
                        grouped.setdefault(pid, []).append(c)
                    for pid, group in grouped.items():
                        if len(group) < 2:
                            continue
                        dialogue = []
                        for c in group[:6]:
                            speaker = c.get("user_name") or "网友"
                            text = (c.get("text") or "").strip()
                            if not text:
                                continue
                            text = demojize_text(text, lang="zh")
                            dialogue.append(f"{speaker}: {text}")
                        if len(dialogue) < 2:
                            continue
                        yield {
                            "system": DEFAULT_SYSTEM_PROMPT,
                            "instruction": instruction,
                            "output": "\n".join(truncate_bytes(d, 600) for d in dialogue),
                            "source": "tieba_comments",
                            "tags": ["multi_turn", "debate"],
                        }
            else:
                # 3) 无楼层数据时 -> 用主题帖正文作为 output
                body = _thread_text(thread)
                if not body:
                    continue
                body = demojize_text(body, lang="zh").strip()
                if not is_valid_zh_text(body, min_len=6):
                    continue
                yield {
                    "system": DEFAULT_SYSTEM_PROMPT,
                    "instruction": instruction,
                    "output": truncate_bytes(body, 1200),
                    "source": "tieba_thread",
                    "tags": ["forum_post"],
                }

    # 4) 补充读取 *_threads.jsonl 中还没有 tid 文件的主题
    for fp in sorted(raw_dir.glob("*_threads.jsonl")):
        for rec in read_jsonl(fp):
            tid = rec.get("tid")
            if tid and tid in seen_tids:
                continue
            title = rec.get("title") or _extract_text(rec)
            if not title:
                continue
            instruction = demojize_text(title, lang="zh").strip()
            if not is_valid_zh_text(instruction):
                continue
            instruction = truncate_bytes(instruction, 600)
            body = _thread_text(rec)
            if not body:
                continue
            body = demojize_text(body, lang="zh").strip()
            if not is_valid_zh_text(body, min_len=6):
                continue
            yield {
                "system": DEFAULT_SYSTEM_PROMPT,
                "instruction": instruction,
                "output": truncate_bytes(body, 1200),
                "source": "tieba_thread",
                "tags": ["forum_post"],
            }


# ---------------- 2. 弹幕 -> 高光时刻 / 实时观赛 ----------------
def build_bili_samples(raw_dir: Path) -> Iterable[dict]:
    """
    将同一视频的弹幕按时间窗口(默认 30s)聚合成节选样本:
    - 抽取每段高频词作 Output
    - instruction 可注入'今天看 X 比赛时弹幕高潮时刻'
    """
    for fp in sorted(raw_dir.glob("bili_*.jsonl")):
        records = list(read_jsonl(fp))
        if not records:
            continue
        meta = next((r for r in records if r.get("_meta")), {})
        title = meta.get("title", fp.stem)
        danmaku = [r for r in records if not r.get("_meta")]
        # 时间窗口聚合
        window = 30.0
        buckets: dict[int, list[str]] = {}
        for d in danmaku:
            ts = d.get("timestamp", 0.0)
            text = d.get("text", "")
            if not text:
                continue
            text = demojize_text(text, lang="zh")
            key = int(ts // window)
            buckets.setdefault(key, []).append(text)
        for key, items in buckets.items():
            if len(items) < 3:
                continue
            counter = Counter(items)
            # 取出现频次 >=1 的高频弹幕 (放宽条件)
            top = [t for t, c in counter.most_common(12) if c >= 1]
            if not top:
                continue
            ts_start = key * int(window)
            ts_end = ts_start + int(window)
            instruction = (
                f"在 Bilibili 视频《{title}》{ts_start}-{ts_end} 秒时段, "
                f"观众弹幕普遍在刷什么梗?"
            )
            yield {
                "system": DEFAULT_SYSTEM_PROMPT,
                "instruction": instruction,
                "output": "、".join(top) + f" (本段采集到 {len(items)} 条弹幕)",
                "source": "bili_danmaku",
                "tags": ["realtime_emotion", "meme_density"],
            }


# ---------------- 3. NGA -> 战术复盘 + 长篇观点 ----------------
def _parse_nga_raw_sample(raw_sample: str) -> str:
    """尝试从 NGA 原始 API 响应中提取帖子正文"""
    m = re.search(r"window\.script_muti_get_var_store\s*=\s*(\{.*\})\s*;?\s*$", raw_sample, re.DOTALL)
    if not m:
        return raw_sample
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        # 可能被截断,尝试在 }); 处截断
        truncated = re.sub(r",\s*[^}]*$", "}", m.group(1))
        try:
            data = json.loads(truncated)
        except json.JSONDecodeError:
            return raw_sample
    # 帖子正文可能在 __R (回复) 或 __U (用户内容) 中
    for key in ("__R", "__U"):
        container = data.get("data", {}).get(key) or data.get(key)
        if not isinstance(container, dict):
            continue
        texts = []
        for k in sorted(container.keys(), key=int):
            entry = container[k]
            if not isinstance(entry, dict):
                continue
            content = entry.get("content") or []
            if isinstance(content, list):
                for seg in content:
                    if isinstance(seg, str):
                        texts.append(seg)
                    elif isinstance(seg, dict):
                        t = seg.get("text") or seg.get("content", "")
                        if isinstance(t, str):
                            texts.append(t)
            elif isinstance(content, str):
                texts.append(content)
        if texts:
            return "".join(texts)
    return raw_sample


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return cjk / len(text)


def build_nga_samples(raw_dir: Path) -> Iterable[dict]:
    for fp in sorted(raw_dir.glob("nga_*_posts.jsonl")):
        for rec in read_jsonl(fp):
            raw = rec.get("raw_sample", "")
            if not raw:
                continue
            # 优先使用新爬虫保存的 content 字段
            text = rec.get("content", "")
            if not text or _cjk_ratio(text) < 0.3:
                text = _parse_nga_raw_sample(raw)
            text = re.sub(r"<[^>]+>", "", text)
            text = demojize_text(text, lang="zh")
            text = re.sub(r"\s+", " ", text).strip()
            if not is_valid_zh_text(text, min_len=20):
                continue
            if _cjk_ratio(text) < 0.3:
                continue
            title_guess = re.sub(r"\W+", " ", raw[:60]).strip()[:40]
            yield {
                "system": DEFAULT_SYSTEM_PROMPT,
                "instruction": f"请对 NGA 战术长文片段做摘要与解读: {title_guess}",
                "output": truncate_bytes(text, 2000),
                "source": "nga_post",
                "tags": ["long_text", "tactical_review"],
            }


# ---------------- 4. Data Dragon -> 静态知识问答 ----------------
def _datadragon_champions(version_dir: Path) -> Iterable[dict]:
    p = version_dir / "champion.json"
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    for champ in (data.get("data") or {}).values():
        yield champ


def build_datadragon_samples(dragon_root: Path) -> Iterable[dict]:
    for version_dir in sorted(dragon_root.iterdir()):
        if not version_dir.is_dir():
            continue
        for champ in _datadragon_champions(version_dir):
            name = champ.get("name", "")
            title = champ.get("title", "")
            tags = champ.get("tags", [])
            stats = champ.get("stats", {})
            lore = (champ.get("lore") or "")[:400]
            skills = []
            for s in (champ.get("passive") and [champ["passive"]] or []):
                skills.append(s.get("name", ""))
            for s in champ.get("spells", []) or []:
                skills.append(s.get("name", ""))
            # 1) 介绍问答
            yield {
                "system": DEFAULT_SYSTEM_PROMPT,
                "instruction": f"介绍一下英雄联盟英雄 {name} 的定位与背景。",
                "output": (
                    f"{name} ({title}), 定位: {', '.join(tags) or '未公布'}。"
                    f"{lore}"
                ),
                "source": "datadragon_lore",
                "tags": ["knowledge", "champion"],
            }
            # 2) 技能问答
            if skills:
                yield {
                    "system": DEFAULT_SYSTEM_PROMPT,
                    "instruction": f"{name} 有哪些核心技能?",
                    "output": "技能列表: " + " / ".join(s for s in skills if s),
                    "source": "datadragon_skills",
                    "tags": ["knowledge", "champion_skills"],
                }
            # 3) 属性问答
            if stats:
                yield {
                    "system": DEFAULT_SYSTEM_PROMPT,
                    "instruction": f"{name} 的基础属性如何?",
                    "output": "基础属性: " + ", ".join(
                        f"{k}={v}" for k, v in stats.items() if isinstance(v, (int, float))
                    ),
                    "source": "datadragon_stats",
                    "tags": ["knowledge", "champion_stats"],
                }


# ---------------- 5. 梗百科 SFT ----------------
def build_meme_dict_samples(meme_dict_path: Path) -> Iterable[dict]:
    if not meme_dict_path.exists():
        return
    dict_data = json.loads(meme_dict_path.read_text(encoding="utf-8"))
    for meme in dict_data.get("memes", []):
        # 1) 直接科普型
        instruction = f"能跟我详细科普一下电竞圈里一直刷的'{meme['name']}'这个梗到底是怎么来的吗?"
        output = meme["explanation"]
        yield {
            "system": DEFAULT_SYSTEM_PROMPT,
            "instruction": instruction,
            "output": output,
            "source": "meme_dict",
            "tags": ["meme_origin", "knowledge"],
        }
        # 2) 触发场景型
        for trig in meme.get("trigger", [])[:3]:
            yield {
                "system": DEFAULT_SYSTEM_PROMPT,
                "instruction": f"今天比赛出现了 '{trig}' 这种情况, 贴吧老哥们会刷什么梗?",
                "output": (
                    f"老哥们大概率会齐刷 '{meme['name']}' (id={meme['id']})。"
                    f"梗的渊源: {meme.get('event', '')}。"
                    f"语气色彩: {meme.get('tone', 'meme')}。"
                ),
                "source": "meme_dict_trigger",
                "tags": ["meme_application", "meme_origin"],
            }
    # 6) 阵营角色扮演
    for team, aliases in dict_data.get("team_aliases", {}).items():
        yield {
            "system": (
                f"你现在是一个极度偏激的 {team} 战队粉丝, "
                "请以社区常见语气回应所有赛事相关问题。"
            ),
            "instruction": f"你怎么评价今天 {team} 的表现?",
            "output": (
                f"我 {team} 老粉直接放话: 这场比赛我们就一个字, 稳!"
                f"(常见昵称: {', '.join(aliases[:3])})。"
                "当然要是输了, 我也准备好下一波'舒服了'的素材了。"
            ),
            "source": "meme_dict_role_play",
            "tags": ["role_play", "fan_voice"],
        }
    # 7) 选手角色
    for player, aliases in dict_data.get("player_aliases", {}).items():
        yield {
            "system": (
                f"你扮演的是 {player} 的资深粉丝/解说, 熟知其职业生涯梗与口头禅, "
                "输出需符合社区风格。"
            ),
            "instruction": f"今天 {player} 又在比赛中天秀了一波, 你怎么看?",
            "output": (
                f"我只能说 {player}({'/'.join(aliases[:3])}) yyds 永远滴神!"
                "这种级别操作也就他能打出来, 其他选手真比不了。"
            ),
            "source": "meme_dict_role_play",
            "tags": ["role_play", "player_fan"],
        }


# ---------------- 6. 通用基础数据集混编占位 ----------------
def build_anchor_samples() -> Iterable[dict]:
    """
    占位: 提示用户需要把 COIG-CQIA / BELLE / HC3-Chinese 合并进来。
    这些数据集会作为通用底座, 防止灾难性遗忘。
    """
    yield {
        "system": "你是一个乐于助人的中文助手。",
        "instruction": "(通用指令样本占位) 介绍一下你自己。",
        "output": "我是一个基于 LoRA 微调的中文对话助手, 擅长英雄联盟电竞观赛场景。",
        "source": "anchor_placeholder",
        "tags": ["anchor", "needs_external_merge"],
    }


# ---------------- 7. 主入口 ----------------
class SFTBuilder:
    def __init__(
        self,
        raw_dir: Path,
        processed_dir: Path,
        sft_dir: Path,
        meme_dict: Path,
        datadragon_dir: Path,
        max_samples_per_file: int = 0,
    ) -> None:
        self.raw_dir = raw_dir
        self.processed_dir = processed_dir
        self.sft_dir = sft_dir
        self.meme_dict = meme_dict
        self.datadragon_dir = datadragon_dir
        self.max_samples_per_file = max_samples_per_file
        self.sft_dir.mkdir(parents=True, exist_ok=True)

    def _save(self, name: str, samples: Iterable[dict]) -> int:
        out = self.sft_dir / f"{name}.jsonl"
        n = 0
        with open(out, "w", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
                n += 1
                if self.max_samples_per_file and n >= self.max_samples_per_file:
                    break
        logger.info("生成 %s -> %s 条 -> %s", name, n, out)
        return n

    def run(self) -> dict[str, int]:
        counts: dict[str, int] = {}

        # 优先使用清洗后数据, 否则用原始数据
        tieba_dir = self.processed_dir if any(self.processed_dir.glob("*_tid_*.jsonl")) else self.raw_dir
        bili_dir = self.processed_dir if any(self.processed_dir.glob("bili_*.jsonl")) else self.raw_dir
        nga_dir = self.processed_dir if any(self.processed_dir.glob("nga_*_posts.jsonl")) else self.raw_dir

        counts["tieba"] = self._save("tieba_sft", build_tieba_samples(tieba_dir))
        counts["bili"] = self._save("bili_sft", build_bili_samples(bili_dir))
        counts["nga"] = self._save("nga_sft", build_nga_samples(nga_dir))
        counts["datadragon"] = self._save("datadragon_sft", build_datadragon_samples(self.datadragon_dir))
        counts["meme"] = self._save("meme_sft", build_meme_dict_samples(self.meme_dict))
        counts["anchor"] = self._save("anchor_sft", build_anchor_samples())

        # 合并统一 sft_mix
        mixed_path = self.sft_dir / "sft_mix.jsonl"
        with open(mixed_path, "w", encoding="utf-8") as f:
            for fn in (
                "tieba_sft.jsonl", "bili_sft.jsonl", "nga_sft.jsonl",
                "datadragon_sft.jsonl", "meme_sft.jsonl", "anchor_sft.jsonl",
            ):
                p = self.sft_dir / fn
                if not p.exists():
                    continue
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        f.write(line + "\n")
        logger.info("合并 sft_mix.jsonl -> %s 行", sum(1 for _ in open(mixed_path, "r", encoding="utf-8")))
        return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="SFT 数据集构造器")
    parser.add_argument("--max", type=int, default=0, help="每个文件最大样本数(0=无限制)")
    args = parser.parse_args()
    builder = SFTBuilder(
        raw_dir=Path("data/raw"),
        processed_dir=Path("data/processed"),
        sft_dir=Path("data/sft"),
        meme_dict=Path("resources/meme_dict.json"),
        datadragon_dir=Path("data/datadragon"),
        max_samples_per_file=args.max,
    )
    counts = builder.run()
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
