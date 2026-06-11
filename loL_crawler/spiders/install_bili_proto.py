"""
下载 Bilibili 弹幕 proto 定义并生成 Python 代码
免手动编译,一键启用 protobuf 接口(绕开 412)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROTO_DIR = Path("danmaku_proto")
PROTO_FILE = PROTO_DIR / "dm_web_seg.proto"
GEN_DIR = PROTO_DIR

PROTO_CONTENT = """
syntax = "proto3";
package bilibili.danmaku;

message DanmakuElem {
  int64 id = 1;
  int32 progress = 2;
  int32 mode = 3;
  int32 fontsize = 4;
  uint32 color = 5;
  string midHash = 6;
  string content = 7;
  int64 ctime = 8;
  int32 weight = 9;
  string action = 10;
  int32 pool = 11;
  string idStr = 12;
  int32 attr = 13;
  string animation = 22;
  int64 mid = 25;
  int32 dm_type = 26;
  int64 dm_id = 27;
  int64 uid = 30;
  int64 show_flow_weight = 31;
  int64 show_count = 32;
  int64 dmid = 36;
}

message DmSegMobileReply {
  repeated DanmakuElem elems = 1;
  DmSegWebInfo web_info = 2;
  int32 visible_danmaku_num = 3;
}

message DmSegWebInfo {
  int32 dm_need_scan = 1;
  string scan_type = 2;
  string scan_end = 3;
}
"""


def install() -> None:
    PROTO_DIR.mkdir(parents=True, exist_ok=True)
    # 写入 .proto 文件
    PROTO_FILE.write_text(PROTO_CONTENT.strip(), encoding="utf-8")
    # 安装 protobuf (如尚未安装)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "protobuf"])
    # 编译 protobuf -> Python
    result = subprocess.run(
        [
            sys.executable, "-m", "grpc_tools.protoc",
            f"--proto_path={PROTO_DIR}",
            f"--python_out={GEN_DIR}",
            str(PROTO_FILE),
        ],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"proto 编译成功 -> {GEN_DIR / 'dm_web_seg_pb2.py'}")
    else:
        # 降级: 用 protoc 命令行(如果已安装 protoc)
        result2 = subprocess.run(
            ["protoc", f"--proto_path={PROTO_DIR}", f"--python_out={GEN_DIR}", str(PROTO_FILE)],
            capture_output=True, text=True,
        )
        if result2.returncode == 0:
            print(f"proto 编译成功(protoc) -> {GEN_DIR / 'dm_web_seg_pb2.py'}")
        else:
            print("未找到 protoc,使用纯 Python 方式安装 grpcio-tools...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "grpcio-tools"])
            subprocess.check_call([
                sys.executable, "-m", "grpc_tools.protoc",
                f"--proto_path={PROTO_DIR}",
                f"--python_out={GEN_DIR}",
                str(PROTO_FILE),
            ])
            print(f"proto 编译成功 -> {GEN_DIR / 'dm_web_seg_pb2.py'}")


if __name__ == "__main__":
    install()
