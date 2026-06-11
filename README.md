# LoL 电竞观赛模型微调

基于 Qwen2.5-3B-Instruct + QLoRA 的英雄联盟电竞观赛助手微调项目。

## 硬件要求

- GPU: RTX 3070 8GB (或同等显存)
- Python: 3.10+
- CUDA: 11.8+

## 快速开始

### 1. 准备环境

```bash
bash prepare.sh
```

这会安装依赖并下载 Qwen2.5-3B-Instruct 模型 (~6GB)。

### 2. 准备训练数据

```bash
bash prepare_data.sh
```

这会将 `loL_crawler/data/sft/sft_mix.jsonl` 转换为 LLaMA-Factory 格式。

### 3. 开始训练

```bash
bash train.sh
```

训练参数:
- 模型: Qwen2.5-3B-Instruct
- 量化: QLoRA 4-bit
- LoRA rank: 16
- Batch size: 2 (梯度累积 4)
- Epochs: 3
- 显存占用: ~6-7GB

### 4. 合并权重

```bash
bash merge.sh
```

将 LoRA 适配器合并到基座模型。

### 5. 测试模型

```bash
bash test.sh
```

## 训练数据

数据来源:
| 来源 | 数量 | 说明 |
|------|------|------|
| 贴吧 | 2683 | 电竞讨论帖 |
| B站弹幕 | 1496 | 比赛弹幕聚合 |
| NGA | 339 | 战术长文 |
| DataDragon | 344 | 英雄/装备知识 |
| Meme | 70 | 电竞梗 |
| **总计** | **4933** | |

## 文件结构

```
loraSFT/
├── prepare.bat          # 环境准备
├── prepare_data.bat     # 数据准备
├── train.bat            # 开始训练
├── merge.bat            # 合并权重
├── test.bat             # 测试模型
├── train_config.yaml    # 训练配置
├── convert_data.py      # 数据转换脚本
├── data/
│   ├── dataset_info.json
│   ├── lol_sft.json
│   └── lol_sft_alpaca.json
├── models/
│   ├── Qwen2.5-3B-Instruct/    # 基座模型
│   └── lol_qwen3b_merged/      # 合并后模型
├── saves/
│   └── lol_qwen3b_lora/        # LoRA 权重
└── loL_crawler/                # 数据爬取
```

## 自定义配置

编辑 `train_config.yaml` 可调整:
- `num_train_epochs`: 训练轮数
- `learning_rate`: 学习率
- `lora_rank`: LoRA 秩 (越大越强但越慢)
- `cutoff_len`: 最大序列长度

## 常见问题

**Q: 显存不足怎么办?**
- 降低 `per_device_train_batch_size` 到 1
- 降低 `lora_rank` 到 8
- 降低 `cutoff_len` 到 512

**Q: 训练太慢?**
- 安装 Flash Attention: `pip install flash-attn --no-build-isolation`
- 减少 `num_train_epochs` 到 2

**Q: 模型效果不好?**
- 增加训练数据 (重新爬取更多帖子)
- 增加 `num_train_epochs` 到 5
- 尝试更大的基座模型 (需要更大显存)
