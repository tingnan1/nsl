"""
天猫复购预测数据集：小文件清洗脚本

本阶段只清洗三个较小文件：
    1. train_format1.csv
    2. test_format1.csv
    3. user_info_format1.csv

暂时不处理 user_log_format1.csv，因为它是 1.78 GB、约 5492 万行，
下一步会单独用分块方式清洗。

清洗规则：
    - train：ID 转成整数，label 转成 int8。
    - test：ID 转成整数，prob 保持为空（待预测）。
    - user_info：age_range、gender 使用可空整数 Int64，保留缺失值，
      同时生成 age_range_missing、gender_missing 两个缺失标记列。
    - user_info 如果同一个 user_id 出现多次，只保留第一条并记录。

运行方式（PowerShell）：
    .\.venv\Scripts\python.exe python\data_cleaning.py
"""

from datetime import datetime
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "tmall" / "data_format1"
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"


def ensure_clean_dir() -> None:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)


def read_csv(filename: str) -> pd.DataFrame:
    """统一读取原始 CSV，避免每个函数重复写路径。"""
    return pd.read_csv(RAW_DIR / filename, encoding="utf-8")


def clean_train() -> tuple[pd.DataFrame, int]:
    print("清洗 train_format1.csv ...")
    df = read_csv("train_format1.csv")

    before = len(df)
    df["user_id"] = df["user_id"].astype("int64")
    df["merchant_id"] = df["merchant_id"].astype("int64")
    df["label"] = df["label"].astype("int8")

    # 质量检查已经确认没有完全重复行，这里再确认一次。
    duplicate_rows = int(df.duplicated().sum())
    if duplicate_rows > 0:
        df = df.drop_duplicates(keep="first")

    print(f"  清洗前 {before:,} 行，清洗后 {len(df):,} 行，删除重复 {duplicate_rows:,} 行")
    return df, before


def clean_test() -> tuple[pd.DataFrame, int]:
    print("清洗 test_format1.csv ...")
    df = read_csv("test_format1.csv")

    before = len(df)
    df["user_id"] = df["user_id"].astype("int64")
    df["merchant_id"] = df["merchant_id"].astype("int64")
    # prob 保持 NaN，因为它是我们后面要预测的答案。

    duplicate_rows = int(df.duplicated().sum())
    if duplicate_rows > 0:
        df = df.drop_duplicates(keep="first")

    print(f"  清洗前 {before:,} 行，清洗后 {len(df):,} 行，删除重复 {duplicate_rows:,} 行")
    return df, before


def clean_user_info() -> tuple[pd.DataFrame, int]:
    print("清洗 user_info_format1.csv ...")
    df = read_csv("user_info_format1.csv")

    before = len(df)

    # 先把缺失的 user_id 删除，因为无法关联用户画像。
    missing_user_id = int(df["user_id"].isna().sum())
    df = df.dropna(subset=["user_id"])
    df["user_id"] = df["user_id"].astype("int64")

    # 生成缺失标记，1 表示原始值为空，0 表示有值。
    df["age_range_missing"] = df["age_range"].isna().astype("int8")
    df["gender_missing"] = df["gender"].isna().astype("int8")

    # 可空整数类型允许存在 NaN，适合年龄和性别这种含缺失值的编码字段。
    df["age_range"] = df["age_range"].astype("Int64")
    df["gender"] = df["gender"].astype("Int64")

    # 如果 user_id 重复，只保留第一条，避免画像关联时产生一对多。
    duplicate_user_ids = int(df["user_id"].duplicated().sum())
    df = df.drop_duplicates(subset=["user_id"], keep="first")

    print(f"  清洗前 {before:,} 行，清洗后 {len(df):,} 行")
    print(f"  缺失 user_id 删除 {missing_user_id:,} 行")
    print(f"  重复 user_id 删除 {duplicate_user_ids:,} 行")
    return df, before


def save_clean(df: pd.DataFrame, filename: str) -> None:
    output_path = CLEAN_DIR / filename
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"  已保存：{output_path}")


def write_cleaning_log(train_before: int, train_after: int,
                       test_before: int, test_after: int,
                       user_info_before: int, user_info_after: int) -> None:
    log_path = CLEAN_DIR / "cleaning_log.txt"
    lines = [
        f"清洗时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"train_format1.csv：{train_before:,} -> {train_after:,}",
        f"test_format1.csv：{test_before:,} -> {test_after:,}",
        f"user_info_format1.csv：{user_info_before:,} -> {user_info_after:,}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  清洗记录已保存：{log_path}")


def main() -> None:
    ensure_clean_dir()

    train, train_before = clean_train()
    test, test_before = clean_test()
    user_info, user_info_before = clean_user_info()

    save_clean(train, "train_format1_clean.csv")
    save_clean(test, "test_format1_clean.csv")
    save_clean(user_info, "user_info_format1_clean.csv")

    write_cleaning_log(
        train_before=train_before,
        train_after=len(train),
        test_before=test_before,
        test_after=len(test),
        user_info_before=user_info_before,
        user_info_after=len(user_info),
    )

    print("\n小文件清洗完成。")


if __name__ == "__main__":
    main()
