"""
Phase 13：FastAPI 接口冒烟测试

设计原则：
    1. **不 mock 数据库**。测试直接打真实 MySQL，因为要验证的就是「接口 → SQL → 数据」这条链路。
    2. **断言绑定已验证的真实数字**。下面这些常量全部来自 Phase 4 的 19 项校验、
       Phase 5 的 SQL 分析与 Phase 6/7 的 pandas 计算，三套实现互相验证过。
       一旦改动破坏了数据链路，测试会立刻失败，而不是悄悄返回错数据。
    3. **数据库不可用时 skip 而不是 fail**。环境问题不该被误判成代码回归。

运行：
    .\\.venv\\Scripts\\python.exe -m pytest tests -v
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app

# 整个模块都是集成测试：需要真实 MySQL 与已导入的数据。
# CI 里用 `pytest -m "not integration"` 跳过，只跑单元测试。
pytestmark = pytest.mark.integration

# 不进入 lifespan：避免启动时后台预热线程与测试抢数据库连接。
client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def require_live_database():
    """数据库或数据不完整时整模块跳过。"""
    try:
        resp = client.get("/api/health")
    except Exception as exc:  # 连不上时 TestClient 会抛异常
        pytest.skip(f"接口不可达，跳过集成测试：{exc}")
    if resp.status_code != 200:
        pytest.skip(f"数据库不可用，跳过集成测试：{resp.text[:200]}")
    if not resp.json().get("db_connected"):
        pytest.skip("数据库未连接，跳过集成测试")


# ---- 已由三套实现交叉验证过的真实数字 ----
EXPECTED_TOTAL_EVENTS = 54_925_330
EXPECTED_TOTAL_USERS = 424_170
EXPECTED_TRAIN_ROWS = 260_864
EXPECTED_TEST_ROWS = 261_477
EXPECTED_ACTIONS = {
    0: (48_550_713, 423_862),
    1: (76_750, 31_044),
    2: (3_292_144, 424_170),
    3: (3_005_723, 233_428),
}


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["db_connected"] is True
    assert data["table_count"] >= 9          # 9 张主表 + 3 张物化表
    assert data["model_loaded"] is True
    assert data["model_features"] == 17


def test_overview():
    data = client.get("/api/overview").json()
    assert data["total_events"] == EXPECTED_TOTAL_EVENTS
    assert data["total_users"] == EXPECTED_TOTAL_USERS
    assert data["train_rows"] == EXPECTED_TRAIN_ROWS
    assert data["test_rows"] == EXPECTED_TEST_ROWS
    assert data["days"] == 186
    assert data["first_date"] == 511
    assert data["last_date"] == 1112


def test_overview_effective_window():
    """
    1112 只有 46 行、0 次购买，是数据截断的尾巴。
    日均必须按 185 天的有效窗口算，而不是 186 天——否则会被这条尾巴拉低。
    含 1112 是 295,297，排除后是 296,893。
    """
    data = client.get("/api/overview").json()
    assert data["days"] == 186
    assert data["effective_days"] == 185
    assert data["avg_daily_events"] == 296_893
    assert data["avg_daily_events"] == round(
        (EXPECTED_TOTAL_EVENTS - 46) / 185)
    assert data["avg_daily_purchases"] == pytest.approx(17_795.4, abs=0.1)


def test_daily_marks_truncated_tail():
    rows = client.get("/api/daily?start=1111&end=1112").json()["rows"]
    by_date = {r["date_key"]: r for r in rows}
    assert by_date[1111]["effective"] == 1
    assert by_date[1112]["effective"] == 0
    assert by_date[1112]["events"] == 46
    assert by_date[1112]["purchases"] == 0


def test_actions():
    rows = client.get("/api/actions").json()["rows"]
    assert len(rows) == 4
    for row in rows:
        expected_events, expected_users = EXPECTED_ACTIONS[row["action_type"]]
        assert row["events"] == expected_events, row["action_name"]
        assert row["users"] == expected_users, row["action_name"]


def test_daily_known_values():
    rows = client.get("/api/daily?start=1110&end=1111").json()["rows"]
    assert len(rows) == 2
    by_date = {r["date_key"]: r for r in rows}
    assert by_date[1110]["events"] == 2_940_826
    assert by_date[1110]["purchases"] == 17_249
    assert by_date[1111]["events"] == 10_582_633
    assert by_date[1111]["purchases"] == 1_223_354
    # 1111 的 UV 等于全量用户（这份数据全是 11.11 买家）
    assert by_date[1111]["uv"] == EXPECTED_TOTAL_USERS


def test_double11_multiples():
    data = client.get("/api/double11").json()
    assert data["day_events"] == 10_582_633
    assert data["day_purchases"] == 1_223_354
    assert data["events_multiple"] == pytest.approx(8.97, abs=0.01)
    assert data["purchases_multiple"] == pytest.approx(88.98, abs=0.01)
    assert data["uv_multiple"] == pytest.approx(4.43, abs=0.01)


def test_segments_repurchase_rates():
    rows = client.get("/api/segments").json()["rows"]
    assert len(rows) == 2
    by_name = {r["segment"]: r for r in rows}
    old = by_name["1111前有购买（老客）"]
    new = by_name["1111前无购买（新客）"]
    assert old["samples"] == 211_874
    assert old["repurchase"] == 13_553
    assert old["repurchase_rate"] == pytest.approx(6.40, abs=0.01)
    assert new["samples"] == 48_990
    assert new["repurchase"] == 2_399
    assert new["repurchase_rate"] == pytest.approx(4.90, abs=0.01)


def test_top_categories_pareto():
    data = client.get("/api/top-categories?limit=10").json()
    assert data["total_purchases"] == 3_292_144
    assert data["category_count"] == 1_324
    rows = data["rows"]
    assert len(rows) == 10
    assert rows[0]["cat_id"] == 1213
    assert rows[0]["purchases"] == 95_353
    # 单调不增
    purchases = [r["purchases"] for r in rows]
    assert purchases == sorted(purchases, reverse=True)
    # 帕累托累计单调递增，且第 10 名累计约 23.21%
    cumulative = [r["cumulative_pct"] for r in rows]
    assert cumulative == sorted(cumulative)
    assert cumulative[-1] == pytest.approx(23.21, abs=0.05)


def test_top_sellers():
    data = client.get("/api/top-sellers?limit=10").json()
    assert data["seller_count"] == 4_995
    rows = data["rows"]
    assert rows[0]["seller_id"] == 3828
    assert rows[0]["purchases"] == 18_877
    assert rows[0]["buyers"] == 11_093


def test_merchant_health_excludes_small_samples():
    rows = client.get("/api/merchant-health?min_samples=30&limit=100").json()["rows"]
    assert rows
    assert all(r["samples"] >= 30 for r in rows)
    rates = [r["repurchase_rate"] for r in rows]
    assert rates == sorted(rates, reverse=True)


def test_model_info():
    data = client.get("/api/model/info").json()
    assert data["model_type"] == "HistGradientBoostingClassifier"
    assert data["feature_count"] == 17
    assert len(data["features"]) == 17
    # 常量特征必须已被剔除
    for dropped in ("p_has_buy", "p_last_buy_recency", "p_last_recency", "p_events"):
        assert dropped not in data["features"]
    assert data["metrics"]["roc_auc_holdout"] == pytest.approx(0.6544, abs=1e-4)


def test_predict_known_pair():
    resp = client.post("/api/predict", json={"user_id": 34176, "merchant_id": 4356})
    assert resp.status_code == 200
    data = resp.json()
    assert 0.0 <= data["repurchase_probability"] <= 1.0
    # 该样本在 train 中真实 label=1，p_buys=6。
    # 注意：这个数值绑定的是「全量训练集拟合」版本的 artifact。
    # 重新训练模型后必须同步更新这里的期望值（见 docs/PROJECT_REPORT.md 的产物说明）。
    assert data["repurchase_probability"] == pytest.approx(0.153984, abs=0.002)
    assert data["cold_start"] is False
    assert data["features"]["p_buys"] == 6
    assert set(data["features"]) == set(client.get("/api/model/info").json()["features"])


def test_predict_orders_by_purchase_count():
    """
    理性检查：对同一商家，买过 6 次的用户概率应当高于从没买过的用户。
    这不是在验证模型"正确"，而是在验证特征确实进了模型、没有接错列。
    """
    bought = client.post(
        "/api/predict", json={"user_id": 34176, "merchant_id": 4356}).json()
    never = client.post(
        "/api/predict", json={"user_id": 1, "merchant_id": 3828}).json()
    assert bought["features"]["p_buys"] > never["features"]["p_buys"]
    assert bought["repurchase_probability"] > never["repurchase_probability"]


def test_predict_rejects_out_of_range():
    # pydantic 的边界约束必须生效
    assert client.post("/api/predict", json={"user_id": 0, "merchant_id": 4356}).status_code == 422
    assert client.post("/api/predict", json={"user_id": 34176, "merchant_id": 999_999}).status_code == 422


def test_sample_pairs_filter():
    rows = client.get("/api/sample-pairs?label=1&limit=5").json()["rows"]
    assert len(rows) == 5
    assert all(r["label"] == 1 for r in rows)


# 静态前端路由（首页、未知 /api 404、SPA 兜底）不需要数据库，
# 放在 tests/test_units.py 里，CI 也能覆盖。
