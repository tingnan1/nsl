"""
天猫复购预测数据集：user_log_format1.csv 分块清洗脚本

为什么单独写一个脚本？
    user_log_format1.csv 约 1.78 GB、5492 万行，如果一次性读入内存，
    普通电脑很容易卡死。因此采用“分块读取 -> 逐块清洗 -> 逐块追加写入”的方式。

清洗内容：
    - user_id/item_id/cat_id/seller_id 转为普通整数；
    - brand_id 使用可空整数 Int64，因为该列有约 9 万条缺失；
    - action_type 使用 Int8；
    - time_stamp（mmdd）拆成 month、day、day_of_year 三列。
      day_of_year 使用非闰年的月份累计天数计算，仅用于表示相对时间顺序，
      不代表我们虚构了真实年份。

运行方式（PowerShell）：
    .\.venv\Scripts\python.exe python\clean_user_log.py
"""

from datetime import datetime
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "tmall" / "data_format1"
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"

INPUT_FILE = RAW_DIR / "user_log_format1.csv"
OUTPUT_FILE = CLEAN_DIR / "user_log_format1_clean.csv"
CHUNK_SIZE = 1_000_000

# 非闰年每个月份之前已经过去的天数，用于把 mmdd 转成 day_of_year。
# 1 月前 0 天，2 月前 31 天，3 月前 59 天，依此类推。
DAYS_BEFORE_MONTH = {
    1: 0,
    2: 31,
    3: 59,
    4: 90,
    5: 120,
    6: 151,
    7: 181,
    8: 212,
    9: 243,
    10: 273,
    11: 304,
    12: 334,
}


def build_dtype_map() -> dict[str, str]:
    """返回读取 CSV 时使用的字段类型。"""
    return {
        "user_id": "int64",
        "item_id": "int64",
        "cat_id": "int64",
        "seller_id": "int64",
        "brand_id": "Int64",
        "time_stamp": "str",
        "action_type": "Int8",
    }


def transform_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    """对单个数据块做清洗和特征构造。"""
    # time_stamp 是 mmdd 字符串，例如 0829。
    # 先统一去掉可能的空格，并处理长度不是 4 位的异常值。
    stamp = chunk["time_stamp"].astype("string").str.strip()

    month = pd.to_numeric(stamp.str[:2], errors="coerce").astype("Int8")
    day = pd.to_numeric(stamp.str[2:4], errors="coerce").astype("Int8")

    chunk["month"] = month
    chunk["day"] = day

    # 计算 day_of_year：当月前已经过去的天数 + 当月第几天。
    days_before = month.map(DAYS_BEFORE_MONTH)
    chunk["day_of_year"] = (days_before + day).astype("Int16")

    return chunk


def write_cleaning_log(total_rows: int, missing_brand: int) -> None:
    log_path = CLEAN_DIR / "user_log_cleaning_log.txt"
    lines = [
        f"清洗时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"输入文件：{INPUT_FILE}",
        f"输出文件：{OUTPUT_FILE}",
        f"总行数：{total_rows:,}",
        f"brand_id 缺失行数：{missing_brand:,}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n清洗记录已保存：{log_path}")


def main() -> None:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)

    dtype_map = build_dtype_map()
    total_rows = 0
    missing_brand = 0
    first_chunk = True

    reader = pd.read_csv(
        INPUT_FILE,
        encoding="utf-8",
        dtype=dtype_map,
        chunksize=CHUNK_SIZE,
    )

    for chunk_index, chunk in enumerate(reader, start=1):
        chunk = transform_chunk(chunk)
        total_rows += len(chunk)
        missing_brand += int(chunk["brand_id"].isna().sum())

        # 第一块用覆盖写（mode="w"）建立新文件，后续块才追加（mode="a"）。
        # 注意：这里不能统一用 mode="a"。否则重复运行时，新数据会直接接在
        # 上一次的产物后面，导致行数翻倍并产生重复记录。
        chunk.to_csv(
            OUTPUT_FILE,
            mode="w" if first_chunk else "a",
            header=first_chunk,
            index=False,
            encoding="utf-8",
        )
        first_chunk = False

        if chunk_index % 5 == 0:
            print(f"已处理 {chunk_index} 块，累计约 {total_rows:,} 行")

    print(f"\n清洗完成，总行数：{total_rows:,}")
    print(f"brand_id 缺失行数：{missing_brand:,}")
    write_cleaning_log(total_rows, missing_brand)


if __name__ == "__main__":
    main()
