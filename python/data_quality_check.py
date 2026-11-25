"""
天猫复购预测数据集：数据质量检查脚本

目的：
    在真正清洗数据之前，先了解每个文件有多少行、字段类型、缺失值、
    重复值和取值范围。这样能避免后面盲目清洗。

重要说明：
    user_log_format1.csv 约 1.78 GB，如果一次性读入内存，普通电脑可能卡死。
    因此本脚本对 user_log 使用 chunksize 分块读取，每块 100 万行，
    逐块统计总行数、缺失值和 action_type 分布，不会一次性加载全部数据。

运行方式（PowerShell）：
    .\.venv\Scripts\python.exe python\data_quality_check.py
"""

from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "raw" / "tmall" / "data_format1"

SMALL_FILES = [
    "train_format1.csv",
    "test_format1.csv",
    "user_info_format1.csv",
]

USER_LOG_FILE = DATA_DIR / "user_log_format1.csv"
USER_LOG_CHUNK_SIZE = 1_000_000


def print_section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def inspect_small_file(filename: str) -> None:
    """读取并检查三个较小的 CSV 文件。"""
    path = DATA_DIR / filename
    print_section(f"检查文件：{filename}")

    df = pd.read_csv(path, encoding="utf-8")

    print(f"行数：{len(df)}")
    print(f"列数：{len(df.columns)}")
    print(f"字段：{list(df.columns)}")
    print("\n字段类型：")
    print(df.dtypes)

    print("\n缺失值数量：")
    print(df.isna().sum())

    duplicate_count = int(df.duplicated().sum())
    print(f"\n完全重复行数：{duplicate_count}")

    print("\n前 3 行：")
    print(df.head(3))


def inspect_user_log() -> None:
    """分块检查 1.78 GB 的用户行为日志。"""
    print_section("分块检查文件：user_log_format1.csv")

    # 给每一列指定类型，可以减少内存占用。
    # time_stamp 使用 str，是为了保留类似 0829 的前导零。
    # 使用可空整数类型 Int64，而不是 int64。
    # 因为 brand_id 等字段存在缺失值，普通 int64 遇到 NaN 会直接报错。
    dtype_map = {
        "user_id": "Int64",
        "item_id": "Int64",
        "cat_id": "Int64",
        "seller_id": "Int64",
        "brand_id": "Int64",
        "time_stamp": "str",
        "action_type": "Int8",
    }

    total_rows = 0
    missing_sum = pd.Series(dtype="int64")
    action_counts = pd.Series(dtype="int64")
    first_sample = None
    min_time_stamp = None
    max_time_stamp = None

    reader = pd.read_csv(
        USER_LOG_FILE,
        encoding="utf-8",
        dtype=dtype_map,
        chunksize=USER_LOG_CHUNK_SIZE,
    )

    for chunk_index, chunk in enumerate(reader, start=1):
        if first_sample is None:
            first_sample = chunk.head(3)

        total_rows += len(chunk)

        # 缺失值按列累加，避免把大文件同时保留在内存里。
        missing_sum = missing_sum.add(chunk.isna().sum(), fill_value=0)

        # action_type 的取值分布也需要跨块累加。
        counts = chunk["action_type"].value_counts()
        action_counts = action_counts.add(counts, fill_value=0)

        # 记录 time_stamp 的最小值和最大值，用来粗略判断时间范围。
        stamps = chunk["time_stamp"].dropna().astype(str)
        if not stamps.empty:
            if min_time_stamp is None or stamps.min() < min_time_stamp:
                min_time_stamp = stamps.min()
            if max_time_stamp is None or stamps.max() > max_time_stamp:
                max_time_stamp = stamps.max()

        # 每 5 块打印一次进度，便于观察程序是否还在正常运行。
        if chunk_index % 5 == 0:
            print(f"已处理 {chunk_index} 块，累计约 {total_rows:,} 行")

    print(f"\n总行数：{total_rows:,}")
    print(f"字段：{list(dtype_map.keys())}")
    print("\n缺失值数量：")
    print(missing_sum.astype("int64"))

    print("\naction_type 分布：")
    for action, count in action_counts.sort_index().items():
        print(f"  action_type={int(action)}：{int(count):,} 条")

    print(f"\ntime_stamp 最小/最大（mmdd 格式）：{min_time_stamp} ~ {max_time_stamp}")
    print("\n前 3 行：")
    print(first_sample)


def main() -> None:
    for filename in SMALL_FILES:
        inspect_small_file(filename)

    inspect_user_log()


if __name__ == "__main__":
    main()
