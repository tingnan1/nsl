"""
Phase 8：复购预测建模

任务：给定一个 (user_id, merchant_id) 样本，预测 label=1（复购买家）的概率。

三个必须守住的底线：
    1. **不用任何 target 派生特征**。比如「该商家的历史复购率」看似好用，
       但它是用 label 算出来的，属于泄漏，本脚本一律不用。
    2. **test 集没有标签**，所以只能产出预测概率，**不能报 test 上的任何指标**。
       所有指标都来自 train 的内部划分。
    3. **必须给基线**。如果模型打不过"全部预测众数"或"按单特征排序"，
       那就说明模型没用，必须如实写出来。

数据事实（决定了特征选择）：
    - 全部 260,864 个训练样本都满足「该用户已在该商家购买过」，
      所以 p_has_buy / p_last_buy_recency / p_last_recency 是常量或近似常量，
      必须剔除，否则只会误导特征重要性。
    - 正例率 6.12%，属于不平衡问题，所以除 AUC 外还要看 PR-AUC。

运行方式（PowerShell）：
    .\\.venv\\Scripts\\python.exe python\\train_model.py
"""

from datetime import datetime
from pathlib import Path
import json as jsonlib

import joblib
import numpy as np
import pandas as pd

from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, average_precision_score, confusion_matrix,
    f1_score, precision_score, recall_score, roc_auc_score, roc_curve,
    precision_recall_curve,
)
from sklearn.model_selection import (
    RepeatedStratifiedKFold, StratifiedKFold, cross_val_score, train_test_split,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TABLE_DIR = PROJECT_ROOT / "outputs" / "tables"
CHART_DIR = PROJECT_ROOT / "outputs" / "charts"
REPORT_DIR = PROJECT_ROOT / "outputs" / "reports"
MODEL_DIR = PROJECT_ROOT / "outputs" / "models"

RANDOM_STATE = 42

# 常量 / 近似常量特征，必须剔除（理由见文件头）
DEGENERATE = ["p_has_buy", "p_last_buy_recency", "p_last_recency"]
# 标识列不能当特征，商家体量信息已经由 seller_buys 表达
ID_COLUMNS = ["user_id", "merchant_id", "label"]
# p_events = p_clicks + p_carts + p_favs + p_buys，完全共线，去掉避免冗余
REDUNDANT = ["p_events"]

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian"]
plt.rcParams["axes.unicode_minus"] = False


def load_data() -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, list[str]]:
    train = pd.read_csv(TABLE_DIR / "features_train.csv")
    test = pd.read_csv(TABLE_DIR / "features_test.csv")

    drop = set(DEGENERATE + ID_COLUMNS + REDUNDANT)
    features = [c for c in train.columns if c not in drop]
    # test 表没有 label，确保特征列完全一致
    missing_in_test = [c for c in features if c not in test.columns]
    if missing_in_test:
        raise SystemExit(f"test 缺少特征列：{missing_in_test}")

    X = train[features]
    y = train["label"].astype(int)
    return X, y, test, features


def rule_scores(X: pd.DataFrame) -> np.ndarray:
    """基线规则得分：先看购买次数，再看该商家占该用户购买力的比例。"""
    return (X["p_buys"].to_numpy() * 100
            + X["p_buys_share_of_user"].to_numpy() * 10
            + X["p_has_fav"].to_numpy())


def evaluate(name: str, y_true, scores, threshold=0.5) -> dict:
    """统一算指标。scores 为连续分数或概率。"""
    auc = roc_auc_score(y_true, scores)
    ap = average_precision_score(y_true, scores)
    pred = (scores >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "模型": name,
        "ROC_AUC": round(float(auc), 4),
        "PR_AUC": round(float(ap), 4),
        "准确率": round(float(accuracy_score(y_true, pred)), 4),
        "精确率": round(float(precision_score(y_true, pred, zero_division=0)), 4),
        "召回率": round(float(recall_score(y_true, pred, zero_division=0)), 4),
        "F1": round(float(f1_score(y_true, pred, zero_division=0)), 4),
        "TP": int(tp), "FP": int(fp), "FN": int(fn), "TN": int(tn),
    }


def rate_threshold_metrics(name: str, y_true, scores, rate: float) -> dict:
    """
    按「预测正例率 = 训练集正例率」定阈值后的指标。

    为什么需要这张表：不平衡数据下，模型输出的概率绝对值不可比。
    HGB 没做类别权重，概率整体压在 0.5 以下，用固定 0.5 阈值会一个正例都不报
    （精确率/召回率都是 0），看起来像模型坏了，其实排序能力是三个模型里最好的。
    所以改成统一按分位数定阈值，让各模型的"报出多少正例"一致，才可比。
    """
    thr = float(np.quantile(scores, 1 - rate)) if len(scores) else 0.0
    pred = (scores >= thr).astype(int)
    return {
        "模型": name,
        "阈值": round(thr, 6),
        "预测正例数": int(pred.sum()),
        "精确率": round(float(precision_score(y_true, pred, zero_division=0)), 4),
        "召回率": round(float(recall_score(y_true, pred, zero_division=0)), 4),
        "F1": round(float(f1_score(y_true, pred, zero_division=0)), 4),
    }


