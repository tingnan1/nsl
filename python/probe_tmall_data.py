r"""
天猫复购预测数据集：文件探针脚本

用途：
    不依赖 pandas，只用 Python 标准库读取每个 CSV 文件的表头和前几行，
    快速确认文件名、分隔符、字段名和数据规模是否符合预期。

运行方式（PowerShell）：
    python python\probe_tmall_data.py

如果你的电脑只有 py 启动器：
    py -3 python\probe_tmall_data.py
"""

import csv
from pathlib import Path


# 数据根目录：从本脚本所在目录向上找项目根目录，再进入 data/raw/tmall/data_format1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "raw" / "tmall" / "data_format1"

FILES = [
    "train_format1.csv",
    "test_format1.csv",
    "user_info_format1.csv",
    "user_log_format1.csv",
]


def human_size(num_bytes: int) -> str:
    """把字节数转换成易读的 MB / GB 文本。"""
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.2f} MB"


def inspect_file(filename: str) -> None:
    """打印一个 CSV 文件的表头、前 3 行和文件大小。"""
    path = DATA_DIR / filename
    if not path.exists():
        print(f"[缺失] {filename}")
        return

    size = path.stat().st_size
    print("=" * 60)
    print(f"文件：{filename}")
    print(f"大小：{human_size(size)}")

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        rows = []
        for i, row in enumerate(reader):
            rows.append(row)
            if i >= 2:
                break

    if not rows:
        print("内容：文件为空")
        return

    header = rows[0]
    print(f"字段数：{len(header)}")
    print(f"表头：{header}")
    for idx, row in enumerate(rows[1:], start=1):
        print(f"第 {idx} 行：{row}")


def main() -> None:
    print(f"数据目录：{DATA_DIR}")
    for filename in FILES:
        inspect_file(filename)
        print()


if __name__ == "__main__":
    main()
