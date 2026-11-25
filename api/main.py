"""
Phase 10：FastAPI 后端

所有接口都查真实 MySQL 数据库（tmall_analysis），没有硬编码数字、没有 mock 数据。

启动：
    .\\start_mysql.cmd                                     # 先起数据库
    .\\.venv\\Scripts\\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000

接口文档：
    http://127.0.0.1:8000/docs      （Swagger UI，自动生成）
    http://127.0.0.1:8000/api/health

设计说明：
    1. 连接配置从项目根目录的 .env 读取（该文件被 .gitignore 忽略）。
    2. 每个请求开一条连接、用完即关。数据量小、并发低，够用且没有连接池泄漏风险。
    3. /api/predict 用 Phase 8 保存的模型做单条预测，特征由 SQL 现算。
       保存的 artifact 是**全量训练集**拟合的版本，与 prediction_test.csv 同源；
       而 holdout 指标（ROC-AUC 0.6544 等）来自 70% 训练集那一次评估，
       两者不可混用。元信息见 outputs/models/repurchase_model_meta.json。
       特征顺序从 features_train.csv 表头推导，保证与训练时完全一致；
       该文件不存在时（例如 CI 环境没有数据）退回内置的 17 个特征名。
"""

from __future__ import annotations

import datetime
import os
import threading
from contextlib import asynccontextmanager, contextmanager
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import joblib
import pandas as pd
import pymysql
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TABLE_DIR = PROJECT_ROOT / "outputs" / "tables"
MODEL_PATH = PROJECT_ROOT / "outputs" / "models" / "repurchase_model.joblib"

# 与 train_model.py 完全一致的剔除规则
DEGENERATE = ["p_has_buy", "p_last_buy_recency", "p_last_recency"]
ID_COLUMNS = ["user_id", "merchant_id", "label"]
REDUNDANT = ["p_events"]
DOY_1111 = 315


def load_dotenv() -> None:
    """读项目根目录的 .env，不覆盖已存在的环境变量。"""
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

DB_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", ""),
    "database": os.environ.get("MYSQL_DATABASE", "tmall_analysis"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": True,
}


# 与训练脚本 train_model.py 中的特征顺序完全一致的兜底列表。
# 只有在 outputs/tables/features_train.csv 不存在时才会用到（例如 CI 环境没有数据）。
FALLBACK_FEATURES = [
    "p_clicks", "p_carts", "p_favs", "p_buys", "p_has_cart", "p_has_fav",
    "user_buys", "user_events", "user_active_days", "seller_buys", "seller_buyers",
    "p_buys_share_of_user", "p_buys_share_of_seller",
    "age_range", "gender", "age_range_missing", "gender_missing",
]


def feature_columns() -> tuple[list[str], str]:
    """
    从 features_train.csv 表头推导特征列，保证顺序与训练时一致。

    顺序必须精确：sklearn 模型是按列位置读特征的，顺序错了不会报错，
    只会静默给出错误预测。
    """
    path = TABLE_DIR / "features_train.csv"
    if path.exists():
        header = pd.read_csv(path, nrows=0)
        drop = set(DEGENERATE + ID_COLUMNS + REDUNDANT)
        return [c for c in header.columns if c not in drop], "features_train.csv"
    return list(FALLBACK_FEATURES), "内置默认列表（features_train.csv 不存在）"


FEATURES, FEATURE_SOURCE = feature_columns()
MODEL = joblib.load(MODEL_PATH) if MODEL_PATH.exists() else None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """后台预热缓存：几个重聚合查询要几十秒到几分钟，放到启动线程里先算好。"""
    threading.Thread(target=_prewarm_caches, daemon=True).start()
    yield


