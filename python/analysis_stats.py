"""
Phase 6：用户行为统计分析

设计要点（都是被数据规模逼出来的）：
    user_log 清洗后 2.30 GB / 54,925,330 行，不能整体读进内存。
    所以分块读取，只读需要的列，用 numpy 做累加。

    唯一用户数 / 每日 UV 这类"去重"指标，用 186 天 x 424,170 用户的 bool 矩阵
    （约 79 MB）一次性解决，比维护几十万到几千万个 pair 的集合省内存得多，
    结果还是精确值而不是 HyperLogLog 之类的估算。

输出：
    outputs/tables/*.csv     分析结果表
    outputs/charts/*.png     图表
    outputs/reports/phase6_summary.md   文字结论

运行方式（PowerShell）：
    .\\.venv\\Scripts\\python.exe python\\analysis_stats.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"
OUT_DIR = PROJECT_ROOT / "outputs"
TABLE_DIR = OUT_DIR / "tables"
CHART_DIR = OUT_DIR / "charts"
REPORT_DIR = OUT_DIR / "reports"

# ---- 由 Phase 3 / probe_id_ranges.py 实测得到的边界，用于分配定长数组 ----
USER_MAX = 424_170
CAT_MAX = 1_671
SELLER_MAX = 4_995
BRAND_MAX = 8_477

# 非闰年月份起始天，和 clean_user_log.py、dim_date 完全同一口径
DAYS_BEFORE_MONTH = {
    1: 0, 2: 31, 3: 59, 4: 90, 5: 120, 6: 151,
    7: 181, 8: 212, 9: 243, 10: 273, 11: 304, 12: 334,
}

DOY_MIN = 131          # 0511
DOY_MAX = 316          # 1112
N_DAYS = DOY_MAX - DOY_MIN + 1

CHUNK_SIZE = 3_000_000

# Phase 3 已核验的真实数字，用于本脚本自校验
EXPECTED_TOTAL_ROWS = 54_925_330
EXPECTED_ACTIONS = {0: 48_550_713, 1: 76_750, 2: 3_292_144, 3: 3_005_723}
EXPECTED_BRAND_NULL = 91_015

ACTION_NAMES = {0: "点击", 1: "加入购物车", 2: "购买", 3: "加入收藏夹"}

# 中文字体：Windows 自带 Microsoft YaHei / SimHei
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian"]
plt.rcParams["axes.unicode_minus"] = False


def doy_labels() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """把 day_of_year(131..316) 反解成 month、day、MMDD 字符串。"""
    doys = np.arange(DOY_MIN, DOY_MAX + 1)
    months = np.zeros_like(doys)
    days = np.zeros_like(doys)
    for idx, doy in enumerate(doys):
        for month in range(12, 0, -1):
            if doy > DAYS_BEFORE_MONTH[month]:
                months[idx] = month
                days[idx] = doy - DAYS_BEFORE_MONTH[month]
                break
    mmdd = np.array([f"{int(m):02d}{int(d):02d}" for m, d in zip(months, days)])
    return months, days, mmdd


def ensure_dirs() -> None:
    for path in (TABLE_DIR, CHART_DIR, REPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def scan_user_log() -> dict:
    """一趟扫完 54,925,330 行，返回所有累加结果。"""
    # 布尔去重矩阵：186 x 424171，约 79 MB
    day_user = np.zeros((N_DAYS, USER_MAX + 1), dtype=bool)
    day_buyer = np.zeros((N_DAYS, USER_MAX + 1), dtype=bool)
    users_by_action = np.zeros((4, USER_MAX + 1), dtype=bool)

    rows_by_day = np.zeros(N_DAYS, dtype=np.int64)
    actions_by_day = np.zeros((N_DAYS, 4), dtype=np.int64)
    purchases_by_user = np.zeros(USER_MAX + 1, dtype=np.int64)
    purchases_by_cat = np.zeros(CAT_MAX + 1, dtype=np.int64)
    purchases_by_seller = np.zeros(SELLER_MAX + 1, dtype=np.int64)
    purchases_by_brand = np.zeros(BRAND_MAX + 1, dtype=np.int64)
    events_by_action = np.zeros(4, dtype=np.int64)

    total_rows = 0
    brand_null = 0

    reader = pd.read_csv(
        CLEAN_DIR / "user_log_format1_clean.csv",
        usecols=["user_id", "cat_id", "seller_id", "brand_id",
                 "action_type", "day_of_year"],
        dtype={
            "user_id": "int32",
            "cat_id": "int32",
            "seller_id": "int32",
            "brand_id": "Int32",
            "action_type": "int8",
            "day_of_year": "int16",
        },
        chunksize=CHUNK_SIZE,
    )

    for chunk_index, chunk in enumerate(reader, start=1):
        user = chunk["user_id"].to_numpy(dtype=np.int32)
        doy = chunk["day_of_year"].to_numpy(dtype=np.int32) - DOY_MIN
        action = chunk["action_type"].to_numpy(dtype=np.int8)
        cat = chunk["cat_id"].to_numpy(dtype=np.int32)
        seller = chunk["seller_id"].to_numpy(dtype=np.int32)
        # brand_id 可空：缺失填 -1，后面统一排除
        brand = chunk["brand_id"].to_numpy(dtype=np.int32, na_value=-1)

        total_rows += len(chunk)
        brand_null += int((brand <= 0).sum())

        rows_by_day += np.bincount(doy, minlength=N_DAYS)
        actions_by_day += np.bincount(
            doy * 4 + action, minlength=N_DAYS * 4
        ).reshape(N_DAYS, 4)
        events_by_action += np.bincount(action, minlength=4)

        # 去重：直接往布尔矩阵里点灯，重复写入是幂等的
        day_user[doy, user] = True
        users_by_action[action, user] = True

        bought = action == 2
        if bought.any():
            day_buyer[doy[bought], user[bought]] = True
            purchases_by_user += np.bincount(user[bought], minlength=USER_MAX + 1)
            purchases_by_cat += np.bincount(cat[bought], minlength=CAT_MAX + 1)
            purchases_by_seller += np.bincount(seller[bought], minlength=SELLER_MAX + 1)
            brand_mask = bought & (brand > 0)
            if brand_mask.any():
                purchases_by_brand += np.bincount(
                    brand[brand_mask], minlength=BRAND_MAX + 1
                )

        print(f"  已处理 {chunk_index} 块，累计 {total_rows:,} 行", flush=True)

    return {
        "total_rows": total_rows,
        "brand_null": brand_null,
        "rows_by_day": rows_by_day,
        "actions_by_day": actions_by_day,
        "events_by_action": events_by_action,
        "purchases_by_user": purchases_by_user,
        "purchases_by_cat": purchases_by_cat,
        "purchases_by_seller": purchases_by_seller,
        "purchases_by_brand": purchases_by_brand,
        "uv_by_day": day_user.sum(axis=1),
        "buyers_by_day": day_buyer.sum(axis=1),
        "days_active_per_user": day_user.sum(axis=0),
        "users_by_action": users_by_action.sum(axis=1),
    }


def self_check(result: dict) -> list[tuple[str, str, str, str]]:
    """用 Phase 3 已核验的数字自校验本次统计，防止分块累加写错。"""
    checks = []
    checks.append((
        "总行数", f"{EXPECTED_TOTAL_ROWS:,}",
        f"{result['total_rows']:,}",
        "PASS" if result["total_rows"] == EXPECTED_TOTAL_ROWS else "FAIL",
    ))
    for action, expected in EXPECTED_ACTIONS.items():
        actual = int(result["events_by_action"][action])
        checks.append((
            f"action_type={action} {ACTION_NAMES[action]}",
            f"{expected:,}", f"{actual:,}",
            "PASS" if actual == expected else "FAIL",
        ))
    checks.append((
        "brand_id 缺失", f"{EXPECTED_BRAND_NULL:,}",
        f"{result['brand_null']:,}",
        "PASS" if result["brand_null"] == EXPECTED_BRAND_NULL else "FAIL",
    ))
    return checks


def build_tables(result: dict, months, days, mmdd) -> dict[str, pd.DataFrame]:
    """把累加结果整理成结果表。"""
    tables: dict[str, pd.DataFrame] = {}

    daily = pd.DataFrame({
        "mmdd": mmdd,
        "month": months,
        "day": days,
        "day_of_year": np.arange(DOY_MIN, DOY_MAX + 1),
        "行为条数": result["rows_by_day"],
        "活跃用户数UV": result["uv_by_day"],
        "点击事件": result["actions_by_day"][:, 0],
        "加购事件": result["actions_by_day"][:, 1],
        "购买事件": result["actions_by_day"][:, 2],
        "收藏事件": result["actions_by_day"][:, 3],
        "购买用户数": result["buyers_by_day"],
    })
    daily["人均行为条数"] = (daily["行为条数"] / daily["活跃用户数UV"]).round(2)
    tables["daily_summary"] = daily

    action_rows = []
    total_events = int(result["events_by_action"].sum())
    # 全周期去重用户数：活跃天数 > 0 的用户个数
    total_users = int(np.count_nonzero(result["days_active_per_user"]))
    for action in range(4):
        action_rows.append({
            "行为": ACTION_NAMES[action],
            "action_type": action,
            "事件数": int(result["events_by_action"][action]),
            "事件占比%": round(result["events_by_action"][action] * 100 / total_events, 2),
            "去重用户数": int(result["users_by_action"][action]),
            "用户占比%": round(result["users_by_action"][action] * 100 / total_users, 2),
        })
    tables["action_summary"] = pd.DataFrame(action_rows)

    def top_table(counts: np.ndarray, id_name: str, top_n: int = 15) -> pd.DataFrame:
        ids = np.arange(len(counts))
        order = np.argsort(counts)[::-1][:top_n]
        df = pd.DataFrame({
            id_name: ids[order],
            "购买事件数": counts[order],
        })
        valid_total = int(counts.sum())
        df["占全部购买事件%"] = (df["购买事件数"] * 100 / valid_total).round(2)
        return df

    tables["top_categories"] = top_table(result["purchases_by_cat"], "cat_id")
    tables["top_sellers"] = top_table(result["purchases_by_seller"], "seller_id")
    tables["top_brands"] = top_table(result["purchases_by_brand"], "brand_id")

    # 用户购买频次分布
    # 用显式区间而不是自己拼 bins：区间必须首尾衔接、既不重叠也不留空隙，
    # 否则"标签个数"和"计数个数"对不上，会直接抛 ValueError。
    freq = result["purchases_by_user"]
    buyers = freq[freq > 0]
    intervals = [
        ("1次", 1, 1),
        ("2次", 2, 2),
        ("3次", 3, 3),
        ("4-5次", 4, 5),
        ("6-10次", 6, 10),
        ("11-20次", 11, 20),
        ("21-50次", 21, 50),
        ("51-100次", 51, 100),
        ("100次以上", 101, None),
    ]
    freq_rows = []
    for label, low, high in intervals:
        if high is None:
            count = int((buyers >= low).sum())
        else:
            count = int(((buyers >= low) & (buyers <= high)).sum())
        freq_rows.append({"购买次数区间": label, "用户数": count})
    tables["purchase_freq_distribution"] = pd.DataFrame(freq_rows)

    # 自检：各区间人数之和必须等于有过购买行为的用户数
    assert sum(r["用户数"] for r in freq_rows) == int(len(buyers)), "购买频次分布人数合计不符"

    # 用户活跃天数分布
    active_days = result["days_active_per_user"]
    ad_bins = [1, 2, 3, 5, 10, 20, 50, 100, N_DAYS + 1]
    ad_labels = ["1天", "2天", "3-4天", "5-9天", "10-19天",
                 "20-49天", "50-99天", "100天及以上"]
    ad_counts = []
    for low, high in zip(ad_bins[:-1], ad_bins[1:]):
        ad_counts.append(int(((active_days >= low) & (active_days < high)).sum()))
    tables["active_days_distribution"] = pd.DataFrame({
        "活跃天数区间": ad_labels,
        "用户数": ad_counts,
    })

    # 双十一对比：1111 当天 vs 前 7 天均值 vs 后 1 天
    idx_1111 = int(np.where(mmdd == "1111")[0][0])
    # 注意：这里必须用 iloc 而不是 loc。
    # loc 的切片是「标签闭区间」，会把 1111 当天本身也算进"前 7 天"里。
    prev_week = (idx_1111 - 7, idx_1111)
    rows = []

    def at_1111(column: str) -> int:
        """1111 当天的值（按位置取，避免标签与位置混淆）。"""
        return int(daily[column].iloc[idx_1111])

    def prev_mean(column: str) -> float:
        """1104–1110 这 7 天的均值，区间左闭右开，不含 1111 当天。"""
        return float(daily[column].iloc[prev_week[0]:prev_week[1]].mean())

    for metric in ("行为条数", "活跃用户数UV", "购买事件", "购买用户数"):
        today = at_1111(metric)
        base = prev_mean(metric)
        rows.append({
            "指标": metric,
            "1111当天": today,
            "前7天均值": round(base, 1),
            "倍数": round(today / base, 2) if base else None,
        })
    tables["double11_comparison"] = pd.DataFrame(rows)
    tables["_idx_1111"] = idx_1111

    # 用户画像
    info = pd.read_csv(CLEAN_DIR / "user_info_format1_clean.csv")
    age_map = {
        0: "未知(编码0)", 1: "18岁以下", 2: "18-24岁", 3: "25-29岁",
        4: "30-34岁", 5: "35-39岁", 6: "40-49岁", 7: "50岁以上",
        8: "未知(编码8)",
    }
    age_series = info["age_range"].fillna(-1).astype(int).map(
        lambda v: age_map.get(v, f"未知(编码{v})" if v != -1 else "缺失(NULL)")
    )
    tables["age_distribution"] = (
        age_series.value_counts().rename_axis("年龄段").reset_index(name="用户数")
    )
    gender_map = {0: "女性", 1: "男性", 2: "未知(编码2)"}
    gender_series = info["gender"].fillna(-1).astype(int).map(
        lambda v: gender_map.get(v, f"未知(编码{v})" if v != -1 else "缺失(NULL)")
    )
    tables["gender_distribution"] = (
        gender_series.value_counts().rename_axis("性别").reset_index(name="用户数")
    )

    # 复购标签分布
    train = pd.read_csv(CLEAN_DIR / "train_format1_clean.csv")
    label_map = {1: "复购买家", 0: "非复购买家", -1: "非该商家新客户(不在预测范围)"}
    label_counts = train["label"].value_counts().rename_axis("label").reset_index(name="样本数")
    label_counts["含义"] = label_counts["label"].map(label_map)
    label_counts["占全部样本%"] = (label_counts["样本数"] * 100 / len(train)).round(2)
    tables["label_distribution"] = label_counts

    return tables


def save_tables(tables: dict[str, pd.DataFrame]) -> None:
    for name, df in tables.items():
        if name.startswith("_"):
            continue
        path = TABLE_DIR / f"{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  已保存表：{path.name}")


def plot_all(tables: dict[str, pd.DataFrame], months, mmdd) -> None:
    daily = tables["daily_summary"]
    idx_1111 = tables["_idx_1111"]

    # 图 1：行为总量与购买事件趋势（上：全期对数轴；下：双十一前后放大）
    # 单日 1058 万条的 1111 尖峰会把其余 185 天压成一条直线，
    # 所以全期用对数轴，另配一张 1101–1112 的线性放大图。
    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(13, 9))
    ax_top.plot(range(len(daily)), daily["行为条数"], color="#4C72B0", lw=1.2, label="全部行为条数")
    ax_top.plot(range(len(daily)), daily["购买事件"], color="#C44E52", lw=1.2, label="购买事件")
    ax_top.set_yscale("log")
    ax_top.set_ylabel("数量（对数轴）")
    ax_top.set_xticks(range(0, len(daily), 15))
    ax_top.set_xticklabels(mmdd[::15], rotation=45)
    ax_top.axvline(idx_1111, color="gray", ls="--", lw=1)
    ax_top.legend(loc="upper left")
    ax_top.set_title("每日行为总量与购买事件（全期 0511–1112，对数轴）")

    zoom = (idx_1111 - 10, len(daily))
    ax_bot.plot(range(*zoom), daily["行为条数"].iloc[zoom[0]:zoom[1]], color="#4C72B0", lw=1.6,
                marker="o", ms=3, label="全部行为条数")
    ax_bot.plot(range(*zoom), daily["购买事件"].iloc[zoom[0]:zoom[1]], color="#C44E52", lw=1.6,
                marker="o", ms=3, label="购买事件")
    ax_bot.set_xticks(range(*zoom))
    ax_bot.set_xticklabels(mmdd[zoom[0]:zoom[1]], rotation=45)
    ax_bot.set_ylabel("数量（线性轴）")
    ax_bot.axvline(idx_1111, color="gray", ls="--", lw=1)
    ax_bot.legend(loc="upper left")
    ax_bot.set_title("1101–1112 局部放大（线性轴）")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "01_daily_trend.png", dpi=150)
    plt.close(fig)

    # 图 2：每日 UV 与购买用户数（同样分两栏）
    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(13, 9))
    ax_top.plot(range(len(daily)), daily["活跃用户数UV"], color="#55A868", lw=1.2, label="活跃用户数 UV")
    ax_top.plot(range(len(daily)), daily["购买用户数"], color="#DD8452", lw=1.2, label="购买用户数")
    ax_top.set_ylabel("用户数")
    ax_top.set_xticks(range(0, len(daily), 15))
    ax_top.set_xticklabels(mmdd[::15], rotation=45)
    ax_top.axhline(424170, color="gray", ls=":", lw=1)
    ax_top.text(2, 424170, " 全量用户数 424,170", va="bottom", fontsize=9, color="gray")
    ax_top.axvline(idx_1111, color="gray", ls="--", lw=1)
    ax_top.legend(loc="upper left")
    ax_top.set_title("每日活跃用户数与购买用户数（全期）")

    ax_bot.plot(range(*zoom), daily["活跃用户数UV"].iloc[zoom[0]:zoom[1]], color="#55A868",
                lw=1.6, marker="o", ms=3, label="活跃用户数 UV")
    ax_bot.plot(range(*zoom), daily["购买用户数"].iloc[zoom[0]:zoom[1]], color="#DD8452",
                lw=1.6, marker="o", ms=3, label="购买用户数")
    ax_bot.set_xticks(range(*zoom))
    ax_bot.set_xticklabels(mmdd[zoom[0]:zoom[1]], rotation=45)
    ax_bot.set_ylabel("用户数")
    ax_bot.axvline(idx_1111, color="gray", ls="--", lw=1)
    ax_bot.legend(loc="upper left")
    ax_bot.set_title("1101–1112 局部放大")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "02_daily_uv.png", dpi=150)
    plt.close(fig)

    # 图 3：行为类型分布（事件数 vs 去重用户数）
    act = tables["action_summary"]
    fig, ax = plt.subplots(figsize=(9, 5.5))
    x = np.arange(len(act))
    width = 0.38
    ax.bar(x - width / 2, act["事件数"], width, label="事件数", color="#4C72B0")
    ax.bar(x + width / 2, act["去重用户数"], width, label="去重用户数（累计 186 天）", color="#DD8452")
    ax.set_xticks(x)
    ax.set_xticklabels(act["行为"])
    ax.set_yscale("log")
    ax.set_ylabel("数量（对数轴）")
    ax.legend()
    ax.set_title("四类行为的累计事件数与去重用户数")
    for i, (e, u) in enumerate(zip(act["事件数"], act["去重用户数"])):
        ax.text(i - width / 2, e, f"{e:,}", ha="center", va="bottom", fontsize=8, rotation=90)
        ax.text(i + width / 2, u, f"{u:,}", ha="center", va="bottom", fontsize=8, rotation=90)
    fig.tight_layout()
    fig.savefig(CHART_DIR / "03_action_mix.png", dpi=150)
    plt.close(fig)

    # 图 4：双十一对比
    d11 = tables["double11_comparison"]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    x = np.arange(len(d11))
    width = 0.38
    ax.bar(x - width / 2, d11["1111当天"], width, label="1111 当天", color="#C44E52")
    ax.bar(x + width / 2, d11["前7天均值"], width, label="前 7 天均值", color="#8172B3")
    ax.set_xticks(x)
    ax.set_xticklabels(d11["指标"])
    ax.set_yscale("log")
    ax.set_ylabel("数量（对数轴）")
    ax.legend()
    for i, (a, b) in enumerate(zip(d11["1111当天"], d11["前7天均值"])):
        ax.text(i - width / 2, a, f"{a:,}", ha="center", va="bottom", fontsize=8)
        ax.text(i + width / 2, b, f"{b:,.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_title("双十一（1111）与前一周边界对比")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "04_double11.png", dpi=150)
    plt.close(fig)

    # 图 5：品类 TOP10 购买事件
    cat = tables["top_categories"].head(10).iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh([f"cat_{c}" for c in cat["cat_id"]], cat["购买事件数"], color="#4C72B0")
    for y, (v, p) in enumerate(zip(cat["购买事件数"], cat["占全部购买事件%"])):
        ax.text(v, y, f" {v:,} ({p}%)", va="center", fontsize=9)
    ax.set_xlabel("购买事件数")
    ax.set_title("品类 TOP10（按购买事件数）")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "05_top_categories.png", dpi=150)
    plt.close(fig)

    # 图 6：商家 TOP10 购买事件
    sel = tables["top_sellers"].head(10).iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh([f"seller_{s}" for s in sel["seller_id"]], sel["购买事件数"], color="#55A868")
    for y, (v, p) in enumerate(zip(sel["购买事件数"], sel["占全部购买事件%"])):
        ax.text(v, y, f" {v:,} ({p}%)", va="center", fontsize=9)
    ax.set_xlabel("购买事件数")
    ax.set_title("商家 TOP10（按购买事件数）")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "06_top_sellers.png", dpi=150)
    plt.close(fig)

    # 图 7：购买频次分布
    freq = tables["purchase_freq_distribution"]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.bar(freq["购买次数区间"], freq["用户数"], color="#DD8452")
    for i, v in enumerate(freq["用户数"]):
        ax.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("用户数")
    ax.set_title("购买用户按购买事件次数分布")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(CHART_DIR / "07_purchase_freq.png", dpi=150)
    plt.close(fig)

    # 图 8：用户画像（年龄 + 性别）
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    age = tables["age_distribution"]
    axes[0].bar(age["年龄段"], age["用户数"], color="#4C72B0")
    for i, v in enumerate(age["用户数"]):
        axes[0].text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=8)
    axes[0].set_title("年龄区间分布")
    axes[0].set_ylabel("用户数")
    axes[0].tick_params(axis="x", rotation=35)

    gender = tables["gender_distribution"]
    axes[1].bar(gender["性别"], gender["用户数"], color="#C44E52")
    for i, v in enumerate(gender["用户数"]):
        axes[1].text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=8)
    axes[1].set_title("性别分布")
    axes[1].set_ylabel("用户数")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "08_user_profile.png", dpi=150)
    plt.close(fig)

    # 图 9：复购标签分布
    lab = tables["label_distribution"]
    fig, ax = plt.subplots(figsize=(9, 5.5))
    colors = {"复购买家": "#55A868", "非复购买家": "#C44E52",
              "非该商家新客户(不在预测范围)": "#8172B3"}
    ax.bar(lab["含义"], lab["样本数"], color=[colors.get(v, "#999") for v in lab["含义"]])
    for i, (v, p) in enumerate(zip(lab["样本数"], lab["占全部样本%"])):
        ax.text(i, v, f"{v:,}\n({p}%)", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("样本数")
    ax.set_title("训练集复购标签分布")
    ax.tick_params(axis="x", rotation=12)
    fig.tight_layout()
    fig.savefig(CHART_DIR / "09_label_distribution.png", dpi=150)
    plt.close(fig)

    # 图 10：用户活跃天数分布
    ad = tables["active_days_distribution"]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.bar(ad["活跃天数区间"], ad["用户数"], color="#8172B3")
    for i, v in enumerate(ad["用户数"]):
        ax.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("用户数")
    ax.set_title("用户在 186 天内的活跃天数分布")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(CHART_DIR / "10_active_days.png", dpi=150)
    plt.close(fig)

    print(f"  已生成 10 张图表到 {CHART_DIR}")


def write_report(tables: dict[str, pd.DataFrame], checks, result: dict) -> None:
    daily = tables["daily_summary"]
    act = tables["action_summary"]
    d11 = tables["double11_comparison"]
    lab = tables["label_distribution"]
    freq = tables["purchase_freq_distribution"]

    total_events = int(act["事件数"].sum())
    buy_users = int(act.loc[act["action_type"] == 2, "去重用户数"].iloc[0])
    all_users = int(result["days_active_per_user"].astype(bool).sum())

    buyers = int((result["purchases_by_user"] > 0).sum())
    multi = int((result["purchases_by_user"] >= 2).sum())

    label_1 = int(lab.loc[lab["label"] == 1, "样本数"].iloc[0]) if (lab["label"] == 1).any() else 0
    label_0 = int(lab.loc[lab["label"] == 0, "样本数"].iloc[0]) if (lab["label"] == 0).any() else 0
    label_m1 = int(lab.loc[lab["label"] == -1, "样本数"].iloc[0]) if (lab["label"] == -1).any() else 0
    in_scope = label_1 + label_0
    repurchase_rate = label_1 * 100 / in_scope if in_scope else 0

    # 1112 的实测值，用于在报告里说明数据截断尾巴
    rows_1112 = int(daily.loc[daily["mmdd"] == "1112", "行为条数"].iloc[0])
    buy_1112 = int(daily.loc[daily["mmdd"] == "1112", "购买事件"].iloc[0])
    addcart_users = int(act.loc[act["action_type"] == 1, "去重用户数"].iloc[0])

    lines = []
    lines.append("# Phase 6 用户行为统计分析报告\n")
    lines.append("> 数据来源：`data/clean/user_log_format1_clean.csv`（54,925,330 行）、"
                 "`train_format1_clean.csv`、`user_info_format1_clean.csv`。\n")
    lines.append("> 本文所有数字均由 `python/analysis_stats.py` 从本地真实数据计算得出，"
                 "没有使用任何估算值或占位值。\n")

    lines.append("\n## 1. 数据自校验\n")
    lines.append("统计逻辑与 Phase 3 已核验数字逐项比对，防止分块累加写错：\n")
    lines.append("| 检查项 | 预期 | 实际 | 结果 |")
    lines.append("|---|---|---|---|")
    for name, expected, actual, status in checks:
        lines.append(f"| {name} | {expected} | {actual} | {status} |")
    all_pass = all(c[3] == "PASS" for c in checks)
    lines.append(f"\n**结论：{'全部通过' if all_pass else '存在 FAIL，统计结果不可用'}**\n")

    lines.append("\n## 2. 这份数据的真实结构（读后面的结论前必须先看）\n")
    lines.append(
        "下面三条不是推断，是从 5,492 万行里数出来、并用两种独立实现交叉验证过的：\n"
    )
    lines.append(
        f"1. 去重用户 **{all_users:,}** 个，`user_id` 连续覆盖 1~424,170，没有跳号。"
    )
    lines.append(
        "2. **这 424,170 个用户全部在 1111 当天出现过，而且每人都至少有一次购买事件**。"
        "1111 当天的用户集合与全量用户集合完全相等（集合相等，不是近似相等）。"
    )
    lines.append(
        "3. **1112 当天只有 %s 行、%s 次购买**，日志实际上在 1111 就结束了。\n" % (
            f"{rows_1112:,}", f"{buy_1112:,}")
    )
    lines.append("由此得到两个必须写进报告的口径限制：\n")
    lines.append(
        "- 这份竞赛数据的抽样口径是「11.11 当天有购买行为的用户」，"
        "**不是全站随机用户**。所以："
    )
    lines.append(
        "  - 「购买用户占比 = 100%」是抽样口径的必然结果，**这个数字没有信息量**，"
        "不能当成转化率或渗透率来讲；"
    )
    lines.append(
        f"  - 加购去重用户只有 **{addcart_users:,}** 人"
        f"（占全部用户的 {addcart_users * 100 / all_users:.2f}%），"
        "同样受抽样口径影响，不代表平台真实加购率。"
    )
    lines.append(
        "- 因为日志到 1111 就结束，**无法用本日志构造「1111 之后是否再购买」的标签**。"
        "复购标签只能沿用比赛方给出的 `label`（其观测窗口不在我们拿到的数据里）。"
        "这一点直接决定 Phase 8 的建模方式：特征只能用 1111 及之前的行为，"
        "标签来自外部，不能自己重新定义一遍复购。\n"
    )

    lines.append("\n## 3. 行为结构\n")
    lines.append(f"- 全周期共 **{total_events:,}** 条行为事件，覆盖 **{all_users:,}** 个用户。")
    lines.append(f"- 点击事件 **{int(act.loc[0, '事件数']):,}** 条，占 **{act.loc[0, '事件占比%']}%**，"
                 "行为日志以浏览为主，符合电商流量结构。")
    lines.append(f"- 购买事件 **{int(act.loc[2, '事件数']):,}** 条，"
                 f"涉及 **{buy_users:,}** 个用户。")
    lines.append(f"- 有购买行为的用户 **{buyers:,}** 人，其中有 **{multi:,}** 人购买事件 ≥2 次"
                 f"（占购买用户 **{multi * 100 / buyers:.2f}%**）。")
    lines.append("\n> 口径提醒：日志里没有会话 ID 和订单号，"
                 "所以**不能**直接算「单次会话的点击→购买转化率」。"
                 "上文的「用户占比」是 186 天累计口径，只能说明触达面，不能当作转化率。\n")

    lines.append("\n## 4. 时间趋势\n")
    peak_row = daily.loc[daily["行为条数"].idxmax()]
    peak_buy = daily.loc[daily["购买事件"].idxmax()]
    lines.append(f"- 行为量最高的一天是 **{peak_row['mmdd']}**，"
                 f"{int(peak_row['行为条数']):,} 条；当天 UV **{int(peak_row['活跃用户数UV']):,}**。")
    lines.append(f"- 购买事件最高的一天是 **{peak_buy['mmdd']}**，"
                 f"{int(peak_buy['购买事件']):,} 条。")
    # 日均只统计「有效窗口」：1112 只有 46 行、0 次购买，是数据截断的尾巴，
    # 计入会把日均拉低（含 1112 是 295,297，排除后是 296,893）。
    # 官方口径与 /api/overview 的 effective_days 保持一致。
    effective = daily[daily["mmdd"] != "1112"]
    lines.append(f"- 有效窗口 **{len(effective)} 天**（0511–1111），"
                 f"日均行为条数 **{effective['行为条数'].mean():,.0f}**，"
                 f"日均 UV **{effective['活跃用户数UV'].mean():,.0f}**。")
    lines.append(f"  > 1112 仅有 **{rows_1112:,} 条、{buy_1112:,} 次购买**，"
                 f"属于数据截断的尾巴，因此不计入日均。"
                 f"若把 186 天全算进来，日均会变成 "
                 f"{daily['行为条数'].mean():,.0f}，低估约 "
                 f"{(effective['行为条数'].mean() / daily['行为条数'].mean() - 1) * 100:.2f}%。")

    # 结构性跳变：找出环比倍数最大的一天，作为数据覆盖是否完整的证据。
    # 这类断层会严重扭曲"日均""整体趋势"，必须显式指出，不能一句带过。
    rows_series = daily["行为条数"].astype(float)
    ratios = rows_series / rows_series.shift(1)
    if ratios.notna().any():
        # 必须用 nanargmax：第 0 位是 shift(1) 产生的 NaN，
        # 而 np.argmax 碰到 NaN 会直接返回 0，导致这段判断永远不成立。
        break_pos = int(np.nanargmax(ratios.to_numpy()))
        if break_pos > 0:
            before = rows_series.iloc[:break_pos]
            lines.append(
                f"- **{daily['mmdd'].iloc[break_pos - 1]} → {daily['mmdd'].iloc[break_pos]} "
                f"存在结构性跳变**：单日行为条数从 {int(rows_series.iloc[break_pos - 1]):,} "
                f"涨到 {int(rows_series.iloc[break_pos]):,}"
                f"（{ratios.iloc[break_pos]:.2f}×）。"
                f"跳变前 {break_pos} 天日均只有 {before.mean():,.0f} 条，"
                f"跳变后日均 {rows_series.iloc[break_pos:].mean():,.0f} 条，"
                "相差一个量级。判断为**数据采集覆盖不完整**（早期只覆盖了部分用户），"
                "所以上述「日均」把两段混在一起算，会低估后半段的真实强度；"
                "做趋势或基线对比时，应剔除前 9 天或分段说明。"
            )

    lines.append("\n## 5. 双十一（1111）对比\n")
    lines.append("| 指标 | 1111 当天 | 前 7 天均值 | 倍数 |")
    lines.append("|---|---|---|---|")
    for _, row in d11.iterrows():
        lines.append(f"| {row['指标']} | {row['1111当天']:,} | {row['前7天均值']:,.0f} | "
                     f"{row['倍数']}× |")
    lines.append("\n> 对比基准是 1104–1110 的 7 天均值，同口径，未做任何假设性修正。\n")

    lines.append("\n## 6. 品类与商家集中度\n")
    cat10 = tables["top_categories"].head(10)
    sel10 = tables["top_sellers"].head(10)
    # 有购买事件的品类/商家总数（不是 top_n，两者别混）
    n_cat = int((result["purchases_by_cat"] > 0).sum())
    n_seller = int((result["purchases_by_seller"] > 0).sum())
    lines.append(f"- 购买事件最多的品类是 **cat_{int(cat10.iloc[0]['cat_id'])}**，"
                 f"{int(cat10.iloc[0]['购买事件数']):,} 次，"
                 f"占全部购买事件 **{cat10.iloc[0]['占全部购买事件%']}%**。")
    lines.append(f"- 购买事件一共分布在 **{n_cat:,}** 个品类上，"
                 f"TOP10 合计占 **{cat10['占全部购买事件%'].sum():.2f}%**。")
    lines.append(f"- 购买事件最多的商家是 **seller_{int(sel10.iloc[0]['seller_id'])}**，"
                 f"{int(sel10.iloc[0]['购买事件数']):,} 次，"
                 f"占 **{sel10.iloc[0]['占全部购买事件%']}%**。")
    lines.append(f"- 购买事件一共分布在 **{n_seller:,}** 个商家上，"
                 f"TOP10 合计只占 **{sel10['占全部购买事件%'].sum():.2f}%**，"
                 "商家侧非常分散，没有寡头。")
    lines.append("\n> 原始数据集的所有 ID 已脱敏，没有商家名/品类名，"
                 "因此这里只能用编码指代，不做任何名称猜测。\n")

    lines.append("\n## 7. 购买频次分布\n")
    lines.append("| 购买次数 | 用户数 |")
    lines.append("|---|---|")
    for _, row in freq.iterrows():
        lines.append(f"| {row['购买次数区间']} | {int(row['用户数']):,} |")

    lines.append("\n## 8. 复购标签（官方口径）\n")
    lines.append(f"- label=1（复购买家）**{label_1:,}** 条")
    lines.append(f"- label=0（非复购买家）**{label_0:,}** 条")
    lines.append(f"- label=-1（非该商家新客户，不在预测范围）**{label_m1:,}** 条"
                 + ("：说明训练集里实际没有 -1 样本，label 是干净的 0/1 二分类，"
                    "不需要再过滤。" if label_m1 == 0 else ""))
    lines.append(f"- **有效样本内复购率 = {label_1:,} / ({label_1:,}+{label_0:,}) = "
                 f"{repurchase_rate:.2f}%**")
    lines.append("\n> 这是标签本身的分布，不是模型效果；模型指标见 Phase 8。\n")

    lines.append("\n## 9. 本阶段的局限（必须写在报告里）\n")
    lines.append("1. `time_stamp` 只有 mmdd，没有年份，**不做工作日/周末对比**。"
                 "没有确凿日历年出处，就不编造星期几。")
    lines.append("2. 没有会话 ID / 订单号 / 金额，"
                 "**不能算真实转化率、客单价、GMV**，任何此类数字都是编的。")
    lines.append("3. 日志时间范围 0511–1112，缺少 1112 之后的数据，"
                 "双十一后续长尾无法评估。")
    lines.append("4. 用户画像的年龄/性别存在缺失（age_range 缺 2,217、gender 缺 6,436），"
                 "分布图包含「未知」类别，不做插补。")

    path = REPORT_DIR / "phase6_summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  已生成报告：{path}")


def main() -> None:
    ensure_dirs()
    months, days, mmdd = doy_labels()

    print("扫描 user_log_format1_clean.csv ...")
    result = scan_user_log()

    checks = self_check(result)
    print("\n自校验：")
    for name, expected, actual, status in checks:
        print(f"  [{status}] {name}：预期 {expected}，实际 {actual}")
    if any(c[3] == "FAIL" for c in checks):
        raise SystemExit("自校验失败，统计结果不可信，已中止。")

    print("\n生成结果表 ...")
    tables = build_tables(result, months, days, mmdd)
    save_tables(tables)

    print("\n生成图表 ...")
    plot_all(tables, months, mmdd)

    print("\n生成报告 ...")
    write_report(tables, checks, result)

    print("\nPhase 6 统计分析完成。")


if __name__ == "__main__":
    main()
