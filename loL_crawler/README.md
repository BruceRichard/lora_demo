# LoL 观赛模型 — 数据爬取与 SFT 构造管线

参考论文《针对英雄联盟电竞观赛领域的大语言模型微调数据集构建与工程实践研究》实现。本工程把该研究方案从论文落到代码,完成 **贴吧 / NGA / B 站弹幕** 的异步高并发采集、**Data Dragon** 静态知识库下载、**高噪声社区文本预处理(含 Demojize + ZWJ 处理)**,以及 **SFT 训练样本(系统提示+指令+输出三元组)** 的自动构造。

## 目录结构

```
loL_crawler/
├── main.py                       # 主管线(orchestrator)
├── config.py                     # 路径 / 贴吧 / NGA / B 站配置
├── utils.py                      # 日志 / JSONL / 限流 / 断点续爬
├── spiders/
│   ├── tieba_crawler.py          # aiotieba 异步三级拓扑爬虫
│   ├── nga_crawler.py            # NGA read.php?lite=js
│   ├── bili_crawler.py           # 弹幕 proto / xml
│   └── datadragon_downloader.py  # Riot 静态数据
├── processors/
│   └── preprocess.py             # Demojize / 停用词 / 短文去重
├── builders/
│   └── sft_builder.py            # (System, Instruction, Output) 构造
├── resources/
│   ├── meme_dict.json            # 梗文化词典
│   ├── stopwords_general.txt
│   └── stopwords_esports.txt
└── data/
    ├── raw/                      # 原始抓取
    ├── processed/                # 清洗后
    ├── sft/                      # SFT 训练样本
    ├── datadragon/               # 英雄 / 装备 JSON
    └── logs/
```

## 1. 安装

```bash
cd loL_crawler
pip install -r requirements.txt
```

> 真实抓取需要登录凭证(BDUSS / NGA Cookie / B 站 Cookie)。无凭证可用 `--mock` 生成样例数据联调整条管线。

## 2. 全流程运行

```bash
# 全 mock,无任何凭证即可跑通
python main.py all --mock

# 真实抓取(提供 cookie)
python main.py all \
  --bduss "your_baidu_bduss" \
  --nga-cookie "ngaPassportCid=...; ngaPassportUid=...; ngacn=...; _ga=..." \
  --bvid BV1xxxx1 \
  --bvid BV1xxxx2 \
  --fbar kangya --fbar rng --fbar ig \
  --nga-fid -7 --nga-fid -1019717 \
  --pages 5
```

子命令:
- `python main.py tieba --mock` 只跑贴吧
- `python main.py nga --mock` 只跑 NGA
- `python main.py bili --mock` 只跑 B 站弹幕
- `python main.py datadragon --version 14.1.1` 下载特定版本英雄数据
- `python main.py preprocess` 跑清洗
- `python main.py build-sft` 构造 SFT(可在没有抓取数据时仅生成梗百科/角色扮演/通用占位)

## 3. SFT 样本字段

`data/sft/*.jsonl` 每行一条训练样本:

```json
{
  "system": "你是一个精通 LPL 历史与抗压背锅吧抽象文化的资深观众...",
  "instruction": "能跟我详细科普一下电竞圈里一直刷的'舒服了'这个梗到底是怎么来的吗?",
  "output": "这可是 LPL 史上的经典圣经... 王校长在 RNG 官博下留了一句'舒服了'...",
  "source": "meme_dict | tieba_post | tieba_comments | bili_danmaku | nga_post | datadragon_lore | datadragon_skills | datadragon_stats | anchor_placeholder",
  "tags": ["meme_origin", "knowledge", "multi_turn", "role_play", "abstract", ...]
}
```

合并后: `data/sft/sft_mix.jsonl` 可以直接接入 LoRA / SFT 训练脚本(参考 `download.py`)。

## 4. 与论文章节的对应

| 论文章节 | 代码入口 |
| :-- | :-- |
| §通用基础指令数据集(COIG-CQIA/BELLE/HC3/InstructWild/COIG-P) | `builders/sft_builder.build_anchor_samples` 占位;需用户自行 merge |
| §静态知识本体(英雄/装备/小兵) | `spiders/datadragon_downloader.py` + `builders/sft_builder.build_datadragon_samples` |
| §异步协程高并发爬虫(抗压背锅吧) | `spiders/tieba_crawler.py` 内的 `TiebaCrawler`(`get_threads/get_posts/get_comments` 三级拓扑) |
| §NGA / B 站横向补充 | `spiders/nga_crawler.py`、`spiders/bili_crawler.py` |
| §Demojize + ZWJ 处理 | `processors/preprocess.py:demojize_text` |
| §N-gram / TF-IDF 短文去重 | `processors/preprocess.py:ShortTextDeduplicator` |
| §梗文化词典与历史解析 | `resources/meme_dict.json` + `builders/sft_builder.build_meme_dict_samples` |
| §动态角色扮演(阵营打标) | `builders/sft_builder.build_meme_dict_samples` 中 `team_aliases` 段落 |
| §PEFT/LoRA 训练 | **不在本仓库**,需要消费 `data/sft/sft_mix.jsonl` 自行搭建(参见 `../download.py`) |

## 5. 注意事项

- **aiotieba** 的 `get_posts` 与 `get_comments` 是异步原生方法;`process_thread` 中使用 `asyncio.Semaphore` 控制并发,可通过 `concurrency` 参数调节。
- **B 站新版历史弹幕** 是 proto 格式。如已生成 `DmSegMobileReply` proto,放到 `danmaku_proto/` 即可启用;否则自动降级为旧版 XML (`/x/v1/dm/list.so`)。
- **NGA** 反爬严格,务必限速且使用真实登录 Cookie;`read.php?lite=js` 返回的 JSON 顶层通常是 `window.script_muti_get_var_value([...])` 的内嵌列表,本仓库用正则 `\[\s*\{.*\}\s*\]` 抽取。
- **Demojize** 默认使用 `emojiswitch`(`lang="zh"`),缺包时回退到 `emoji` 库;再退到内置字典。
- **停用词** 简化为精简版,实际生产可替换为哈工大完整停用词表。

## 6. 后续训练指引(LoRA)

合并后的 `data/sft/sft_mix.jsonl` 大致使用方式(伪代码,具体参考 `../download.py`):

```python
from datasets import load_dataset, Dataset
ds = load_dataset("json", data_files="data/sft/sft_mix.jsonl")["train"]
# 转 chat template -> Alpaca / ChatML
ds = ds.map(format_for_lora)  # 自定义
# LoRA(参考 论文 §PEFT 章节)
```

到此整个项目即可一站式产出:
**多源数据 → 清洗去噪 → Demojize → 多类 SFT 样本 → 合并 jsonl → 进入 LoRA 训练。**