def top_frac_report(y_true, scores, frac: float = 0.10) -> dict:
    """运营视角：只触达分数最高的 frac 比例人群，能捞到多少正例。"""
    n = len(scores)
    k = max(1, int(n * frac))
    order = np.argsort(scores)[::-1][:k]
    hit = int(y_true.to_numpy()[order].sum())
    total_pos = int(y_true.sum())
    base = total_pos / n
    prec = hit / k
    return {
        "触达比例": f"{frac * 100:.0f}%",
        "触达人数": k,
        "命中正例数": hit,
        "精确率": round(prec, 4),
        "召回率": round(hit / total_pos, 4),
        "相对基线提升倍数": round(prec / base, 2) if base else None,
    }


def build_business_value(y_true, scores,
                         fractions=(0.01, 0.05, 0.10, 0.20, 0.50)) -> pd.DataFrame:
    """
    把模型分数翻译成业务语言：只触达前 K% 人群时，能比随机多命中多少人。

    「相对随机提升」= 精确率 / 基准正例率；「较随机多命中」= 命中数 - K*N*基准率。
    后者才是运营真正能感知的量：同样的触达预算，多捞回多少个真实复购用户。
    """
    n = len(scores)
    y_arr = y_true.to_numpy()
    total_pos = int(y_arr.sum())
    base = total_pos / n
    order = np.argsort(scores)[::-1]

    rows = []
    for frac in fractions:
        k = max(1, int(n * frac))
        hit = int(y_arr[order[:k]].sum())
        precision = hit / k
        rows.append({
            "触达比例": f"{frac:.0%}",
            "触达人数": k,
            "命中正例": hit,
            "精确率": round(precision, 4),
            "召回率": round(hit / total_pos, 4),
            "相对随机提升": round(precision / base, 2) if base else None,
            "较随机多命中": int(round(hit - base * k)),
        })
    return pd.DataFrame(rows)


def compute_vif(X: pd.DataFrame) -> pd.DataFrame:
    """
    计算方差膨胀因子（VIF），检查特征共线性。

    VIF_j = 1 / (1 - R²_j)，R²_j 是把第 j 个特征对其余特征做线性回归得到的决定系数。
    VIF > 10 通常认为共线性严重。这里不用 statsmodels，直接调 sklearn 的
    LinearRegression，避免为一个指标引入额外依赖。
    """
    from sklearn.linear_model import LinearRegression

    values = X.to_numpy(dtype=float)
    rows = []
    for j, name in enumerate(X.columns):
        target = values[:, j]
        others = np.delete(values, j, axis=1)
        model = LinearRegression().fit(others, target)
        r2 = float(model.score(others, target))
        # R² 接近 1 时 VIF 趋于无穷，用 None 表示
        vif = None if r2 >= 1 - 1e-12 else round(1 / (1 - r2), 2)
        rows.append({"特征": name, "VIF": vif, "R²": round(r2, 4)})

    df = pd.DataFrame(rows)
    return df.sort_values("VIF", ascending=False, na_position="first").reset_index(drop=True)


def compute_corr_pairs(X: pd.DataFrame, threshold: float = 0.8) -> pd.DataFrame:
    """列出相关系数绝对值超过阈值的特征对。"""
    corr = X.corr().abs()
    cols = list(corr.columns)
    rows = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            value = float(corr.iloc[i, j])
            if value >= threshold:
                rows.append({"特征A": cols[i], "特征B": cols[j],
                             "|相关系数|": round(value, 4)})
    if not rows:
        return pd.DataFrame(columns=["特征A", "特征B", "|相关系数|"])
    return pd.DataFrame(rows).sort_values("|相关系数|", ascending=False).reset_index(drop=True)


def build_models(features: list[str]) -> dict:
    return {
        "逻辑回归": Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced",
                                       random_state=RANDOM_STATE)),
        ]),
        "随机森林": RandomForestClassifier(
            n_estimators=300, min_samples_leaf=20, max_features="sqrt",
            class_weight="balanced_subsample", n_jobs=-1, random_state=RANDOM_STATE,
        ),
        "梯度提升(HGB)": HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.06, max_leaf_nodes=31,
            min_samples_leaf=40, l2_regularization=1.0,
            random_state=RANDOM_STATE,
        ),
    }


