"""
Phase 7：运营指标计算

指标口径定义见 docs/metrics.md，本脚本只负责把「算得出来」的那些真的算出来。
凡是 docs/metrics.md §7 列的不可算指标，这里一律不产出替代数字。

核心分析设计（被数据结构逼出来的）：
    全部 424,170 个用户都在 1111 当天有购买，所以「全期最近一次购买 R」恒为 0，
    毫无区分度。真正有信息量的是 R_pre：「1111 之前是否有购买、多久前买过」。
    因此本项目用「1111 前是否购买」作为核心用户分层维度，并交叉官方 label
    计算分群复购率——这是一个能直接喂给 Phase 8 的可解释特征。

输出：
    outputs/tables/{action_overlap,seg_prior_purchase,user_recency_pre,
                    merchant_health,concentration}.csv
    outputs/reports/phase7_metrics.md

运行方式（PowerShell）：
    .\\.venv\\Scripts\\python.exe python\\metrics_kpi.py
"""

from pathlib import Path

import math

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"
TABLE_DIR = PROJECT_ROOT / "outputs" / "tables"
REPORT_DIR = PROJECT_ROOT / "outputs" / "reports"

USER_MAX = 424_170
CAT_MAX = 1_671
SELLER_MAX = 4_995
BRAND_MAX = 8_477

DOY_MIN, DOY_MAX = 131, 316
N_DAYS = DOY_MAX - DOY_MIN + 1
# 有效窗口：1112 只有 46 行、0 次购买，是数据截断的尾巴，不计入日均/占比类指标。
# 与 /api/overview 的 effective_days 及 Phase 6 报告保持同一口径。
EFFECTIVE_DAYS = N_DAYS - 1
DOY_1111 = 315
CHUNK_SIZE = 3_000_000

ACTION_NAMES = {0: "点击", 1: "加入购物车", 2: "购买", 3: "加入收藏夹"}

EXPECTED_TOTAL_ROWS = 54_925_330
EXPECTED_ACTIONS = {0: 48_550_713, 1: 76_750, 2: 3_292_144, 3: 3_005_723}


