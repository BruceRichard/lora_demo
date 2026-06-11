#!/bin/bash
echo "========================================"
echo "  LoL SFT 微调 - 数据准备"
echo "========================================"
echo ""

echo "[1/2] 转换数据格式 ..."
python convert_data.py

if [ $? -ne 0 ]; then
    echo "[错误] 数据转换失败"
    exit 1
fi

echo ""
echo "[2/2] 注册数据集到 LLaMA-Factory ..."

# 找到 LLaMA-Factory 的数据目录
LLAMA_PATH=$(python -c "import llamafactory; import os; print(os.path.dirname(llamafactory.__file__).replace(chr(92), '/'))")
LLAMA_DATA="$LLAMA_PATH/data"

if [ ! -d "$LLAMA_DATA" ]; then
    echo "[错误] LLaMA-Factory 数据目录不存在: $LLAMA_DATA"
    exit 1
fi

# 合并数据集配置 (文件不存在则创建)
python -c "
import json
from pathlib import Path

llama_data = Path(r'$LLAMA_DATA')
local_info = json.loads(Path('data/dataset_info.json').read_text(encoding='utf-8'))

existing = {}
if (llama_data / 'dataset_info.json').exists():
    existing = json.loads((llama_data / 'dataset_info.json').read_text(encoding='utf-8'))

existing.update(local_info)
(llama_data / 'dataset_info.json').write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'已注册 {len(local_info)} 个数据集到 {llama_data}')
"

# 复制数据文件
cp data/lol_sft.json "$LLAMA_DATA/"
cp data/lol_sft_alpaca.json "$LLAMA_DATA/"

echo ""
echo "========================================"
echo "  数据准备完成! 下一步: bash train.sh"
echo "========================================"
