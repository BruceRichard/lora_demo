#!/bin/bash
echo "========================================"
echo "  LoL SFT 微调 - 合并权重"
echo "========================================"
echo ""

# 检查 LoRA 权重是否存在
if [ ! -d "saves/lol_qwen3b_lora" ]; then
    echo "[错误] LoRA 权重不存在，请先运行 bash train.sh"
    exit 1
fi

echo "合并 LoRA 权重到基座模型..."
echo ""

llamafactory-cli export \
    --model_name_or_path ./models/Qwen2.5-3B-Instruct \
    --adapter_name_or_path ./saves/lol_qwen3b_lora \
    --template qwen \
    --finetuning_type lora \
    --export_dir ./models/lol_qwen3b_merged \
    --export_size 2 \
    --export_legacy_format false

if [ $? -ne 0 ]; then
    echo ""
    echo "[错误] 合并失败"
    exit 1
fi

echo ""
echo "========================================"
echo "  合并完成!"
echo "  完整模型保存在: models/lol_qwen3b_merged"
echo ""
echo "  可以使用以下方式测试:"
echo "  bash test.sh"
echo "========================================"
