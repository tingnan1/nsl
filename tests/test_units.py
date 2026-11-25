"""
Phase 13：不需要数据库的单元测试

这一组测试**不连 MySQL**，CI 里可以直接跑（`pytest -m "not integration"`）。
它们保护的是最容易静默出错、而且一错就全错的地方：

    1. clean() 的类型转换——pymysql 返回 Decimal，转错会让 JSON 里出现字符串数字
    2. 特征列表的来源与顺序——sklearn 按列位置读特征，顺序错了不报错，只给错预测
    3. 静态前端的路由行为——SPA 兜底不能吞掉 /api
"""

import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api.main import app

client = TestClient(app)


# ---------------------------------------------------------------------
# clean()：MySQL 驱动类型 → JSON 友好类型
# ---------------------------------------------------------------------
def test_clean_decimal_integer_becomes_int():
    """SUM()/COUNT() 返回 Decimal('1223354')，必须是 int 而不是 "1223354"。"""
    value = main.clean(Decimal("1223354"))
    assert value == 1223354
    assert isinstance(value, int)


def test_clean_decimal_fraction_becomes_float():
    value = main.clean(Decimal("6.40"))
    assert value == pytest.approx(6.40)
    assert isinstance(value, float)


def test_clean_passthrough_types():
    assert main.clean(None) is None
    assert main.clean(True) is True
    assert main.clean(3) == 3
    assert main.clean("x") == "x"


def test_clean_datetime_is_isoformat():
    assert main.clean(datetime.date(2026, 11, 11)) == "2026-11-11"
    assert main.clean(datetime.datetime(2026, 11, 11, 8, 30)) == "2026-11-11T08:30:00"


# ---------------------------------------------------------------------
# 特征列表：顺序与内容
# ---------------------------------------------------------------------
def test_feature_count_is_17():
    assert len(main.FEATURES) == 17
    assert main.FEATURE_SOURCE != ""


def test_feature_order_matches_fallback():
    """
    这条最关键的：从 features_train.csv 表头推导出的顺序，必须与内置兜底列表
    逐项一致。任何一边改动而另一边没同步，这里就会红——
    否则 sklearn 会按位置读特征，静默给出错误预测。
    """
    assert main.FEATURES == main.FALLBACK_FEATURES


def test_degenerate_features_are_excluded():
    """三个零方差特征 + 一个完全共线特征必须不在特征列表里。"""
    for dropped in ("p_has_buy", "p_last_buy_recency", "p_last_recency", "p_events"):
        assert dropped not in main.FEATURES
    for identifier in ("user_id", "merchant_id", "label"):
        assert identifier not in main.FEATURES


# ---------------------------------------------------------------------
# 静态前端路由（不查数据库）
# ---------------------------------------------------------------------
def test_frontend_index_served():
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<div id="app">' in resp.text


def test_unknown_api_path_returns_404():
    """SPA 兜底路由不能把 /api/* 也吞掉，否则错误路径会静默返回页面。"""
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "接口不存在"


def test_spa_fallback_for_deep_link():
    resp = client.get("/some/deep/link")
    assert resp.status_code == 200
    assert '<div id="app">' in resp.text
