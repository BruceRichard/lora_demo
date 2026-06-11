#!/bin/bash
echo "========================================"
echo "  LoL SFT 微调 - 开始训练"
echo "========================================"
echo ""
echo "模型: Qwen2.5-3B-Instruct (QLoRA 4-bit)"
echo "数据: lol_sft (ShareGPT 格式)"
echo "显存: RTX 3070 8GB"
echo ""

# 检查模型是否存在
if [ ! -d "models/Qwen2.5-3B-Instruct" ]; then
    echo "[错误] 模型不存在，请先运行 bash prepare.sh 下载模型"
    exit 1
fi

# 检查数据是否存在
if [ ! -f "data/lol_sft.json" ]; then
    echo "[错误] 训练数据不存在，请先运行 bash prepare_data.sh"
    exit 1
fi

echo "[开始训练...]"
echo ""

llamafactory-cli train train_config.yaml

if [ $? -ne 0 ]; then
    echo ""
    echo "[错误] 训练失败，请检查日志"
    exit 1
fi

echo ""
echo "========================================"
echo "  训练完成!"
echo "  LoRA 权重保存在: saves/lol_qwen3b_lora"
echo ""
echo "  下一步: bash merge.sh 合并权重"
echo "========================================"
