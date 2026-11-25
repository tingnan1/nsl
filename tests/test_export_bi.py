"""
BI 导出逻辑的单元测试（不需要数据库）

`export_bi.py` 整体依赖数据库，但「每日宽表」的拼装是纯函数：
日期补零、购买率、人均条数、effective 合并。这几处一旦算错不会报错，
只会让 Power BI 看板上的数字静默偏差——所以单独拿出来测。
"""

import pandas as pd
import pytest

from python.export_bi import build_daily_table


@pytest.fixture
def daily():
    return pd.DataFrame({
        "date_key": [511, 1111, 1112],
        "clicks": [0, 8_000_000, 30],
        "carts": [62, 500_000, 10],
        "favs": [10_385, 120_000, 5],
        "purchases": [9_597, 1_223_354, 0],
        "events": [20_044, 10_582_633, 46],
    })


@pytest.fixture
def uv():
    return pd.DataFrame({
        "date_key": [511, 1111, 1112],
        "uv": [11_476, 424_170, 45],
        "effective": [1, 1, 0],
    })


@pytest.fixture
def dim_date():
    return pd.DataFrame({
        "date_key": [511, 1111, 1112],
        "month": [5, 11, 11],
        "day": [11, 11, 12],
        "day_of_year": [131, 315, 316],
        "is_weekend": [None, None, None],
    })


def test_mmdd_is_zero_padded_four_digits(daily, uv, dim_date):
    """511 必须变成 "0511"，否则按字符串排序时 1101 会排在 511 前面。"""
    out = build_daily_table(daily, uv, dim_date)
    assert out["mmdd"].tolist() == ["0511", "1111", "1112"]
    assert all(len(x) == 4 for x in out["mmdd"])


def test_purchase_rate_is_percent_not_ratio(daily, uv, dim_date):
    """购买率是百分数（乘 100），不是 0~1 的比例。"""
    out = build_daily_table(daily, uv, dim_date).set_index("date_key")
    # 1111: 1,223,354 / 10,582,633 * 100 = 11.561
    assert out.loc[1111, "purchase_rate_pct"] == pytest.approx(11.561, abs=0.01)
    assert out.loc[511, "purchase_rate_pct"] == pytest.approx(47.880, abs=0.01)


def test_events_per_user_uses_daily_uv(daily, uv, dim_date):
    out = build_daily_table(daily, uv, dim_date).set_index("date_key")
    # 1111: 10,582,633 / 424,170 = 24.95
    assert out.loc[1111, "events_per_user"] == pytest.approx(24.95, abs=0.01)


def test_effective_flag_survives_the_join(daily, uv, dim_date):
    """
    1112 必须标记为 effective = 0。
    这一列丢了的话，Power BI 做日均就会把 46 行的一天算进去。
    """
    out = build_daily_table(daily, uv, dim_date).set_index("date_key")
    assert out.loc[1111, "effective"] == 1
    assert out.loc[1112, "effective"] == 0


def test_column_order_is_stable(daily, uv, dim_date):
    """列顺序是下游（Power BI / Excel 看板）按名取列的前提，不能随意变。"""
    out = build_daily_table(daily, uv, dim_date)
    assert list(out.columns) == [
        "date_key", "mmdd", "month", "day", "day_of_year", "events", "uv",
        "clicks", "carts", "favs", "purchases", "purchase_rate_pct",
        "events_per_user", "effective",
    ]


def test_date_dimensions_are_joined(daily, uv, dim_date):
    out = build_daily_table(daily, uv, dim_date).set_index("date_key")
    assert out.loc[1111, "month"] == 11
    assert out.loc[1111, "day"] == 11
    assert out.loc[1111, "day_of_year"] == 315
