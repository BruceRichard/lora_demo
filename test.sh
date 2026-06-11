#!/bin/bash
echo "========================================"
echo "  LoL SFT 微调 - 模型测试"
echo "========================================"
echo ""

# 检查合并模型是否存在
if [ -d "models/lol_qwen3b_merged" ]; then
    MODEL_PATH="./models/lol_qwen3b_merged"
    echo "使用合并后的模型"
elif [ -d "saves/lol_qwen3b_lora" ]; then
    MODEL_PATH="./models/Qwen2.5-3B-Instruct"
    ADAPTER_PATH="./saves/lol_qwen3b_lora"
    echo "使用 LoRA 适配器模式"
else
    echo "[错误] 未找到训练好的模型，请先运行 bash train.sh 和 bash merge.sh"
    exit 1
fi

echo ""
echo "启动对话测试..."
echo "输入 'exit' 或按 Ctrl+C 退出"
echo ""

if [ -n "$ADAPTER_PATH" ]; then
    llamafactory-cli chat --model_name_or_path "$MODEL_PATH" --adapter_name_or_path "$ADAPTER_PATH" --template qwen
else
    llamafactory-cli chat --model_name_or_path "$MODEL_PATH" --template qwen
fi
