#!/bin/bash
echo "========================================"
echo "  LoL SFT 微调 - 环境准备"
echo "========================================"
echo ""

echo "[1/4] 安装 LLaMA-Factory ..."
pip install llamafactory -q

echo "[2/4] 安装量化依赖 ..."
pip install bitsandbytes accelerate -q

echo "[3/4] 安装 Flash Attention (可选) ..."
pip install flash-attn --no-build-isolation -q 2>/dev/null || echo "[提示] Flash Attention 安装失败，将使用标准注意力"

echo "[4/4] 下载 Qwen2.5-3B-Instruct 模型 (~6GB) ..."
pip install modelscope -q
python -c "from modelscope import snapshot_download; snapshot_download('Qwen/Qwen2.5-3B-Instruct', local_dir='./models/Qwen2.5-3B-Instruct')"

echo ""
echo "========================================"
echo "  准备完成! 下一步: bash prepare_data.sh"
echo "========================================"
