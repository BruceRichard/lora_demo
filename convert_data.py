"""
将 sft_mix.jsonl 转换为 LLaMA-Factory 支持的格式
- ShareGPT 格式 (推荐)
- Alpaca 格式 (备选)
"""
import json
import shutil
from pathlib import Path

# 路径配置
SFT_MIX_PATH = Path("loL_crawler/data/sft/sft_mix.jsonl")
OUTPUT_DIR = Path("data")
SHAREGPT_PATH = OUTPUT_DIR / "lol_sft.json"
ALPACA_PATH = OUTPUT_DIR / "lol_sft_alpaca.json"


def convert_to_sharegpt(records: list[dict]) -> list[dict]:
    """转换为 ShareGPT 格式"""
    output = []
    for rec in records:
        system = rec.get("system", "")
        instruction = rec.get("instruction", "")
        response = rec.get("output", "")
        
        if not instruction or not response:
            continue
        
        item = {
            "messages": [
                {"role": "system", "content": system} if system else None,
                {"role": "user", "content": instruction},
                {"role": "assistant", "content": response}
            ]
        }
        # 过滤掉 None
        item["messages"] = [m for m in item["messages"] if m is not None]
        output.append(item)
    return output


def convert_to_alpaca(records: list[dict]) -> list[dict]:
    """转换为 Alpaca 格式"""
    output = []
    for rec in records:
        system = rec.get("system", "")
        instruction = rec.get("instruction", "")
        response = rec.get("output", "")
        
        if not instruction or not response:
            continue
        
        item = {
            "instruction": instruction,
            "input": "",
            "output": response,
            "system": system
        }
        output.append(item)
    return output


def main():
    if not SFT_MIX_PATH.exists():
        print(f"错误: 找不到 {SFT_MIX_PATH}")
        print("请先运行: cd loL_crawler && python main.py build-sft --max 0")
        return
    
    # 读取原始数据
    records = []
    with open(SFT_MIX_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    
    print(f"读取 {len(records)} 条原始数据")
    
    # 转换 ShareGPT 格式
    sharegpt_data = convert_to_sharegpt(records)
    with open(SHAREGPT_PATH, "w", encoding="utf-8") as f:
        json.dump(sharegpt_data, f, ensure_ascii=False, indent=2)
    print(f"ShareGPT 格式: {SHAREGPT_PATH} ({len(sharegpt_data)} 条)")
    
    # 转换 Alpaca 格式
    alpaca_data = convert_to_alpaca(records)
    with open(ALPACA_PATH, "w", encoding="utf-8") as f:
        json.dump(alpaca_data, f, ensure_ascii=False, indent=2)
    print(f"Alpaca 格式: {ALPACA_PATH} ({len(alpaca_data)} 条)")
    
    # 统计
    sources = {}
    for rec in records:
        src = rec.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    print("\n数据来源统计:")
    for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"  {src}: {cnt}")


if __name__ == "__main__":
    main()
