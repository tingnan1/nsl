"""
Phase 8 第一步：为每个 (user_id, merchant_id) 样本构造特征

样本单位是「用户 × 商家」对（train 260,864 对、test 261,477 对），
所以所有行为日志特征都必须聚合到「这个用户在这个商家身上」这个粒度。

防标签泄漏的三条硬约束：
    1. 特征只来自行为日志和用户画像。
    2. 绝对不用 train_sample 的 label 去反算任何特征
       （例如「该商家的复购率」就是泄漏，虽然它看起来很像个好特征）。
    3. 所有特征的时间窗口是 0511~1111，也就是比赛给的特征窗口；
       复购标签的观测窗口不在这份日志里，所以不存在"用未来预测过去"的问题。

实现要点：
    日志 5,492 万行，目标样本约 52 万对，不能逐行循环。
    把 (seller_id, user_id) 编码成 int64 组合键，排序后用 searchsorted
    在每块里定位命中的行，再用 bincount / maximum.at 累加。

运行方式（PowerShell）：
    .\\.venv\\Scripts\\python.exe python\\build_features.py
"""

from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"
TABLE_DIR = PROJECT_ROOT / "outputs" / "tables"

USER_MAX = 424_170
SELLER_MAX = 4_995
DOY_MIN, DOY_MAX = 131, 316
N_DAYS = DOY_MAX - DOY_MIN + 1
DOY_1111 = 315
CHUNK_SIZE = 3_000_000

KEY_FACTOR = 1_000_000


def pair_key(seller: np.ndarray, user: np.ndarray) -> np.ndarray:
    """把 (seller, user) 编码成唯一的 int64 键。user < 1e6，seller < 1e4，不会碰撞。"""
    return seller.astype(np.int64) * KEY_FACTOR + user.astype(np.int64)


def load_pairs() -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
    """读取训练/测试样本，返回两张表和它们的唯一组合键（排序后）。"""
    train = pd.read_csv(CLEAN_DIR / "train_format1_clean.csv")
    test = pd.read_csv(CLEAN_DIR / "test_format1_clean.csv")

    all_keys = np.unique(np.concatenate([
        pair_key(train["merchant_id"].to_numpy(), train["user_id"].to_numpy()),
        pair_key(test["merchant_id"].to_numpy(), test["user_id"].to_numpy()),
    ]))
    print(f"  训练样本 {len(train):,} 对，测试样本 {len(test):,} 对，"
          f"唯一用户商家组合 {len(all_keys):,} 个")
    return train, test, all_keys