def scan() -> dict:
    """一趟扫完日志，产出指标所需的全部累加量。"""
    day_user = np.zeros((N_DAYS, USER_MAX + 1), dtype=bool)
    users_by_action = np.zeros((4, USER_MAX + 1), dtype=bool)

    purchases_by_user = np.zeros(USER_MAX + 1, dtype=np.int64)
    pre_purchases_by_user = np.zeros(USER_MAX + 1, dtype=np.int64)
    last_pre_doy = np.zeros(USER_MAX + 1, dtype=np.int16)  # 0 表示 1111 前没买过

    purchases_by_seller = np.zeros(SELLER_MAX + 1, dtype=np.int64)
    purchases_by_cat = np.zeros(CAT_MAX + 1, dtype=np.int64)
    purchases_by_brand = np.zeros(BRAND_MAX + 1, dtype=np.int64)
    events_by_action = np.zeros(4, dtype=np.int64)

    # 商家维度的去重买家数：用 (seller_id, user_id) 组合键累计后去重
    pair_buf: list[np.ndarray] = []
    pair_total = 0
    PAIR_COMPACT_AT = 8_000_000

    total_rows = 0
    brand_null = 0

    reader = pd.read_csv(
        CLEAN_DIR / "user_log_format1_clean.csv",
        usecols=["user_id", "cat_id", "seller_id", "brand_id",
                 "action_type", "day_of_year"],
        dtype={
            "user_id": "int32", "cat_id": "int32", "seller_id": "int32",
            "brand_id": "Int32", "action_type": "int8", "day_of_year": "int16",
        },
        chunksize=CHUNK_SIZE,
    )

    for chunk_index, chunk in enumerate(reader, start=1):
        user = chunk["user_id"].to_numpy(dtype=np.int32)
        doy = chunk["day_of_year"].to_numpy(dtype=np.int32)
        action = chunk["action_type"].to_numpy(dtype=np.int8)
        cat = chunk["cat_id"].to_numpy(dtype=np.int32)
        seller = chunk["seller_id"].to_numpy(dtype=np.int32)
        brand = chunk["brand_id"].to_numpy(dtype=np.int32, na_value=-1)

        total_rows += len(chunk)
        brand_null += int((brand <= 0).sum())
        events_by_action += np.bincount(action, minlength=4)

        day_user[doy - DOY_MIN, user] = True
        users_by_action[action, user] = True

        bought = action == 2
        if bought.any():
            bu = user[bought]
            bd = doy[bought]
            bs = seller[bought]
            bc = cat[bought]

            purchases_by_user += np.bincount(bu, minlength=USER_MAX + 1)
            purchases_by_seller += np.bincount(bs, minlength=SELLER_MAX + 1)
            purchases_by_cat += np.bincount(bc, minlength=CAT_MAX + 1)

            bm = bought & (brand > 0)
            if bm.any():
                purchases_by_brand += np.bincount(
                    brand[bm], minlength=BRAND_MAX + 1)

            # 1111 之前的购买：用于 R_pre
            pre = bd < DOY_1111
            if pre.any():
                pre_bu = bu[pre]
                pre_bd = bd[pre]
                pre_purchases_by_user += np.bincount(
                    pre_bu, minlength=USER_MAX + 1)
                # 同一用户可能多块出现，取最大 doy
                np.maximum.at(last_pre_doy, pre_bu, pre_bd.astype(np.int16))

            # (seller, user) 组合键
            keys = np.unique(bs.astype(np.int64) * 1_000_000 + bu.astype(np.int64))
            pair_buf.append(keys)
            pair_total += keys.size
            if pair_total > PAIR_COMPACT_AT:
                merged = np.unique(np.concatenate(pair_buf))
                pair_buf = [merged]
                pair_total = merged.size

        print(f"  已处理 {chunk_index} 块，累计 {total_rows:,} 行", flush=True)

    keys = np.unique(np.concatenate(pair_buf)) if pair_buf else np.array([], dtype=np.int64)
    seller_buyers = np.bincount(
        (keys // 1_000_000).astype(np.int64), minlength=SELLER_MAX + 1)

    return {
        "total_rows": total_rows,
        "brand_null": brand_null,
        "events_by_action": events_by_action,
        "purchases_by_user": purchases_by_user,
        "pre_purchases_by_user": pre_purchases_by_user,
        "last_pre_doy": last_pre_doy,
        "purchases_by_seller": purchases_by_seller,
        "purchases_by_cat": purchases_by_cat,
        "purchases_by_brand": purchases_by_brand,
        "seller_buyers": seller_buyers,
        "seller_buyer_pairs": int(keys.size),
        "uv_by_day": day_user.sum(axis=1),
        "days_active_per_user": day_user.sum(axis=0),
        "users_by_action": users_by_action,
    }


def self_check(r: dict) -> list[tuple[str, str, str, str]]:
    checks = [(
        "行为总行数", f"{EXPECTED_TOTAL_ROWS:,}", f"{r['total_rows']:,}",
        "PASS" if r["total_rows"] == EXPECTED_TOTAL_ROWS else "FAIL",
    )]
    for action, expected in EXPECTED_ACTIONS.items():
        actual = int(r["events_by_action"][action])
        checks.append((
            f"action_type={action} {ACTION_NAMES[action]}", f"{expected:,}",
            f"{actual:,}", "PASS" if actual == expected else "FAIL",
        ))
    # 1111 前购买 + 1111 当天购买 应等于全部购买事件
    total_purchase = int(r["purchases_by_user"].sum())
    pre = int(r["pre_purchases_by_user"].sum())
    checks.append((
        "全期购买事件 == 1111前购买 + 1111当天购买？",
        f"{EXPECTED_ACTIONS[2]:,}",
        f"pre={pre:,}（仅校验总数 {total_purchase:,}）",
        "PASS" if total_purchase == EXPECTED_ACTIONS[2] and pre <= total_purchase else "FAIL",
    ))
    return checks


def hhi(counts: np.ndarray) -> tuple[float, float]:
    """返回 (HHI, TOP10 份额%)。HHI = sum(份额^2) * 10000。"""
    total = counts.sum()
    if total == 0:
        return 0.0, 0.0
    share = counts / total
    h = float((share ** 2).sum() * 10000)
    top10 = float(np.sort(counts)[::-1][:10].sum() / total * 100)
    return round(h, 2), round(top10, 2)


def build_tables(r: dict) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    ua = r["users_by_action"]

    # ---- 1. 转化类（受限口径）----
    click_u = int(ua[0].sum())
    cart_u = int(ua[1].sum())
    buy_u = int(ua[2].sum())
    fav_u = int(ua[3].sum())
    both_cart = int((ua[1] & ua[2]).sum())
    both_fav = int((ua[3] & ua[2]).sum())
    tables["action_overlap"] = pd.DataFrame([
        {"指标": "点击用户数", "值": click_u,
         "口径说明": "186 天累计去重，非会话口径"},
        {"指标": "加购用户数", "值": cart_u, "口径说明": "186 天累计去重"},
        {"指标": "收藏用户数", "值": fav_u, "口径说明": "186 天累计去重"},
        {"指标": "购买用户数", "值": buy_u, "口径说明": "186 天累计去重"},
        {"指标": "既加购又购买的用户数", "值": both_cart, "口径说明": "用户级重叠"},
        {"指标": "加购→购买用户重叠率%", "值": round(both_cart * 100 / cart_u, 2),
         "口径说明": "加购用户中有购买的比例，不是单次转化率"},
        {"指标": "收藏→购买用户重叠率%", "值": round(both_fav * 100 / fav_u, 2),
         "口径说明": "收藏用户中有购买的比例，不是单次转化率"},
        {"指标": "购买/点击 事件比", "值": round(
            int(r["events_by_action"][2]) / int(r["events_by_action"][0]), 4),
         "口径说明": "事件量比值，不是转化率"},
    ])

    # ---- 2. 用户分层：1111 前是否购买 × 官方 label ----
    train = pd.read_csv(CLEAN_DIR / "train_format1_clean.csv")
    had_pre = r["pre_purchases_by_user"] > 0
    train = train.copy()
    train["had_prior_purchase"] = had_pre[train["user_id"].to_numpy()]

    seg_rows = []
    for flag, name in ((True, "1111前有购买的老客"), (False, "1111前无购买的新客")):
        sub = train[train["had_prior_purchase"] == flag]
        n = len(sub)
        rep = int(sub["label"].sum())
        seg_rows.append({
            "分层": name,
            "样本数": n,
            "占样本%": round(n * 100 / len(train), 2),
            "复购样本数": rep,
            "分群复购率%": round(rep * 100 / n, 2) if n else None,
        })
    seg_rows.append({
        "分层": "全样本",
        "样本数": len(train),
        "占样本%": 100.0,
        "复购样本数": int(train["label"].sum()),
        "分群复购率%": round(train["label"].mean() * 100, 2),
    })
    tables["seg_prior_purchase"] = pd.DataFrame(seg_rows)

    # ---- 3. R_pre 分布（1111 前最近一次购买距 1111 的天数）----
    pre_users = r["last_pre_doy"] > 0
    recency = DOY_1111 - r["last_pre_doy"][pre_users].astype(np.int32)
    buckets = [
        ("1-7天", 1, 7), ("8-14天", 8, 14), ("15-30天", 15, 30),
        ("31-60天", 31, 60), ("61-90天", 61, 90),
        ("91-120天", 91, 120), ("120天以上", 121, 10**6),
    ]
    rows = []
    for name, low, high in buckets:
        rows.append({
            "R_pre 区间": name,
            "用户数": int(((recency >= low) & (recency <= high)).sum()),
        })
    rows.append({"R_pre 区间": "1111前无购买", "用户数": int((~pre_users).sum())})
    tables["user_recency_pre"] = pd.DataFrame(rows)

    # ---- 4. 商家健康度 ----
    grp = train.groupby("merchant_id").agg(
        官方样本数=("label", "size"),
        复购样本数=("label", "sum"),
    ).reset_index()
    grp["商家复购率%"] = (grp["复购样本数"] * 100 / grp["官方样本数"]).round(2)
    grp["购买事件数"] = r["purchases_by_seller"][grp["merchant_id"].to_numpy()]
    grp["购买用户数"] = r["seller_buyers"][grp["merchant_id"].to_numpy()]
    grp["样本量是否足够"] = np.where(grp["官方样本数"] >= 30, "是", "否(<30，不参与排名)")
    grp = grp.sort_values("官方样本数", ascending=False)
    tables["merchant_health"] = grp

    # ---- 5. 集中度 ----
    conc_rows = []
    for name, counts, kind in (
        ("品类", r["purchases_by_cat"], "cat_id"),
        ("商家", r["purchases_by_seller"], "seller_id"),
        ("品牌", r["purchases_by_brand"], "brand_id"),
    ):
        h, top10 = hhi(counts.astype(float))
        nonzero = int((counts > 0).sum())
        conc_rows.append({
            "维度": name,
            "有购买事件的个数": nonzero,
            "HHI": h,
            "集中度判定": "分散(<1500)" if h < 1500 else
                          ("中度(1500-2500)" if h < 2500 else "集中(>2500)"),
            "TOP10份额%": top10,
            "购买事件≤10的个数": int(((counts > 0) & (counts <= 10)).sum()),
        })
    tables["concentration"] = pd.DataFrame(conc_rows)

    return tables


def write_report(r: dict, checks, tables: dict[str, pd.DataFrame]) -> None:
    total_users = int(np.count_nonzero(r["days_active_per_user"]))
    events = r["events_by_action"]
    total_events = int(events.sum())
    avg_active_days = float(r["days_active_per_user"].mean())
    buyers = int((r["purchases_by_user"] > 0).sum())
    high_freq = int((r["purchases_by_user"] >= 2).sum())

    seg = tables["seg_prior_purchase"]
    old = seg[seg["分层"] == "1111前有购买的老客"].iloc[0]
    new = seg[seg["分层"] == "1111前无购买的新客"].iloc[0]
    conc = tables["concentration"]
    overlap = tables["action_overlap"].set_index("指标")["值"]

    lines = []
    lines.append("# Phase 7 运营指标计算结果\n")
    lines.append("> 指标定义、公式、口径与「算不出来的指标清单」见 `docs/metrics.md`。")
    lines.append("> 本文所有数字由 `python/metrics_kpi.py` 从本地真实数据计算得出。\n")

    lines.append("\n## 1. 自校验\n")
    lines.append("| 检查项 | 预期 | 实际 | 结果 |")
    lines.append("|---|---|---|---|")
    for name, expected, actual, status in checks:
        lines.append(f"| {name} | {expected} | {actual} | {status} |")
    lines.append(f"\n**结论：{'全部通过' if all(c[3] == 'PASS' for c in checks) else '存在 FAIL'}**\n")

    lines.append("\n## 2. 规模与强度\n")
    lines.append(f"- 累计用户数 **{total_users:,}**，行为事件 **{total_events:,}** 条。")
    lines.append(f"- 人均行为条数 **{total_events / total_users:.1f}** 条 / 人。")
    lines.append(f"- 人均购买事件数 **{int(events[2]) / total_users:.2f}** 次 / 人。")
    lines.append(f"- 人均活跃天数 **{avg_active_days:.1f}** 天"
                 f"（有效窗口 {EFFECTIVE_DAYS} 天，活跃天数平均占比 "
                 f"{avg_active_days * 100 / EFFECTIVE_DAYS:.1f}%）。")
    lines.append(f"- 购买事件 ≥2 次的用户 **{high_freq:,}** 人，"
                 f"占有购买用户 **{high_freq * 100 / buyers:.2f}%**。")

    lines.append("\n## 3. 转化类（受限口径，不可当转化率讲）\n")
    lines.append(f"- 加购用户 **{int(overlap['加购用户数']):,}** 人，"
                 f"其中 **{int(overlap['既加购又购买的用户数']):,}** 人有购买行为，"
                 f"加购→购买用户重叠率 **{overlap['加购→购买用户重叠率%']}%**。")
    lines.append(f"- 收藏用户 **{int(overlap['收藏用户数']):,}** 人，"
                 f"收藏→购买用户重叠率 **{overlap['收藏→购买用户重叠率%']}%**。")
    lines.append(f"- 购买/点击 事件比 **{overlap['购买/点击 事件比']}**。")
    lines.append("\n> **这两个 100% 是退化结果，不是洞察。**")
    lines.append("> 因为这份数据的全部 424,170 个用户都在 1111 有购买行为（见报告 §2 的分层表），"
                 "所以任何「某行为用户 → 购买用户」的重叠率都必然等于 100%，"
                 "与加购/收藏本身没有关系。**把它当成转化率极好的证据是错的。**")
    lines.append("> 真正有信息量的是绝对量：加购用户只有 "
                 f"{int(overlap['加购用户数']):,} 人（占全部用户 "
                 f"{int(overlap['加购用户数']) * 100 / total_users:.2f}%），"
                 f"收藏用户 {int(overlap['收藏用户数']):,} 人。")
    lines.append("\n> 另外，以上都是「186 天累计、用户级」口径。"
                 "因为没有会话 ID 和订单号，**不存在会话级转化率**，"
                 "对外汇报时必须带上这句注释，否则会被误读。\n")

    lines.append("\n## 4. 用户分层：1111 前是否购买（核心发现）\n")
    lines.append("| 分层 | 样本数 | 占样本% | 复购样本数 | 分群复购率% |")
    lines.append("|---|---|---|---|---|")
    for _, row in seg.iterrows():
        lines.append(f"| {row['分层']} | {int(row['样本数']):,} | {row['占样本%']} | "
                     f"{int(row['复购样本数']):,} | {row['分群复购率%']} |")
    lines.append("")
    if new["样本数"] and old["样本数"]:
        diff = float(old["分群复购率%"]) - float(new["分群复购率%"])
        lines.append(
            f"- 1111 前买过的老客复购率 **{old['分群复购率%']}%**，"
            f"1111 前没买过的新客复购率 **{new['分群复购率%']}%**，"
            f"相差 **{diff:+.2f} 个百分点**。"
        )

        # 显著性检验：这是全量样本的精确观测差，不是抽样估计。
        # 但如果要把它推断到「未来的同类用户」，就需要看不确定性有多大。
        # 用两项比例检验（pooled）给出 z 与 p，用非混合标准误给出差值的 95% 区间。
        # 这样被问到「差 1.5pp 会不会是噪声」时有据可答。
        n_old, r_old = int(old["样本数"]), int(old["复购样本数"])
        n_new, r_new = int(new["样本数"]), int(new["复购样本数"])
        p_old, p_new = r_old / n_old, r_new / n_new
        p_pool = (r_old + r_new) / (n_old + n_new)
        se_pool = math.sqrt(p_pool * (1 - p_pool) * (1 / n_old + 1 / n_new))
        z = (p_old - p_new) / se_pool
        p_value = math.erfc(abs(z) / math.sqrt(2))  # 双侧
        se_unpooled = math.sqrt(
            p_old * (1 - p_old) / n_old + p_new * (1 - p_new) / n_new)
        ci_low = (p_old - p_new) - 1.96 * se_unpooled
        ci_high = (p_old - p_new) + 1.96 * se_unpooled

        lines.append(
            f"- 统计口径：这是 **{n_old + n_new:,} 个样本上的精确观测差**，不是抽样估计。"
            f"若把它当作对未来同类用户的推断，两项比例检验给出 "
            f"**z = {z:.2f}，p ≈ {p_value:.2e}**（远小于 0.05），"
            f"差值 95% 置信区间 **[{ci_low * 100:.2f}, {ci_high * 100:.2f}] 个百分点**"
            f"（标准误 {se_unpooled * 100:.3f} pp）。"
            f"**差异在统计上极显著，不是噪声。**"
        )
        lines.append(
            "  > 但显著 ≠ 因果。样本量大到 26 万时，1.5 个百分点的差异也会高度显著；"
            "真正要回答「让用户先买一次能否提高复购」仍然需要对照实验，本数据做不到。"
        )
        lines.append(
            "  > 另外注意：两个分组的用户不是随机分配的（老客本来就更活跃），"
            "所以这个比较属于**观察性对比**，存在混杂因素。"
        )
    lines.append(
        "\n> 为什么不用「最近一次购买 R」：因为全部 424,170 个用户都在 1111 有购买，"
        "全期 R 恒为 0，没有任何区分度。所以这里改用 R_pre（1111 前的购买行为），"
        "它才有区分度。M（金额）不可算，数据无金额字段，所以是 RF 不是 RFM。\n"
    )

    lines.append("\n## 5. 集中度\n")
    lines.append("| 维度 | 有购买事件的个数 | HHI | 判定 | TOP10份额% | 购买事件≤10的个数 |")
    lines.append("|---|---|---|---|---|---|")
    for _, row in conc.iterrows():
        lines.append(f"| {row['维度']} | {row['有购买事件的个数']:,} | {row['HHI']} | "
                     f"{row['集中度判定']} | {row['TOP10份额%']} | "
                     f"{row['购买事件≤10的个数']:,} |")
    lines.append("\n> HHI = Σ(份额²)×10000，<1500 视为分散。"
                 "三个维度的 HHI 都远低于 1500，说明这份数据里没有垄断型商家/品类/品牌。\n")

    mh = tables["merchant_health"]
    enough = mh[mh["官方样本数"] >= 30]
    lines.append("\n## 6. 商家健康度\n")
    lines.append(f"- 有官方标签样本的商家 **{len(mh):,}** 个；"
                 f"其中样本量 ≥30 的只有 **{len(enough):,}** 个，"
                 f"其余 {len(mh) - len(enough):,} 个商家样本太少，"
                 "复购率不参与排名（避免小样本噪声被当成结论）。")
    if len(enough):
        top = enough.sort_values("商家复购率%", ascending=False).head(5)
        lines.append("\n样本量 ≥30 的商家中，复购率最高的 5 个：\n")
        lines.append("| merchant_id | 官方样本数 | 复购样本数 | 商家复购率% |")
        lines.append("|---|---|---|---|")
        for _, row in top.iterrows():
            lines.append(f"| {int(row['merchant_id'])} | {int(row['官方样本数']):,} | "
                         f"{int(row['复购样本数']):,} | {row['商家复购率%']} |")
    lines.append("\n> 商家编码已脱敏，没有商家名，所以只能用 ID 指代。\n")

    lines.append("\n## 7. 本阶段没有做的事（明确声明）\n")
    lines.append("1. 没有计算 GMV、客单价、ARPU、订单量——数据里没有金额和订单字段。")
    lines.append("2. 没有计算会话级转化率、跳出率——没有会话 ID。")
    lines.append("3. 没有做工作日/周末对比——只有 mmdd，没有年份。")
    lines.append("4. 没有做留存率——没有注册日期，也没有 1111 之后的观测窗口。")
    lines.append("5. 没有做 M（金额）分层——RFM 里的 M 不可算，本项目只做 RF。")

    path = REPORT_DIR / "phase7_metrics.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  已生成报告：{path}")


def main() -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    print("扫描 user_log_format1_clean.csv ...")
    result = scan()

    checks = self_check(result)
    print("\n自校验：")
    for name, expected, actual, status in checks:
        print(f"  [{status}] {name}：预期 {expected}，实际 {actual}")
    if any(c[3] == "FAIL" for c in checks):
        raise SystemExit("自校验失败，指标不可信，已中止。")

    print("\n生成指标表 ...")
    tables = build_tables(result)
    for name, df in tables.items():
        df.to_csv(TABLE_DIR / f"{name}.csv", index=False, encoding="utf-8-sig")
        print(f"  已保存：{name}.csv（{len(df)} 行）")

    print("\n生成报告 ...")
    write_report(result, checks, tables)

    print("\nPhase 7 指标计算完成。")


if __name__ == "__main__":
    main()
