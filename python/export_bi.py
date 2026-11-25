"""
Phase 9 前置：导出 Power BI 可直接消费的宽表

为什么需要这一步：
    Power BI Desktop 连 MySQL 需要本机装 MySQL 连接器，权限和驱动版本都容易出问题。
    更稳的做法是把分析结果导出成扁平 CSV，Power BI 只读文件，零依赖。

产出（默认 outputs/bi/）：
    bi_dim_date.csv            日期维度，186 行
    bi_daily.csv               每日汇总（含四类行为分解），186 行
    bi_action_summary.csv      四类行为累计，4 行
    bi_category.csv            品类维度（全部 1,324 个），按购买事件降序
    bi_seller.csv              商家维度（全部 4,995 个），按购买事件降序
    bi_segments.csv            用户分层复购率，2 行
    bi_merchant_health.csv     商家健康度（含样本量过滤标记）
    bi_label_distribution.csv  训练集标签分布，2 行
    bi_predictions_test.csv    测试集预测概率，261,477 行

所有 CSV 用 utf-8-sig 编码，Excel / Power BI 打开中文不乱码。

运行：
    .\\.venv\\Scripts\\python.exe python\\export_bi.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pymysql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "outputs" / "bi"
TABLE_DIR = PROJECT_ROOT / "outputs" / "tables"


def load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv()

CONN = {
    "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", ""),
    "database": os.environ.get("MYSQL_DATABASE", "tmall_analysis"),
    "charset": "utf8mb4",
}


def fetch(sql: str) -> pd.DataFrame:
    """
    用游标直接取数，不用 pd.read_sql。

    pandas 对非 SQLAlchemy 的 DBAPI 连接会打 UserWarning，而且未来版本可能不再支持
    （实测 pandas 3.0.6 已经在警告）。直接 fetchall 构造 DataFrame 反而更干净。
    """
    conn = pymysql.connect(**CONN)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=columns)
    finally:
        conn.close()


def save(df: pd.DataFrame, name: str) -> None:
    path = OUT_DIR / name
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  {name:<28} {len(df):>9,} 行 × {df.shape[1]:>2} 列  "
          f"{path.stat().st_size / 1024:.0f} KB", flush=True)


def build_daily_table(daily_df: pd.DataFrame, uv_df: pd.DataFrame,
                      date_df: pd.DataFrame) -> pd.DataFrame:
    """
    把「按日聚合的行为数」「按日 UV」「日期维度」合成一张每日宽表。

    抽成独立函数是为了能单测：日期补零、购买率、人均条数、effective 合并
    这几处都是「算错了也不报错、只会让看板数字静默偏差」的地方。

    effective 必须带出来：Power BI 里做日均时要先过滤 effective = 1，
    否则 1112 那 46 行会把平均值拉低（295,297 vs 296,893）。
    """
    merged = daily_df.merge(uv_df, on="date_key", how="left")
    merged = merged.merge(
        date_df[["date_key", "month", "day", "day_of_year"]], on="date_key", how="left")
    merged["mmdd"] = merged["date_key"].astype(int).astype(str).str.zfill(4)
    merged["purchase_rate_pct"] = (
        merged["purchases"] * 100 / merged["events"]).round(3)
    merged["events_per_user"] = (merged["events"] / merged["uv"]).round(2)
    return merged[[
        "date_key", "mmdd", "month", "day", "day_of_year", "events", "uv",
        "clicks", "carts", "favs", "purchases", "purchase_rate_pct",
        "events_per_user", "effective",
    ]]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("导出日期维度 ...", flush=True)
    date_df = fetch("""
        SELECT date_key, month, day, day_of_year, is_weekend, days_to_1111
        FROM dim_date
        ORDER BY date_key
    """)
    save(date_df, "bi_dim_date.csv")

    print("导出每日汇总（含四类行为分解，需要扫全表）...", flush=True)
    daily_df = fetch("""
        SELECT f.date_key,
               SUM(f.action_type = 0) AS clicks,
               SUM(f.action_type = 1) AS carts,
               SUM(f.action_type = 3) AS favs,
               SUM(f.action_type = 2) AS purchases,
               COUNT(*)               AS events
        FROM fact_user_log f
        GROUP BY f.date_key
        ORDER BY f.date_key
    """)
    # UV 取自已物化的 agg_daily，避免再对 5492 万行做一次 COUNT(DISTINCT)
    uv_df = fetch("SELECT date_key, uv, effective FROM agg_daily ORDER BY date_key")
    save(build_daily_table(daily_df, uv_df, date_df), "bi_daily.csv")

    print("导出四类行为累计 ...", flush=True)
    action_df = fetch("""
        SELECT action_type, action_name, events, users,
               ROUND(100.0 * events / (SELECT SUM(events) FROM agg_action_summary), 2)
                   AS event_share_pct
        FROM agg_action_summary
        ORDER BY action_type
    """)
    save(action_df, "bi_action_summary.csv")

    print("导出标签分布 ...", flush=True)
    label_df = fetch("""
        SELECT label,
               CASE label WHEN 1 THEN '复购买家' ELSE '非复购买家' END AS label_name,
               COUNT(*) AS samples
        FROM train_sample
        GROUP BY label
        ORDER BY label DESC
    """)
    label_df["share_pct"] = (label_df["samples"] * 100 / label_df["samples"].sum()).round(2)
    save(label_df, "bi_label_distribution.csv")

    print("导出用户分层 ...", flush=True)
    seg_df = fetch("""
        SELECT segment, samples, repurchase, repurchase_rate
        FROM agg_segment_repurchase
        ORDER BY repurchase_rate DESC
    """)
    save(seg_df, "bi_segments.csv")

    print("导出品类维度（全部品类，含去重买家数，较慢）...", flush=True)
    cat_df = fetch("""
        SELECT cat_id,
               COUNT(*)                AS purchases,
               COUNT(DISTINCT user_id) AS buyers
        FROM fact_user_log
        WHERE action_type = 2
        GROUP BY cat_id
        ORDER BY purchases DESC, cat_id
    """)
    total = cat_df["purchases"].sum()
    cat_df["share_pct"] = (cat_df["purchases"] * 100 / total).round(4)
    cat_df["cumulative_pct"] = (cat_df["purchases"].cumsum() * 100 / total).round(4)
    cat_df["rank"] = range(1, len(cat_df) + 1)
    save(cat_df, "bi_category.csv")

    print("导出商家维度（全部商家，含去重买家数，较慢）...", flush=True)
    seller_df = fetch("""
        SELECT seller_id,
               COUNT(*)                AS purchases,
               COUNT(DISTINCT user_id) AS buyers
        FROM fact_user_log
        WHERE action_type = 2
        GROUP BY seller_id
        ORDER BY purchases DESC, seller_id
    """)
    seller_total = seller_df["purchases"].sum()
    seller_df["share_pct"] = (seller_df["purchases"] * 100 / seller_total).round(4)
    seller_df["cumulative_pct"] = (
        seller_df["purchases"].cumsum() * 100 / seller_total).round(4)
    seller_df["rank"] = range(1, len(seller_df) + 1)
    save(seller_df, "bi_seller.csv")

    print("导出商家健康度 ...", flush=True)
    merchant_df = fetch("""
        SELECT merchant_id,
               COUNT(*)                                AS samples,
               SUM(label)                              AS repurchase,
               ROUND(100.0 * SUM(label) / COUNT(*), 2) AS repurchase_rate
        FROM train_sample
        GROUP BY merchant_id
        ORDER BY samples DESC
    """)
    # 小样本复购率是噪声（实测出现过 52.94% 的极端值），单独标记，报表里要能过滤掉
    merchant_df["sample_size_ok"] = (merchant_df["samples"] >= 30).map(
        {True: "是", False: "否(样本<30)"})
    save(merchant_df, "bi_merchant_health.csv")

    print("复制测试集预测概率 ...", flush=True)
    pred_src = TABLE_DIR / "prediction_test.csv"
    if pred_src.exists():
        pred_df = pd.read_csv(pred_src)
        pred_df["prob_rank"] = pred_df["prob"].rank(ascending=False, method="first").astype(int)
        pred_df["prob_decile"] = pd.qcut(
            pred_df["prob"], 10, labels=[f"D{i}" for i in range(1, 11)]).astype(str)
        save(pred_df, "bi_predictions_test.csv")
    else:
        print("  跳过：outputs/tables/prediction_test.csv 不存在，"
              "请先运行 python/train_model.py", flush=True)

    print(f"\n全部导出完成 -> {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
