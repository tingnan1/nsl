"""
Phase 4 建库前置探针：实测各 ID 字段的取值范围

用途：
    在定 mysql 建表脚本之前，先确认 user_id / item_id / cat_id / seller_id /
    brand_id 的实际最小值和最大值，避免把列类型定窄（溢出）或定得过宽（浪费空间）。

    5492 万行的表上，每个 ID 列从 BIGINT 降到 INT 能省下约 2 GB，
    但前提是实测最大值确实在 INT UNSIGNED 范围内。所以必须实测，不能凭感觉。

输出：
    仅打印到标准输出，不写任何文件。

运行方式（PowerShell）：
    .\\.venv\\Scripts\\python.exe python\\probe_id_ranges.py
"""

from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "tmall" / "data_format1"
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"

# INT UNSIGNED 的上限，用来判断列类型够不够。
INT_UNSIGNED_MAX = 4_294_967_295

LOG_COLUMNS = ["user_id", "item_id", "cat_id", "seller_id", "brand_id"]
CHUNK_SIZE = 2_000_000


def scan_user_log() -> dict[str, dict[str, int]]:
    """分块扫描行为日志，统计每个 ID 列的 min / max / 缺失数 / 非空唯一值个数上限。"""
    stats: dict[str, dict[str, int]] = {
        col: {"min": None, "max": None, "null": 0} for col in LOG_COLUMNS
    }

    reader = pd.read_csv(
        CLEAN_DIR / "user_log_format1_clean.csv",
        usecols=LOG_COLUMNS,
        chunksize=CHUNK_SIZE,
    )

    for chunk in reader:
        for col in LOG_COLUMNS:
            series = chunk[col]
            null_count = int(series.isna().sum())
            stats[col]["null"] += null_count

            valid = series.dropna()
            if valid.empty:
                continue

            chunk_min = int(valid.min())
            chunk_max = int(valid.max())

            if stats[col]["min"] is None or chunk_min < stats[col]["min"]:
                stats[col]["min"] = chunk_min
            if stats[col]["max"] is None or chunk_max > stats[col]["max"]:
                stats[col]["max"] = chunk_max

    return stats


def scan_small_file(filename: str, columns: list[str]) -> dict[str, dict[str, int]]:
    """扫描小文件里的 ID 列。"""
    df = pd.read_csv(CLEAN_DIR / filename, usecols=columns)
    out = {}
    for col in columns:
        series = df[col]
        out[col] = {
            "min": int(series.min()) if series.notna().any() else None,
            "max": int(series.max()) if series.notna().any() else None,
            "null": int(series.isna().sum()),
        }
    return out


def verdict(min_value: int | None, max_value: int | None) -> str:
    """给出列类型建议。"""
    if min_value is None:
        return "全空，只能 NULL"
    if max_value <= INT_UNSIGNED_MAX and min_value >= 0:
        return "INT UNSIGNED 够用"
    return "需要 BIGINT"


def print_stats(title: str, stats: dict[str, dict[str, int]]) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    print(f"{'列名':<14}{'最小值':>12}{'最大值':>14}{'缺失':>10}   建议")
    print("-" * 78)
    for col, s in stats.items():
        min_text = "NULL" if s["min"] is None else f"{s['min']:,}"
        max_text = "NULL" if s["max"] is None else f"{s['max']:,}"
        print(f"{col:<14}{min_text:>12}{max_text:>14}{s['null']:>10,}   {verdict(s['min'], s['max'])}")


def main() -> None:
    print("扫描 user_log_format1_clean.csv（2.30 GB，分块读取）...")
    print_stats("user_log_format1_clean.csv", scan_user_log())

    print_stats(
        "train_format1_clean.csv",
        scan_small_file("train_format1_clean.csv", ["user_id", "merchant_id"]),
    )
    print_stats(
        "test_format1_clean.csv",
        scan_small_file("test_format1_clean.csv", ["user_id", "merchant_id"]),
    )
    print_stats(
        "user_info_format1_clean.csv",
        scan_small_file("user_info_format1_clean.csv", ["user_id"]),
    )


if __name__ == "__main__":
    main()
