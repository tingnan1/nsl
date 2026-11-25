"""
交付物一致性测试（不需要数据库，CI 可跑）

为什么需要：
    outputs/reports/ 下的四份报告和 README 是人工复核过的交付物，里面的关键数字
    都经过至少两套实现交叉验证。但它们也是**生成物**——只要有人用不完整的数据
    重新跑一遍脚本再提交，数字就会被悄悄改掉，而且不会有任何报错。

    这组测试把「已核验的关键数字」钉成断言：一旦报告里的核心数值变了，
    CI 立刻失败，逼迫改动者解释为什么变。
"""

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORTS = ROOT / "outputs" / "reports"
MODELS = ROOT / "outputs" / "models"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# 每份报告必须出现的核心数字：
#   事实表行数、去重用户数、有效窗口日均、复购率、模型指标
EXPECTED = {
    "phase5_sql_analysis.md": ["54,925,330"],
    "phase6_summary.md": ["54,925,330", "424,170", "296,893"],
    "phase7_metrics.md": ["424,170", "6.12"],
    "phase8_model.md": ["0.6544", "0.1196", "260,864", "0.05612", "0.6595"],
}


@pytest.mark.parametrize("filename,needles", EXPECTED.items())
def test_report_contains_verified_numbers(filename, needles):
    path = REPORTS / filename
    assert path.exists(), f"缺少报告 {filename}"
    text = read(path)
    missing = [n for n in needles if n not in text]
    assert not missing, f"{filename} 缺少已核验数值：{missing}"


def test_readme_states_core_scale():
    """README 是对外门面，核心规模数字必须与实测一致。"""
    text = read(ROOT / "README.md")
    for needle in ("54,925,330", "424,170", "0.6544", "0.6595"):
        assert needle in text, f"README 缺少 {needle}"


def test_model_metadata_matches_report():
    """模型元信息与实际报告必须说同一件事，避免 artifact 与文档各说各话。"""
    meta = json.loads(read(MODELS / "repurchase_model_meta.json"))
    report = read(REPORTS / "phase8_model.md")

    assert meta["n_train_rows"] == 260_864
    assert meta["n_features"] == 17
    assert len(meta["features"]) == meta["n_features"]
    assert f"{meta['holdout_metrics_at_0_5']['ROC_AUC']:.4f}" in report
    assert "全量训练集" in meta["training_scope"]


def test_model_metadata_declares_both_thresholds():
    """
    元信息必须同时给出 0.5 阈值与按正例率定阈值的两套指标。
    只给 0.5 阈值会让人误以为精确率为 0 的模型毫无价值。
    """
    meta = json.loads(read(MODELS / "repurchase_model_meta.json"))
    assert "holdout_metrics_at_0_5" in meta
    assert "holdout_metrics_at_rate_threshold" in meta
    assert meta["holdout_metrics_at_rate_threshold"]["精确率"] > 0.1
    assert "threshold_note" in meta


def test_bi_documentation_covers_effective_window():
    """BI 文档必须写明日均要过滤 effective，否则 Power BI 会重复踩同一个坑。"""
    text = read(ROOT / "docs" / "bi_export.md")
    assert "effective" in text
    assert "296,893" in text or "296893" in text