def scan_log(ukeys: np.ndarray) -> dict:
    """一趟扫完日志，把行为聚合到样本对上，同时算用户级 / 商家级汇总。"""
    n = len(ukeys)
    p_clicks = np.zeros(n, dtype=np.int32)
    p_carts = np.zeros(n, dtype=np.int32)
    p_favs = np.zeros(n, dtype=np.int32)
    p_buys = np.zeros(n, dtype=np.int32)
    p_last_doy = np.zeros(n, dtype=np.int16)
    p_last_buy_doy = np.zeros(n, dtype=np.int16)

    user_buys = np.zeros(USER_MAX + 1, dtype=np.int64)
    user_events = np.zeros(USER_MAX + 1, dtype=np.int64)
    seller_buys = np.zeros(SELLER_MAX + 1, dtype=np.int64)
    day_user = np.zeros((N_DAYS, USER_MAX + 1), dtype=bool)

    # 商家去重买家数：用 (seller, user) 组合键去重后按商家计数
    buyer_keys_buf: list[np.ndarray] = []
    buyer_keys_total = 0
    COMPACT_AT = 8_000_000

    total_rows = 0

    reader = pd.read_csv(
        CLEAN_DIR / "user_log_format1_clean.csv",
        usecols=["user_id", "seller_id", "action_type", "day_of_year"],
        dtype={"user_id": "int32", "seller_id": "int32",
               "action_type": "int8", "day_of_year": "int16"},
        chunksize=CHUNK_SIZE,
    )

    for chunk_index, chunk in enumerate(reader, start=1):
        user = chunk["user_id"].to_numpy(dtype=np.int32)
        seller = chunk["seller_id"].to_numpy(dtype=np.int32)
        action = chunk["action_type"].to_numpy(dtype=np.int8)
        doy = chunk["day_of_year"].to_numpy(dtype=np.int32)
        total_rows += len(chunk)

        # ---- 用户级 / 商家级整体汇总 ----
        user_events += np.bincount(user, minlength=USER_MAX + 1)
        seller_buys += np.bincount(seller[action == 2], minlength=SELLER_MAX + 1)
        day_user[doy - DOY_MIN, user] = True

        bought = action == 2
        if bought.any():
            user_buys += np.bincount(user[bought], minlength=USER_MAX + 1)
            keys = np.unique(pair_key(seller[bought], user[bought]))
            buyer_keys_buf.append(keys)
            buyer_keys_total += keys.size
            if buyer_keys_total > COMPACT_AT:
                merged = np.unique(np.concatenate(buyer_keys_buf))
                buyer_keys_buf = [merged]
                buyer_keys_total = merged.size

        # ---- 定位命中样本对的行 ----
        keys_chunk = pair_key(seller, user)
        pos = np.searchsorted(ukeys, keys_chunk)
        pos_clipped = np.clip(pos, 0, len(ukeys) - 1)
        hit = ukeys[pos_clipped] == keys_chunk

        if hit.any():
            hp = pos_clipped[hit]
            ha = action[hit]
            hd = doy[hit]

            p_clicks += np.bincount(hp[ha == 0], minlength=n).astype(np.int32)
            p_carts += np.bincount(hp[ha == 1], minlength=n).astype(np.int32)
            p_favs += np.bincount(hp[ha == 3], minlength=n).astype(np.int32)

            buy_hit = ha == 2
            if buy_hit.any():
                p_buys += np.bincount(hp[buy_hit], minlength=n).astype(np.int32)
                # 同一对可能在多块里出现，取最大的 doy
                np.maximum.at(p_last_buy_doy, hp[buy_hit], hd[buy_hit].astype(np.int16))

            np.maximum.at(p_last_doy, hp, hd.astype(np.int16))

        print(f"  已处理 {chunk_index} 块，累计 {total_rows:,} 行", flush=True)

    buyer_keys = (np.unique(np.concatenate(buyer_keys_buf))
                  if buyer_keys_buf else np.array([], dtype=np.int64))
    seller_buyers = np.bincount((buyer_keys // KEY_FACTOR), minlength=SELLER_MAX + 1)

    return {
        "total_rows": total_rows,
        "p_clicks": p_clicks, "p_carts": p_carts, "p_favs": p_favs, "p_buys": p_buys,
        "p_last_doy": p_last_doy, "p_last_buy_doy": p_last_buy_doy,
        "user_buys": user_buys, "user_events": user_events,
        "seller_buys": seller_buys, "seller_buyers": seller_buyers,
        "user_active_days": day_user.sum(axis=0),
    }


def assemble(samples: pd.DataFrame, ukeys: np.ndarray, log: dict) -> pd.DataFrame:
    """把样本对和聚合结果拼成特征表。"""
    keys = pair_key(samples["merchant_id"].to_numpy(), samples["user_id"].to_numpy())
    pos = np.searchsorted(ukeys, keys)

    user = samples["user_id"].to_numpy()
    seller = samples["merchant_id"].to_numpy()

    p_buys = log["p_buys"][pos]
    user_buys = log["user_buys"][user]
    seller_buys = log["seller_buys"][seller]
    p_events = (log["p_clicks"] + log["p_carts"] + log["p_favs"] + log["p_buys"])[pos]

    out = pd.DataFrame({
        "user_id": user,
        "merchant_id": seller,
        # ---- 用户 × 商家 维度 ----
        "p_clicks": log["p_clicks"][pos],
        "p_carts": log["p_carts"][pos],
        "p_favs": log["p_favs"][pos],
        "p_buys": p_buys,
        "p_events": p_events,
        "p_has_cart": (log["p_carts"][pos] > 0).astype(np.int8),
        "p_has_fav": (log["p_favs"][pos] > 0).astype(np.int8),
        "p_has_buy": (p_buys > 0).astype(np.int8),
        # 距 1111 的天数；0 表示 1111 当天有行为，-1 表示从未有过该行为
        "p_last_recency": np.where(log["p_last_doy"][pos] > 0,
                                   DOY_1111 - log["p_last_doy"][pos], -1),
        "p_last_buy_recency": np.where(log["p_last_buy_doy"][pos] > 0,
                                       DOY_1111 - log["p_last_buy_doy"][pos], -1),
        # ---- 用户维度 ----
        "user_buys": user_buys,
        "user_events": log["user_events"][user],
        "user_active_days": log["user_active_days"][user],
        # ---- 商家维度 ----
        "seller_buys": seller_buys,
        "seller_buyers": log["seller_buyers"][seller],
    })

    # 占比特征：这个用户把多少购买力放在了这个商家上
    with np.errstate(divide="ignore", invalid="ignore"):
        out["p_buys_share_of_user"] = np.where(
            user_buys > 0, p_buys / np.maximum(user_buys, 1), 0.0).round(6)
        out["p_buys_share_of_seller"] = np.where(
            seller_buys > 0, p_buys / np.maximum(seller_buys, 1), 0.0).round(8)

    if "label" in samples.columns:
        out["label"] = samples["label"].to_numpy()

    return out


def add_profile(df: pd.DataFrame) -> pd.DataFrame:
    """补上用户画像字段，缺失用 -1 标记并在字典里说明，不做插补。"""
    info = pd.read_csv(CLEAN_DIR / "user_info_format1_clean.csv")
    info = info.rename(columns={"user_id": "user_id"})
    merged = df.merge(info, on="user_id", how="left")
    merged["age_range"] = merged["age_range"].fillna(-1).astype(np.int8)
    merged["gender"] = merged["gender"].fillna(-1).astype(np.int8)
    merged["age_range_missing"] = merged["age_range_missing"].fillna(1).astype(np.int8)
    merged["gender_missing"] = merged["gender_missing"].fillna(1).astype(np.int8)
    return merged


def feature_dictionary() -> pd.DataFrame:
    rows = [
        ("p_clicks", "该用户对该商家的点击事件数", "user_log", "兴趣强度"),
        ("p_carts", "该用户对该商家的加购事件数", "user_log", "购买意向"),
        ("p_favs", "该用户对该商家的收藏事件数", "user_log", "长期意向"),
        ("p_buys", "该用户对该商家的购买事件数", "user_log", "已发生的成交，复购的最强先兆"),
        ("p_events", "该用户对该商家的四类行为合计", "user_log", "整体互动强度"),
        ("p_has_cart", "是否对该商家加购过（0/1）", "user_log", "是否有加购行为"),
        ("p_has_fav", "是否对该商家收藏过（0/1）", "user_log", "是否有收藏行为"),
        ("p_has_buy", "是否对该商家购买过（0/1）", "user_log", "是否已成交"),
        ("p_last_recency", "该用户对该商家最后一次行为距 1111 的天数，-1 表示无行为",
         "user_log", "近期活跃度"),
        ("p_last_buy_recency", "该用户对该商家最后一次购买距 1111 的天数，-1 表示没买过",
         "user_log", "近期成交新鲜度"),
        ("user_buys", "该用户在全站（所有商家）的购买事件数", "user_log", "用户整体购买力"),
        ("user_events", "该用户的全部行为事件数", "user_log", "用户活跃度"),
        ("user_active_days", "该用户在 186 天内的活跃天数", "user_log", "用户粘性"),
        ("seller_buys", "该商家被所有用户购买的事件数", "user_log", "商家体量"),
        ("seller_buyers", "该商家的去重购买用户数", "user_log", "商家客户盘子"),
        ("p_buys_share_of_user", "p_buys / user_buys，该商家占该用户购买的比例",
         "派生", "用户对该商家的依赖程度"),
        ("p_buys_share_of_seller", "p_buys / seller_buys，该用户占该商家成交的比例",
         "派生", "用户对该商家的重要程度"),
        ("age_range", "年龄区间编码，-1 表示缺失", "user_info", "人群画像"),
        ("gender", "性别，-1 表示缺失；0 女 1 男 2 未知", "user_info", "人群画像"),
        ("age_range_missing", "原始 age_range 是否为空（1/0）", "user_info", "缺失标记"),
        ("gender_missing", "原始 gender 是否为空（1/0）", "user_info", "缺失标记"),
    ]
    return pd.DataFrame(rows, columns=["特征名", "含义", "来源", "业务解释"])


def main() -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)

    train, test, ukeys = load_pairs()
    print("\n扫描行为日志并聚合到样本对 ...")
    log = scan_log(ukeys)

    print("\n拼装特征 ...")
    train_f = add_profile(assemble(train, ukeys, log))
    test_f = add_profile(assemble(test, ukeys, log))

    # ---- 覆盖率自检：有多少样本对在日志里完全没有行为 ----
    cold_train = int((train_f["p_events"] == 0).sum())
    cold_test = int((test_f["p_events"] == 0).sum())
    print(f"\n冷启动样本（日志中无任何行为）："
          f"train {cold_train:,} / {len(train_f):,}，"
          f"test {cold_test:,} / {len(test_f):,}")

    print("\n特征与标签的初步关系（仅描述，不做因果解释）：")
    both = train_f["p_has_buy"] == 1
    for flag, name in ((True, "对该商家买过"), (False, "对该商家没买过")):
        sub = train_f[both == flag]
        if len(sub):
            print(f"  {name}：样本 {len(sub):,}，复购率 {sub['label'].mean() * 100:.2f}%")

    train_f.to_csv(TABLE_DIR / "features_train.csv", index=False, encoding="utf-8-sig")
    test_f.to_csv(TABLE_DIR / "features_test.csv", index=False, encoding="utf-8-sig")
    feature_dictionary().to_csv(TABLE_DIR / "feature_dictionary.csv",
                                index=False, encoding="utf-8-sig")
    print(f"\n已保存：features_train.csv（{len(train_f):,} 行 × "
          f"{train_f.shape[1]} 列）、features_test.csv（{len(test_f):,} 行）")


if __name__ == "__main__":
    main()