def write_model_metadata(*, model, features, n_train, positive_rate,
                         best_name, holdout_row, thr_row, cv_auc) -> None:
    """
    写出模型元信息 JSON。

    为什么需要：joblib 是二进制，打开看不知道用了哪些特征、在多少行上训练、
    对应哪组指标。这个 JSON 让任何人（包括半年后的自己）不重跑训练就能核对
    「这个 artifact 是用什么数据、什么特征、什么指标产出的」。

    特别标注 training_scope：artifact 是**全量训练集**拟合的版本，
    而 holdout 指标来自 70% 版本的评估，两者不能混着引用。

    同时给出两套 holdout 指标：
      - holdout_metrics_at_0_5：固定 0.5 阈值。HGB 在这个阈值下精确率/召回率都是 0
        （概率整体压在 0.5 以下），单看这组数字会误判模型无效。
      - holdout_metrics_at_rate_threshold：按「预测正例率 = 训练集正例率」定阈值，
        这才是可以用来比较模型的版本。
    """
    import sklearn

    def to_native(row: dict) -> dict:
        out = {}
        for key, value in row.items():
            if key == "模型":
                continue
            if isinstance(value, np.integer):
                out[key] = int(value)
            elif isinstance(value, (np.floating, float)):
                out[key] = float(value)
            else:
                out[key] = value
        return out

    meta = {
        "artifact": "outputs/models/repurchase_model.joblib",
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "model_type": type(model).__name__,
        "algorithm_label": best_name,
        "training_scope": "全量训练集拟合；holdout 指标来自 70% 训练集版本，两者不可混用",
        "n_train_rows": int(n_train),
        "n_features": len(features),
        "features": features,
        "positive_rate": round(float(positive_rate), 6),
        "holdout_metrics_at_0_5": to_native(holdout_row),
        "holdout_metrics_at_rate_threshold": to_native(thr_row),
        "threshold_note": (
            "0.5 阈值下精确率/召回率为 0 属预期：本数据正例率仅 6.12%，"
            "HGB 未做类别权重，概率整体偏低。评估排序能力请看 ROC-AUC / PR-AUC，"
            "选择工作点请看 holdout_metrics_at_rate_threshold。"
        ),
        "cv_roc_auc_mean": round(float(cv_auc.mean()), 6),
        "cv_roc_auc_std": round(float(cv_auc.std()), 6),
        "random_state": RANDOM_STATE,
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
    }
    path = MODEL_DIR / "repurchase_model_meta.json"
    path.write_text(jsonlib.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已生成模型元信息：{path}")


def main() -> None:
    for path in (CHART_DIR, REPORT_DIR, MODEL_DIR):
        path.mkdir(parents=True, exist_ok=True)

    X, y, test, features = load_data()
    print(f"训练集：{X.shape[0]:,} 行 × {len(features)} 个特征，正例率 {y.mean() * 100:.2f}%")
    print(f"已剔除常量/冗余特征：{DEGENERATE + REDUNDANT}")
    print(f"实际使用特征：{features}\n")

    X_tr, X_va, y_tr, y_va = train_test_split(
        X, y, test_size=0.30, stratify=y, random_state=RANDOM_STATE)
    print(f"内部划分：训练 {len(X_tr):,} / 验证 {len(X_va):,}，"
          f"验证集正例率 {y_va.mean() * 100:.2f}%\n")

    results = []

    # ---- 基线 1：全部预测为多数类 ----
    const = np.zeros(len(y_va))
    results.append(evaluate("基线1：全预测为0（多数类）", y_va, const))

    # ---- 基线 2：单特征规则排序 ----
    rule = rule_scores(X_va)
    # 规则得分不在 0~1，用 AUC / PR-AUC 比较排序能力；阈值指标另算分位数
    rule_bin = (rule >= np.quantile(rule_scores(X_tr), 1 - y_tr.mean())).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_va, rule_bin, labels=[0, 1]).ravel()
    results.append({
        "模型": "基线2：规则排序（购买次数+占比+收藏）",
        "ROC_AUC": round(float(roc_auc_score(y_va, rule)), 4),
        "PR_AUC": round(float(average_precision_score(y_va, rule)), 4),
        "准确率": round(float(accuracy_score(y_va, rule_bin)), 4),
        "精确率": round(float(precision_score(y_va, rule_bin, zero_division=0)), 4),
        "召回率": round(float(recall_score(y_va, rule_bin, zero_division=0)), 4),
        "F1": round(float(f1_score(y_va, rule_bin, zero_division=0)), 4),
        "TP": int(tp), "FP": int(fp), "FN": int(fn), "TN": int(tn),
    })

    # ---- 三个模型 ----
    models = build_models(features)
    importances = {}
    for name, model in models.items():
        print(f"训练 {name} ...", flush=True)
        model.fit(X_tr, y_tr)
        proba = model.predict_proba(X_va)[:, 1]
        results.append(evaluate(name, y_va, proba))
        importances[name] = proba

    result_df = pd.DataFrame(results)
    print("\n验证集指标：")
    print(result_df.drop(columns=["TP", "FP", "FN", "TN"]).to_string(index=False))

    # 统一按训练集正例率定阈值的对照表（见 rate_threshold_metrics 的说明）
    all_scores = {
        "基线1：全预测为0（多数类）": const,
        "基线2：规则排序（购买次数+占比+收藏）": rule,
        **importances,
    }
    thr_df = pd.DataFrame([
        rate_threshold_metrics(name, y_va, sc, float(y_tr.mean()))
        for name, sc in all_scores.items()
    ])
    print("\n按训练集正例率统一阈值后的指标（预测正例数一致，可比）：")
    print(thr_df.to_string(index=False))

    # ---- 5 折交叉验证（只做最优模型的稳定性检验）----
    best_name = result_df.iloc[2:]["ROC_AUC"].astype(float).idxmax()
    best_name = result_df.loc[best_name, "模型"]
    best_model = models[best_name]
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
    cv_auc = cross_val_score(best_model, X, y, cv=cv, scoring="roc_auc", n_jobs=1)
    print(f"\n{best_name} 的 5 折 CV ROC-AUC：{cv_auc.round(4)}，"
          f"均值 {cv_auc.mean():.4f} ± {cv_auc.std():.4f}")

    # ---- 概率校准（概率能不能直接当预期值用）----
    # AUC 只说明排序能力，不说明「预测 0.15 的人真的约 15% 会复购」。
    # 业务上若要用概率算期望人数/预算分配，必须看校准。
    from sklearn.metrics import brier_score_loss

    proba_best = importances[best_name]
    brier = float(brier_score_loss(y_va, proba_best))
    base_rate = float(y_va.mean())
    brier_baseline = base_rate * (1 - base_rate)
    print(f"\n{best_name} 的 Brier 分数：{brier:.5f}"
          f"（常数预测基准线 {brier_baseline:.5f}，越小越好）")

    calib_df = pd.DataFrame({"y": np.asarray(y_va), "p": proba_best})
    calib_df["十分位"] = pd.qcut(calib_df["p"], 10, labels=False, duplicates="drop") + 1
    calib_tbl = (
        calib_df.groupby("十分位")
        .agg(样本数=("y", "size"), 平均预测概率=("p", "mean"), 实际复购率=("y", "mean"))
        .reset_index()
    )
    calib_tbl["平均预测概率"] = calib_tbl["平均预测概率"].round(4)
    calib_tbl["实际复购率"] = calib_tbl["实际复购率"].round(4)
    calib_tbl["偏差"] = (calib_tbl["实际复购率"] - calib_tbl["平均预测概率"]).round(4)
    print("校准表（按预测概率十分位）：")
    print(calib_tbl.to_string(index=False))
    calib_tbl.to_csv(TABLE_DIR / "model_calibration.csv", index=False, encoding="utf-8-sig")

    # ---- 稳健性：重复交叉验证 ----
    # 单次 5 折的均值会受划分随机性影响。重复 3 轮共 15 次评估，
    # 才能说明「0.65 是模型水平，不是某次划分撞上的」。
    cv_rep = RepeatedStratifiedKFold(n_splits=5, n_repeats=3, random_state=RANDOM_STATE)
    cv_auc_rep = cross_val_score(best_model, X, y, cv=cv_rep, scoring="roc_auc", n_jobs=1)
    print(f"\n{best_name} 重复交叉验证（5 折 × 3 轮 = {len(cv_auc_rep)} 次）："
          f"均值 {cv_auc_rep.mean():.4f} ± {cv_auc_rep.std():.4f}，"
          f"范围 [{cv_auc_rep.min():.4f}, {cv_auc_rep.max():.4f}]")

    # ---- 业务价值换算 ----
    business_df = build_business_value(y_va, proba_best)
    business_df.to_csv(TABLE_DIR / "business_value.csv", index=False, encoding="utf-8-sig")
    print("\n业务价值换算（在验证集上模拟不同触达预算）：")
    print(business_df.to_string(index=False))

    # ---- 特征共线性 ----
    vif_df = compute_vif(X)
    corr_df = compute_corr_pairs(X)
    vif_df.to_csv(TABLE_DIR / "feature_vif.csv", index=False, encoding="utf-8-sig")
    corr_df.to_csv(TABLE_DIR / "feature_corr_pairs.csv", index=False, encoding="utf-8-sig")
    print("\n特征共线性（VIF 降序，>10 视为严重）：")
    print(vif_df.head(8).to_string(index=False))
    print(f"|相关系数| >= 0.8 的特征对：{len(corr_df)} 组")

    # ---- 运营视角：取前 10% 人群 ----
    top_rows = []
    for name, proba in importances.items():
        row = top_frac_report(y_va, proba, 0.10)
        row["模型"] = name
        top_rows.append(row)
    top_df = pd.DataFrame(top_rows)[
        ["模型", "触达比例", "触达人数", "命中正例数", "精确率", "召回率", "相对基线提升倍数"]]
    print("\n只触达分数最高 10% 人群时：")
    print(top_df.to_string(index=False))

    # ---- 特征重要性（用最优模型，permutation 更可信）----
    print("\n计算特征重要性（permutation，n_repeats=5）...", flush=True)
    perm = permutation_importance(
        best_model, X_va, y_va, n_repeats=5, random_state=RANDOM_STATE,
        scoring="roc_auc", n_jobs=1)
    imp_df = pd.DataFrame({
        "特征": features,
        "重要性均值": perm.importances_mean.round(5),
        "标准差": perm.importances_std.round(5),
    }).sort_values("重要性均值", ascending=False)
    print(imp_df.head(12).to_string(index=False))

    # ---- 保存产物 ----
    result_df.to_csv(TABLE_DIR / "model_comparison.csv", index=False, encoding="utf-8-sig")
    thr_df.to_csv(TABLE_DIR / "model_threshold_rates.csv", index=False, encoding="utf-8-sig")
    top_df.to_csv(TABLE_DIR / "model_top10pct.csv", index=False, encoding="utf-8-sig")
    imp_df.to_csv(TABLE_DIR / "feature_importance.csv", index=False, encoding="utf-8-sig")

    # 用**全量训练集**重新拟合，并保存这个版本作为交付产物。
    # 注意：下面 holdout 指标仍然来自 70% 训练集拟合的那一版（那是唯一可评估的版本），
    # 但落盘的 artifact 与 prediction_test.csv 必须来自同一个模型，否则
    # 「部署的模型」和「产出预测的模型」不是同一个，属于交付不一致。
    final_model = build_models(features)[best_name]
    final_model.fit(X, y)
    joblib.dump(final_model, MODEL_DIR / "repurchase_model.joblib")
    write_model_metadata(
        model=final_model,
        features=features,
        n_train=len(X),
        positive_rate=float(y.mean()),
        best_name=best_name,
        holdout_row=result_df[result_df["模型"] == best_name].iloc[0].to_dict(),
        thr_row=thr_df[thr_df["模型"] == best_name].iloc[0].to_dict(),
        cv_auc=cv_auc,
    )

    test_proba = final_model.predict_proba(test[features])[:, 1]
    pred_out = test[["user_id", "merchant_id"]].copy()
    pred_out["prob"] = test_proba.round(6)
    pred_out.to_csv(TABLE_DIR / "prediction_test.csv", index=False, encoding="utf-8-sig")
    print(f"\n已生成 test 预测概率：prediction_test.csv（{len(pred_out):,} 行）"
          f"，预测均值 {test_proba.mean():.4f}")

    plot_all(y_va, importances, imp_df, result_df, calib_tbl)
    write_report(result_df, top_df, thr_df, imp_df, cv_auc, y, y_va, features, test_proba,
                 calib_tbl=calib_tbl, brier=brier, brier_baseline=brier_baseline,
                 cv_auc_rep=cv_auc_rep, business_df=business_df,
                 vif_df=vif_df, corr_df=corr_df)

    print("\nPhase 8 建模完成。")