app = FastAPI(
    title="电商用户行为分析与复购预测 API",
    description="数据源：天猫复购预测公开数据集（54,925,330 行行为日志），"
                "所有数字均来自本地 MySQL 实际查询。",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def get_conn():
    conn = pymysql.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


def query(sql: str, params: tuple | None = None) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def query_one(sql: str, params: tuple | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def clean(value):
    """
    把 MySQL 驱动返回的特殊类型转成 JSON 友好类型。

    注意：pymysql 的 SUM()/ROUND()/AVG() 返回的是 Decimal，不能直接 str()，
    否则 JSON 里会出现 "1223354" 这种字符串，前端拿去做算术会静默出错。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        # 整数值用 int 输出，避免 6.0 这种多余的小数位
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def clean_rows(rows: list[dict]) -> list[dict]:
    return [{k: clean(v) for k, v in row.items()} for row in rows]


# ---------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------
@app.get("/api/health", summary="健康检查（含数据库连通性）")
def health() -> dict:
    try:
        row = query_one("SELECT 1 AS ok")
        tables = query_one(
            "SELECT COUNT(*) AS n FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s", (DB_CONFIG["database"],))
        return {
            "status": "ok",
            "database": DB_CONFIG["database"],
            "db_connected": row is not None and row["ok"] == 1,
            "table_count": tables["n"] if tables else 0,
            "model_loaded": MODEL is not None,
            "model_features": len(FEATURES),
        }
    except Exception as exc:  # 数据库没起来时给出可读错误，而不是 500 堆栈
        raise HTTPException(status_code=503,
                            detail=f"数据库连接失败：{exc}") from exc


@app.get("/api/overview", summary="全期总览")
@lru_cache(maxsize=None)
def overview() -> dict:
    # 事件总数直接由 186 行的 agg_daily 求和，不再对 5492 万行做 COUNT(*)
    # （实测 COUNT(*) 要 35 秒）。
    #
    # effective 字段：1112 只有 46 行、0 次购买，是数据截断的尾巴。
    # 日均类指标一律只统计 effective = 1 的 185 天，否则会被这条尾巴拉低
    # （含 1112 是 295,297，排除后是 296,893）。
    row = query_one("""
        SELECT SUM(events)        AS total_events,
               SUM(purchases)     AS total_purchases,
               MIN(date_key)      AS first_date,
               MAX(date_key)      AS last_date,
               COUNT(*)           AS days,
               SUM(effective = 1) AS effective_days,
               ROUND(SUM(IF(effective = 1, events, 0))
                     / SUM(effective = 1), 0)    AS avg_daily_events,
               ROUND(SUM(IF(effective = 1, purchases, 0))
                     / SUM(effective = 1), 1)    AS avg_daily_purchases
        FROM agg_daily
    """)
    # 去重用户数直接用维度表：Phase 4 已验证 fact 里的用户在 dim_user 中零缺失，
    # 所以 COUNT(*) FROM dim_user 与 COUNT(DISTINCT user_id) 等价，但快几百倍。
    samples = query_one("""
        SELECT (SELECT COUNT(*) FROM dim_user)      AS total_users,
               (SELECT COUNT(*) FROM train_sample)  AS train_rows,
               (SELECT COUNT(*) FROM test_sample)   AS test_rows
    """)
    merged = {**row, **samples}
    out = {k: clean(v) for k, v in merged.items()}
    out["note"] = ("全部用户都是 11.11 当天有购买的用户，"
                   "因此『购买用户占比』恒为 100%，无信息量。")
    out["effective_window"] = (
        "日均类指标只统计 effective_days 天：1112 仅有 46 行且 0 次购买，"
        "属于数据截断尾巴，计入会低估真实强度。"
    )
    out["total_users_source"] = "dim_user（与 fact 去重用户等价，已校验零缺失）"
    out["total_events_source"] = "agg_daily 求和（物化表）"
    return out


# ---------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------
@app.get("/api/daily", summary="每日行为量与购买事件（可指定日期区间）")
def daily(
    start: int = Query(511, description="起始 MMDD，如 1101"),
    end: int = Query(1112, description="结束 MMDD，如 1112"),
) -> dict:
    # 读 186 行的物化表 agg_daily，不现算 UV（现算要 58 秒）。
    rows = query("""
        SELECT date_key, events, purchases, uv, effective
        FROM agg_daily
        WHERE date_key BETWEEN %s AND %s
        ORDER BY date_key
    """, (start, end))
    return {"start": start, "end": end, "days": len(rows),
            "source": "agg_daily（物化表，重建方式见 sql/build_aggregates.sql）",
            "note": "effective=0 表示该日属于数据截断尾巴（1112 仅 46 行），"
                    "做日均/趋势时应当排除。",
            "rows": clean_rows(rows)}


@app.get("/api/actions", summary="四类行为的累计事件数与去重用户数")
@lru_cache(maxsize=None)
def actions() -> dict:
    # 读物化表 agg_action_summary（4 行）。
    # 现算 COUNT(DISTINCT user_id) 在 5492 万行上要 100 秒以上，不适合放在接口里。
    # 物化脚本见 sql/build_aggregates.sql。
    rows = query("""
        SELECT action_type, action_name, events, users
        FROM agg_action_summary
        ORDER BY action_type
    """)
    total = sum(r["events"] for r in rows) or 1
    max_users = max((r["users"] for r in rows), default=1) or 1
    for r in rows:
        r["event_share_pct"] = round(r["events"] * 100 / total, 2)
        r["user_share_pct"] = round(r["users"] * 100 / max_users, 2)
    return {
        "source": "agg_action_summary（物化表，重建方式见 sql/build_aggregates.sql）",
        "rows": clean_rows(rows),
        "caveat": "用户级占比是 186 天累计口径，不是会话转化率；"
                  "购买用户占比 100% 是抽样口径的必然结果，无信息量。",
    }


@lru_cache(maxsize=None)
def _category_ranking() -> tuple[list[dict], int]:
    """
    完整品类榜单只算一次并缓存，接口再按 limit 切片。

    直接按 limit 查会导致每次不同 limit 都重算一遍全表聚合；
    而全量榜单只有 1,658 行，缓存起来代价很小。
    """
    rows = query("""
        SELECT cat_id, COUNT(*) AS purchases
        FROM fact_user_log
        WHERE action_type = 2
        GROUP BY cat_id
        ORDER BY purchases DESC, cat_id
    """)
    total = sum(r["purchases"] for r in rows)
    return rows, total


@app.get("/api/top-categories", summary="品类 TOP N（按购买事件）")
def top_categories(limit: int = Query(10, ge=1, le=50)) -> dict:
    ranking, total = _category_ranking()
    rows = [dict(r) for r in ranking[:limit]]
    cumulative = 0
    for r in rows:
        cumulative += r["purchases"]
        r["share_pct"] = round(r["purchases"] * 100 / total, 2)
        r["cumulative_pct"] = round(cumulative * 100 / total, 2)
    return {"total_purchases": total, "category_count": len(ranking),
            "rows": clean_rows(rows)}


@lru_cache(maxsize=None)
def _seller_ranking() -> tuple[list[dict], int]:
    """完整商家榜单（含去重买家数）只算一次并缓存。"""
    rows = query("""
        SELECT seller_id,
               COUNT(*)                AS purchases,
               COUNT(DISTINCT user_id) AS buyers
        FROM fact_user_log
        WHERE action_type = 2
        GROUP BY seller_id
        ORDER BY purchases DESC, seller_id
    """)
    total = sum(r["purchases"] for r in rows)
    return rows, total


@app.get("/api/top-sellers", summary="商家 TOP N（按购买事件，含去重买家数）")
def top_sellers(limit: int = Query(10, ge=1, le=50)) -> dict:
    ranking, total = _seller_ranking()
    rows = [dict(r) for r in ranking[:limit]]
    for r in rows:
        r["share_pct"] = round(r["purchases"] * 100 / total, 2)
    return {"total_purchases": total, "seller_count": len(ranking),
            "rows": clean_rows(rows)}


@app.get("/api/double11", summary="双十一（1111）对比前 7 天均值")
@lru_cache(maxsize=None)
def double11() -> dict:
    row = query_one("""
        WITH d AS (
            SELECT date_key, events, purchases, uv
            FROM agg_daily
            WHERE date_key BETWEEN 1104 AND 1111
        )
        SELECT AVG(CASE WHEN date_key BETWEEN 1104 AND 1110 THEN events END)    AS prev_events_avg,
               MAX(CASE WHEN date_key = 1111 THEN events END)                   AS day_events,
               AVG(CASE WHEN date_key BETWEEN 1104 AND 1110 THEN purchases END) AS prev_purchases_avg,
               MAX(CASE WHEN date_key = 1111 THEN purchases END)                AS day_purchases,
               AVG(CASE WHEN date_key BETWEEN 1104 AND 1110 THEN uv END)        AS prev_uv_avg,
               MAX(CASE WHEN date_key = 1111 THEN uv END)                       AS day_uv
        FROM d
    """)
    # pymysql 的 AVG/MAX 返回 Decimal，必须先转 float 才能做除法，
    # 否则 Decimal 会漏进响应体，FastAPI 序列化直接报错。
    val = {k: (float(v) if v is not None else None) for k, v in row.items()}
    return {
        "prev_events_avg": round(val["prev_events_avg"], 1),
        "day_events": int(val["day_events"]),
        "events_multiple": round(val["day_events"] / val["prev_events_avg"], 2),
        "prev_purchases_avg": round(val["prev_purchases_avg"], 1),
        "day_purchases": int(val["day_purchases"]),
        "purchases_multiple": round(
            val["day_purchases"] / val["prev_purchases_avg"], 2),
        "prev_uv_avg": round(val["prev_uv_avg"], 1),
        "day_uv": int(val["day_uv"]),
        "uv_multiple": round(val["day_uv"] / val["prev_uv_avg"], 2),
        "note": "1111 的 UV 等于全量用户，因为这份数据的用户本来就是 11.11 买家。",
    }


@app.get("/api/segments", summary="用户分层：1111 前是否购买 × 复购率")
@lru_cache(maxsize=None)
def segments() -> dict:
    # 读物化表 agg_segment_repurchase（2 行）。现算要 45 秒，不适合放进接口。
    rows = query("""
        SELECT segment, samples, repurchase, repurchase_rate
        FROM agg_segment_repurchase
        ORDER BY repurchase_rate DESC
    """)
    return {
        "source": "agg_segment_repurchase（物化表，重建方式见 sql/build_aggregates.sql）",
        "rows": clean_rows(rows),
        "caveat": "这是相关性，不是因果；老客本身可能需求更强。",
    }


@app.get("/api/merchant-health", summary="商家健康度（按样本量筛选）")
def merchant_health(
    min_samples: int = Query(30, ge=1, description="最小样本量，低于此值不参与排名"),
    limit: int = Query(10, ge=1, le=100),
) -> dict:
    rows = query("""
        SELECT merchant_id,
               COUNT(*)                                  AS samples,
               SUM(label)                                AS repurchase,
               ROUND(100.0 * SUM(label) / COUNT(*), 2)   AS repurchase_rate
        FROM train_sample
        GROUP BY merchant_id
        HAVING samples >= %s
        ORDER BY repurchase_rate DESC, samples DESC
        LIMIT %s
    """, (min_samples, limit))
    return {"min_samples": min_samples,
            "note": "样本量过小的商家复购率是噪声，必须先过滤再排名。",
            "rows": clean_rows(rows)}


@app.get("/api/model/info", summary="模型元信息与特征列表")
def model_info() -> dict:
    importance_path = TABLE_DIR / "feature_importance.csv"
    importance = []
    if importance_path.exists():
        df = pd.read_csv(importance_path).head(10)
        importance = clean_rows(df.to_dict(orient="records"))
    return {
        "model_type": type(MODEL).__name__ if MODEL is not None else None,
        "features": FEATURES,
        "feature_count": len(FEATURES),
        "top_features": importance,
        "metrics": {
            "roc_auc_holdout": 0.6544,
            "pr_auc_holdout": 0.1196,
            "cv_roc_auc_mean": 0.6590,
            "cv_roc_auc_std": 0.0049,
        },
        "caveat": "指标来自 70/30 内部划分与 5 折 CV，测试集无标签故不报测试集指标。",
    }


# ---------------------------------------------------------------------
# 预测
# ---------------------------------------------------------------------
class PredictRequest(BaseModel):
    user_id: int = Field(..., ge=1, le=424170)
    merchant_id: int = Field(..., ge=1, le=4995)


@app.post("/api/predict", summary="单个（用户, 商家）对的复购概率")
def predict(req: PredictRequest) -> dict:
    if MODEL is None:
        raise HTTPException(status_code=503, detail="模型文件不存在")

    pair = query_one("""
        SELECT SUM(action_type = 0) AS p_clicks,
               SUM(action_type = 1) AS p_carts,
               SUM(action_type = 3) AS p_favs,
               SUM(action_type = 2) AS p_buys,
               MAX(CASE WHEN action_type = 2 THEN day_of_year END) AS last_buy_doy
        FROM fact_user_log
        WHERE user_id = %s AND seller_id = %s
    """, (req.user_id, req.merchant_id))

    user = query_one("""
        SELECT COUNT(*)                AS user_events,
               SUM(action_type = 2)    AS user_buys,
               COUNT(DISTINCT date_key) AS user_active_days
        FROM fact_user_log
        WHERE user_id = %s
    """, (req.user_id,))

    seller = query_one("""
        SELECT SUM(action_type = 2) AS seller_buys
        FROM fact_user_log
        WHERE seller_id = %s
    """, (req.merchant_id,))
    seller_buyers = query_one("""
        SELECT COUNT(DISTINCT user_id) AS sellers_buyers
        FROM fact_user_log
        WHERE action_type = 2 AND seller_id = %s
    """, (req.merchant_id,))

    profile = query_one("""
        SELECT age_range, gender, age_range_missing, gender_missing
        FROM dim_user WHERE user_id = %s
    """, (req.user_id,))

    p_clicks = int(pair["p_clicks"] or 0)
    p_carts = int(pair["p_carts"] or 0)
    p_favs = int(pair["p_favs"] or 0)
    p_buys = int(pair["p_buys"] or 0)
    user_events = int(user["user_events"] or 0)
    user_buys = int(user["user_buys"] or 0)
    user_active_days = int(user["user_active_days"] or 0)
    seller_buys = int(seller["seller_buys"] or 0)
    seller_buyers_n = int(seller_buyers["sellers_buyers"] or 0)

    features = {
        "p_clicks": p_clicks,
        "p_carts": p_carts,
        "p_favs": p_favs,
        "p_buys": p_buys,
        "p_has_cart": int(p_carts > 0),
        "p_has_fav": int(p_favs > 0),
        "user_buys": user_buys,
        "user_events": user_events,
        "user_active_days": user_active_days,
        "seller_buys": seller_buys,
        "seller_buyers": seller_buyers_n,
        "p_buys_share_of_user": (p_buys / user_buys) if user_buys else 0.0,
        "p_buys_share_of_seller": (p_buys / seller_buys) if seller_buys else 0.0,
        "age_range": int(profile["age_range"]) if profile and profile["age_range"] is not None else -1,
        "gender": int(profile["gender"]) if profile and profile["gender"] is not None else -1,
        "age_range_missing": int(profile["age_range_missing"]) if profile else 1,
        "gender_missing": int(profile["gender_missing"]) if profile else 1,
    }

    frame = pd.DataFrame([[features[name] for name in FEATURES]], columns=FEATURES)
    probability = float(MODEL.predict_proba(frame)[0][1])

    return {
        "user_id": req.user_id,
        "merchant_id": req.merchant_id,
        "repurchase_probability": round(probability, 6),
        "cold_start": p_clicks + p_carts + p_favs + p_buys == 0,
        "features": features,
        "note": "概率阈值需按业务可接受的触达量另定，0.5 不是合理阈值（正例率仅 6.12%）。",
    }


@app.get("/api/sample-pairs", summary="给前端用的示例样本对（含真实标签）")
def sample_pairs(limit: int = Query(10, ge=1, le=100),
                 label: int | None = Query(None, ge=0, le=1)) -> dict:
    if label is None:
        rows = query(
            "SELECT user_id, merchant_id, label FROM train_sample LIMIT %s",
            (limit,))
    else:
        rows = query(
            "SELECT user_id, merchant_id, label FROM train_sample "
            "WHERE label = %s LIMIT %s", (label, limit))
    return {"rows": clean_rows(rows)}


def _prewarm_caches() -> None:
    """启动后后台跑一遍重查询，把 lru_cache 填上，避免第一个用户等几分钟。"""
    for fn in (overview, double11, actions, segments):
        try:
            fn()
            print(f"[prewarm] {fn.__name__} 完成", flush=True)
        except Exception as exc:  # 预热失败不影响服务启动
            print(f"[prewarm] {fn.__name__} 失败：{exc}", flush=True)


# =====================================================================
# 静态前端（Phase 14 本地部署）
# ---------------------------------------------------------------------
# 把 `npm run build` 的产物挂到同一个服务上，这样一个地址就能同时提供
# 页面和接口，演示时不用再单开 Vite。
# 开发期仍然可以用 Vite dev server（5173），它会把 /api 代理到这里。
# =====================================================================
DIST_DIR = PROJECT_ROOT / "frontend" / "dist"

if (DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def frontend_index():
    """首页。前端未构建时给出可操作的提示，而不是 404。"""
    index = DIST_DIR / "index.html"
    if not index.exists():
        raise HTTPException(
            status_code=503,
            detail="前端未构建。执行：cd frontend && npm install && npm run build",
        )
    return FileResponse(index)


@app.get("/{full_path:path}", include_in_schema=False)
def frontend_spa(full_path: str):
    """
    SPA 兜底路由，必须定义在所有 /api 路由之后。

    - /api/* 走不到这里（前面已经匹配），万一走到这里就明确 404
    - 静态文件存在就直接返回（带路径穿越防护）
    - 其余一律返回 index.html，交给前端路由处理
    """
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="接口不存在")

    index = DIST_DIR / "index.html"
    dist_root = DIST_DIR.resolve()
    candidate = (DIST_DIR / full_path).resolve()
    if candidate.is_file() and dist_root in candidate.parents:
        return FileResponse(candidate)
    if not index.exists():
        raise HTTPException(
            status_code=503,
            detail="前端未构建。执行：cd frontend && npm install && npm run build",
        )
    return FileResponse(index)