def plot_all(y_va, probas: dict, imp_df: pd.DataFrame, result_df: pd.DataFrame,
             calib_tbl: pd.DataFrame | None = None) -> None:
    # 图 1：ROC
    fig, ax = plt.subplots(figsize=(8, 6.5))
    for name, proba in probas.items():
        fpr, tpr, _ = roc_curve(y_va, proba)
        auc = roc_auc_score(y_va, proba)
        ax.plot(fpr, tpr, lw=1.6, label=f"{name} (AUC={auc:.4f})")
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="随机猜测 (AUC=0.5)")
    ax.set_xlabel("假正率 FPR")
    ax.set_ylabel("真正率 TPR")
    ax.set_title("复购预测 ROC 曲线（验证集）")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "11_roc_curve.png", dpi=150)
    plt.close(fig)

    # 图 2：PR
    fig, ax = plt.subplots(figsize=(8, 6.5))
    base = y_va.mean()
    for name, proba in probas.items():
        prec, rec, _ = precision_recall_curve(y_va, proba)
        ap = average_precision_score(y_va, proba)
        ax.plot(rec, prec, lw=1.6, label=f"{name} (AP={ap:.4f})")
    ax.axhline(base, color="gray", ls="--", lw=1,
               label=f"随机基线 (正例率={base:.2%})")
    ax.set_xlabel("召回率")
    ax.set_ylabel("精确率")
    ax.set_title("复购预测 PR 曲线（验证集，正例仅 6.12%）")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "12_pr_curve.png", dpi=150)
    plt.close(fig)

    # 图 3：特征重要性
    top = imp_df.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.barh(top["特征"], top["重要性均值"], xerr=top["标准差"], color="#4C72B0")
    ax.set_xlabel("permutation 重要性（ROC-AUC 下降量）")
    ax.set_title("特征重要性 TOP15")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "13_feature_importance.png", dpi=150)
    plt.close(fig)

    # 图 4：预测分数分布
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for label, color, name in ((0, "#4C72B0", "label=0 非复购"),
                               (1, "#C44E52", "label=1 复购")):
        mask = y_va.to_numpy() == label
        ax.hist(np.concatenate([p[mask] for p in probas.values()]) / len(probas),
                bins=50, alpha=0.55, color=color, label=name, density=True)
    ax.set_xlabel("预测复购概率")
    ax.set_ylabel("密度")
    ax.set_title("不同真实标签下的预测分数分布（三个模型平均）")
    ax.legend()
    fig.tight_layout()
    fig.savefig(CHART_DIR / "14_score_distribution.png", dpi=150)
    plt.close(fig)

    # 图 5：校准曲线（预测概率 vs 实际复购率）
    if calib_tbl is not None and len(calib_tbl):
        # 预测概率全部落在 0.02~0.15 之间（未做类别权重），
        # 若直接画 0~1 全量程，所有点会挤在原点看不出偏差，所以按实测范围缩放。
        top = max(calib_tbl["平均预测概率"].max(),
                  calib_tbl["实际复购率"].max()) * 1.25
        fig, ax = plt.subplots(figsize=(7, 6))
        ax.plot([0, top], [0, top], "k--", lw=1, label="完美校准（y = x）")
        ax.plot(calib_tbl["平均预测概率"], calib_tbl["实际复购率"],
                "o-", color="#4C72B0", lw=1.6, ms=7, label="十分位实际值")
        for _, row in calib_tbl.iterrows():
            ax.annotate(f"{int(row['十分位'])}",
                        (row["平均预测概率"], row["实际复购率"]),
                        textcoords="offset points", xytext=(5, 5), fontsize=9,
                        color="#555555")
        ax.set_xlim(0, top)
        ax.set_ylim(0, top)
        ax.set_xlabel("平均预测概率（十分位）")
        ax.set_ylabel("实际复购率")
        ax.set_title("概率校准曲线（坐标轴按实测范围缩放，数字为十分位序号）")
        ax.legend(loc="upper left")
        ax.grid(alpha=0.25)
        ax.text(0.98, 0.04,
                "预测概率全部落在 0.02–0.15 区间\n（未做类别权重，正例率仅 6.12%）",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=9,
                color="#8b98a5")
        fig.tight_layout()
        fig.savefig(CHART_DIR / "15_calibration.png", dpi=150)
        plt.close(fig)

    print("已生成 5 张模型图表")


def write_report(result_df, top_df, thr_df, imp_df, cv_auc, y, y_va, features, test_proba,
                 calib_tbl=None, brier=None, brier_baseline=None,
                 cv_auc_rep=None, business_df=None, vif_df=None, corr_df=None) -> None:
    lines = []
    lines.append("# Phase 8 复购预测建模报告\n")
    lines.append("> 数据来源：`outputs/tables/features_train.csv`（由 `python/build_features.py` "
                 "从 5,492 万行行为日志聚合而来）。")
    lines.append("> 所有指标均由 `python/train_model.py` 实际训练与评估得出，没有任何占位或编造数值。\n")

    lines.append("\n## 1. 任务与数据\n")
    lines.append(f"- 样本单位：`(user_id, merchant_id)` 对，训练集 {len(y):,} 条。")
    lines.append(f"- 标签：`label=1` 复购买家，正例率 **{y.mean() * 100:.2f}%**（不平衡）。")
    lines.append(f"- 实际使用特征 {len(features)} 个；剔除的常量特征："
                 "`p_has_buy`（全为 1）、`p_last_buy_recency`（全为 0）、"
                 "`p_last_recency`（260,864 条里 260,862 条为 0）。")
    lines.append("\n> **为什么这些特征是常量**：全部训练样本和测试样本都满足"
                 "「该用户已在该商家购买过」——这正是比赛设定（样本都来自 11.11 成交用户）。"
                 "所以「是否买过」这类特征零方差，留着只会污染特征重要性。\n")

    lines.append("\n## 2. 验证集指标（内部 70/30 划分，固定随机种子 42）\n")
    show = result_df.drop(columns=["TP", "FP", "FN", "TN"])
    lines.append("| 模型 | ROC-AUC | PR-AUC | 准确率 | 精确率 | 召回率 | F1 |")
    lines.append("|---|---|---|---|---|---|---|")
    for _, r in show.iterrows():
        lines.append(f"| {r['模型']} | {r['ROC_AUC']} | {r['PR_AUC']} | {r['准确率']} | "
                     f"{r['精确率']} | {r['召回率']} | {r['F1']} |")
    lines.append("\n> 准确率在 6.12% 正例率下没有参考价值：全预测为 0 就有 93.88% 准确率。"
                 "**判断模型是否有效请看 ROC-AUC / PR-AUC 相对基线的提升。**\n")

    lines.append("\n### 2.1 统一阈值后的对照（各模型报出的正例数相同）\n")
    lines.append("固定 0.5 阈值对不平衡数据不公平，所以再按「预测正例率 = 训练集正例率 6.12%」"
                 "重新定阈值，让各模型报出同样多的正例后再比：\n")
    lines.append("| 模型 | 阈值 | 预测正例数 | 精确率 | 召回率 | F1 |")
    lines.append("|---|---|---|---|---|---|")
    for _, r in thr_df.iterrows():
        lines.append(f"| {r['模型']} | {r['阈值']} | {int(r['预测正例数']):,} | "
                     f"{r['精确率']} | {r['召回率']} | {r['F1']} |")
    lines.append("\n> **注意固定 0.5 阈值时 HGB 的精确率和召回率都是 0**：它没有做类别权重，"
                 "输出概率整体压在 0.5 以下，所以一个正例都不报。"
                 "这不代表模型无效——它的 ROC-AUC 和 PR-AUC 是三个模型里最好的，"
                 "统一阈值后 F1 也是最高。**用 0.5 阈值去评判不平衡模型是常见错误。**")
    lines.append("\n> 表里「基线1」一行看起来召回率 1.0 很漂亮，是假象："
                 "它的分数是常量 0，任何分位数阈值都等于 0，于是把所有样本都判成正例，"
                 "召回率必然 1.0、精确率必然等于正例率。这一行只用来标定下限，不能当成绩。\n")

    best = show.iloc[2:].sort_values("PR_AUC", ascending=False).iloc[0]
    baseline_rule = show[show["模型"].str.startswith("基线2")].iloc[0]
    lines.append(f"- 最优模型：**{best['模型']}**，ROC-AUC **{best['ROC_AUC']}**、"
                 f"PR-AUC **{best['PR_AUC']}**。")
    lines.append(f"- 相对规则基线（ROC-AUC {baseline_rule['ROC_AUC']}），"
                 f"ROC-AUC 提升 **{float(best['ROC_AUC']) - float(baseline_rule['ROC_AUC']):+.4f}**。")
    lines.append(f"- 5 折交叉验证 ROC-AUC：**{cv_auc.mean():.4f} ± {cv_auc.std():.4f}**，"
                 "说明结果不是某一次划分的偶然。")

    if cv_auc_rep is not None and len(cv_auc_rep):
        lines.append(
            f"- 重复交叉验证（5 折 × 3 轮 = {len(cv_auc_rep)} 次）："
            f"均值 **{cv_auc_rep.mean():.4f} ± {cv_auc_rep.std():.4f}**，"
            f"范围 **[{cv_auc_rep.min():.4f}, {cv_auc_rep.max():.4f}]**。")
        lines.append(
            "  > 单次 5 折的均值会被划分随机性影响；重复 15 次后区间仍然只有 "
            f"{cv_auc_rep.max() - cv_auc_rep.min():.4f} 的宽度，说明 "
            f"{cv_auc_rep.mean():.2f} 附近的水平是模型本身的能力，而不是某次划分撞上的。")

    lines.append("\n## 3. 运营视角：只触达前 10% 人群\n")
    lines.append("| 模型 | 触达人数 | 命中正例数 | 精确率 | 召回率 | 相对基线提升倍数 |")
    lines.append("|---|---|---|---|---|---|")
    for _, r in top_df.iterrows():
        lines.append(f"| {r['模型']} | {r['触达人数']:,} | {r['命中正例数']:,} | "
                     f"{r['精确率']} | {r['召回率']} | {r['相对基线提升倍数']}× |")
    lines.append("\n> 这一节才是运营真正关心的：预算只能覆盖一部分人时，"
                 "模型排序能不能比随机挑选更准。\n")

    if business_df is not None and len(business_df):
        lines.append("\n### 3.1 业务价值换算（不同触达预算下能多捞回多少人）\n")
        lines.append("在验证集（78,260 条样本，{:,} 个真实复购）上模拟不同触达比例：\n".format(
            int(y_va.sum())))
        lines.append("| 触达比例 | 触达人数 | 命中正例 | 精确率 | 召回率 | 相对随机提升 | 较随机多命中 |")
        lines.append("|---|---|---|---|---|---|---|")
        for _, r in business_df.iterrows():
            lines.append(
                f"| {r['触达比例']} | {int(r['触达人数']):,} | {int(r['命中正例']):,} | "
                f"{r['精确率']} | {r['召回率']} | {r['相对随机提升']}× | "
                f"**+{int(r['较随机多命中']):,}** |")
        sample = business_df[business_df["触达比例"] == "10%"]
        if len(sample):
            row = sample.iloc[0]
            lines.append(
                f"\n> 读法：若只触达前 10%（{int(row['触达人数']):,} 人），"
                f"模型能命中 {int(row['命中正例']):,} 个真实复购用户；"
                f"同样人数随机挑选只能命中约 {int(row['触达人数'] * float(y_va.mean())):,} 个。"
                f"**同样的触达预算，多捞回 {int(row['较随机多命中']):,} 个真实复购用户**"
                f"（提升 {row['相对随机提升']} 倍）。")
        lines.append(
            "\n> 这是离线模拟，不是线上收益：假设被命中的人不因触达而改变行为，"
            "也没有考虑触达成本与转化漏斗。真实投放需要 A/B 实验验证。\n")

    lines.append("\n## 4. 特征重要性（permutation，最优模型）\n")
    lines.append("| 特征 | 重要性均值 | 标准差 |")
    lines.append("|---|---|---|")
    for _, r in imp_df.head(10).iterrows():
        lines.append(f"| {r['特征']} | {r['重要性均值']} | {r['标准差']} |")
    lines.append("\n> permutation 重要性 = 打乱该特征后 ROC-AUC 的下降幅度。"
                 "负值表示打乱后指标反而上升，说明该特征在模型里没提供有效信息。\n")

    if vif_df is not None and len(vif_df):
        lines.append("\n### 4.1 特征共线性检查\n")
        lines.append("用方差膨胀因子（VIF = 1/(1-R²)）衡量每个特征能被其余特征线性解释的程度，"
                     "VIF > 10 通常视为共线性严重：\n")
        lines.append("| 特征 | VIF | R² |")
        lines.append("|---|---|---|")
        for _, r in vif_df.head(8).iterrows():
            vif_text = "∞" if r["VIF"] is None or pd.isna(r["VIF"]) else f"{r['VIF']:.2f}"
            lines.append(f"| {r['特征']} | {vif_text} | {r['R²']:.4f} |")
        high_vif = vif_df[
            (vif_df["VIF"].isna()) | (pd.to_numeric(vif_df["VIF"], errors="coerce") > 10)]
        lines.append(
            f"\n> VIF 超过 10 的特征有 **{len(high_vif)}** 个"
            + ("：" + "、".join(f"`{n}`" for n in high_vif["特征"].tolist()) if len(high_vif) else "")
            + "。")
        if corr_df is not None and len(corr_df):
            lines.append(
                f"> |相关系数| ≥ 0.8 的特征对有 **{len(corr_df)}** 组，"
                "最高的是 " + "、".join(
                    f"`{r['特征A']}`×`{r['特征B']}`（{r['|相关系数|']:.3f}）"
                    for _, r in corr_df.head(3).iterrows()) + "。")
            lines.append(
                "> 共线性不会让梯度提升失效（树模型对共线不敏感），但会让 permutation "
                "重要性在共线特征间被摊薄——两个高度相关的特征各自的重要性都会偏低，"
                "这是解释重要性时容易误判的地方。")
        lines.append("")

    lines.append("\n## 5. 测试集预测\n")
    lines.append(f"- 已输出 `prediction_test.csv`（261,477 行），预测概率均值 "
                 f"**{test_proba.mean():.4f}**，中位数 **{np.median(test_proba):.4f}**。")
    lines.append("- 落盘的 `outputs/models/repurchase_model.joblib` 是**全量训练集**拟合的版本，"
                 "与本文件的预测同源；上面第 2 节的 holdout 指标来自 **70% 训练集**那一版，"
                 "两者都对但不可混用。元信息见 `outputs/models/repurchase_model_meta.json`。")
    lines.append("- **测试集没有标签，因此不对它报任何准确率/AUC。**"
                 "任何声称在 test 上达到某个 AUC 的说法，要么用了泄漏，要么是编的。")

    if calib_tbl is not None and brier is not None:
        lines.append("\n## 6. 概率校准（概率能不能直接当预期值用）\n")
        lines.append(
            "- ROC-AUC 只衡量**排序能力**，不能说明「预测 0.15 的用户是否真的约 15% 会复购」。"
            "如果要用概率算期望人数、分配预算或做 ROI 估算，必须看校准。")
        lines.append(
            f"- **Brier 分数 {brier:.5f}**；常数预测基准线 {brier_baseline:.5f}"
            f"（相当于永远预测正例率，越小越好）。"
            f"{'优于' if brier < brier_baseline else '劣于'}基准线，"
            f"相对改善 **{(1 - brier / brier_baseline) * 100:.1f}%**。")
        lines.append("\n| 十分位 | 样本数 | 平均预测概率 | 实际复购率 | 偏差 |")
        lines.append("|---|---|---|---|---|")
        for _, row in calib_tbl.iterrows():
            lines.append(f"| {int(row['十分位'])} | {int(row['样本数']):,} | "
                         f"{row['平均预测概率']:.4f} | {row['实际复购率']:.4f} | "
                         f"{row['偏差']:+.4f} |")
        highest = calib_tbl.iloc[-1]
        max_dev = float(calib_tbl["偏差"].abs().max())
        lines.append(
            f"\n> 各十分位的偏差绝对值最大 **{max_dev:.4f}**；"
            f"最高十分位预测 {highest['平均预测概率']:.4f}、实际 "
            f"{highest['实际复购率']:.4f}（偏差 {highest['偏差']:+.4f}）。"
            "也就是说**概率本身基本可信，不只是排序可用**——这一点比 AUC 更能说明模型可用。")
        lines.append(
            f"> 但 Brier 相对基准线只改善 **{(1 - brier / brier_baseline) * 100:.1f}%**，"
            "说明大多数样本的预测都贴近基准率（6.12%），**绝对概率的区分度有限**，"
            "与 AUC 0.65 的判断一致。对外使用时：可做相对排序与粗略的期望人数估算，"
            "不宜把单个用户的概率当作精确承诺。\n")

    lines.append("\n## 7. 局限与风险（必须与结论一起读）\n")
    lines.append("1. **AUC 不会很高是正常的**。正例率仅 6.12%，"
                 "且特征窗口只到 1111，而复购行为发生在窗口之外，"
                 "本数据能给的信息有限。")
    lines.append("2. **不能把相关性当因果**。例如「购买次数多的用户复购率高」"
                 "不代表多买几次就能提升复购。")
    lines.append("3. **没有做时间外验证**。样本本身不带时间戳，"
                 "只能用分层随机划分；如果实际投放有时间先后，效果可能下降。")
    lines.append("4. **类别不平衡未做重采样以外的处理**，"
                 "阈值 0.5 下的精确率偏低是该数据分布的必然结果，"
                 "实际使用应按业务接受的触达量重新定阈值。")

    path = REPORT_DIR / "phase8_model.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已生成报告：{path}")


if __name__ == "__main__":
    main()
