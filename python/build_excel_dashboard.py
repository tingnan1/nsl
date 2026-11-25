"""
生成 Excel 看板

做什么：
    读取 outputs/bi/ 下的宽表与 outputs/tables/ 下的模型结果，生成一个可直接双击
    打开的 dashboard/tmall_analytics.xlsx，包含核心指标、每日趋势、行为结构、
    品类/商家 TOP10、用户分层与模型效果六张表，并为趋势与排名配图。

为什么单独做一个 Excel：
    Power BI 的 .pbix 需要本机安装 Power BI Desktop 才能打开，Vue 看板需要起服务。
    Excel 是唯一「发给任何人都能立刻打开」的载体，适合随简历一起提交。

用法：
    .\\.venv\\Scripts\\python.exe python\\build_excel_dashboard.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pymysql
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BI_DIR = PROJECT_ROOT / "outputs" / "bi"
TABLE_DIR = PROJECT_ROOT / "outputs" / "tables"
MODEL_DIR = PROJECT_ROOT / "outputs" / "models"
OUT_PATH = PROJECT_ROOT / "dashboard" / "tmall_analytics.xlsx"

# 视觉规范：深蓝表头 + 浅灰斑马纹，数字右对齐
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
TITLE_FONT = Font(bold=True, size=14, color="1F3864")
NOTE_FONT = Font(italic=True, size=9, color="7F7F7F")
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = PROJECT_ROOT / ".env"
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def load_bi(name: str) -> pd.DataFrame:
    return pd.read_csv(BI_DIR / name, encoding="utf-8-sig")


def fetch_kpi() -> list[tuple[str, str, str]]:
    """核心指标直接查已物化的聚合表，耗时在毫秒级。"""
    env = load_env()
    conn = pymysql.connect(
        host=env.get("MYSQL_HOST", "127.0.0.1"),
        port=int(env.get("MYSQL_PORT", "3306")),
        user=env.get("MYSQL_USER", "root"),
        password=env.get("MYSQL_PASSWORD", ""),
        database=env.get("MYSQL_DATABASE", "tmall_analysis"),
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT SUM(events), SUM(purchases), COUNT(*),
                       SUM(effective = 1),
                       ROUND(SUM(IF(effective = 1, events, 0)) / SUM(effective = 1)),
                       MIN(date_key), MAX(date_key)
                FROM agg_daily
            """)
            events, purchases, days, eff_days, avg_events, first_date, last_date = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM dim_user")
            users = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*), SUM(label) FROM train_sample")
            train_rows, train_pos = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM test_sample")
            test_rows = cur.fetchone()[0]
    finally:
        conn.close()

    repurchase_rate = train_pos / train_rows
    return [
        ("行为事件总数", f"{int(events):,} 条", "事实表 54,925,330 行，agg_daily 求和"),
        ("去重用户数", f"{int(users):,} 人", "与 fact 去重结果一致（已校验）"),
        ("购买事件数", f"{int(purchases):,} 条",
         f"占全部行为 {purchases / events * 100:.2f}%"),
        ("观测天数（名义）", f"{int(days)} 天", f"{first_date} – {last_date}"),
        ("观测天数（有效）", f"{int(eff_days)} 天",
         "1112 仅 46 行，属数据截断尾巴，不计入日均"),
        ("日均行为条数", f"{int(avg_events):,} 条", "按 185 天有效窗口"),
        ("训练样本", f"{int(train_rows):,} 条", "user × merchant + 复购标签"),
        ("测试样本", f"{int(test_rows):,} 条", "无标签，仅预测"),
        ("官方复购率", f"{repurchase_rate * 100:.2f}%",
         f"{int(train_pos):,} / {int(train_rows):,}"),
    ]


def fetch_model_metrics() -> list[tuple[str, str, str]]:
    meta_path = MODEL_DIR / "repurchase_model_meta.json"
    if not meta_path.exists():
        return [("模型", "未训练", "先运行 python/train_model.py")]
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    holdout = meta["holdout_metrics_at_0_5"]
    at_rate = meta["holdout_metrics_at_rate_threshold"]
    return [
        ("模型", meta["algorithm_label"], meta["model_type"]),
        ("ROC-AUC", f"{holdout['ROC_AUC']:.4f}",
         f"重复交叉验证 {meta['cv_roc_auc_mean']:.4f} ± {meta['cv_roc_auc_std']:.4f}"),
        ("PR-AUC", f"{holdout['PR_AUC']:.4f}", "正例率仅 6.12%，PR-AUC 比 ROC-AUC 更严格"),
        ("按正例率定阈值后 精确率", f"{at_rate['精确率']:.4f}", "0.5 阈值下精确率为 0，属下限"),
        ("按正例率定阈值后 召回率", f"{at_rate['召回率']:.4f}", ""),
        ("Brier 分数", "0.05612", "优于常数基准线 0.05742"),
        ("训练特征数", str(meta["n_features"]), "已剔除 3 个零方差特征"),
    ]


def style_header(ws, row: int, ncols: int) -> None:
    for col in range(1, ncols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER


def style_body(ws, first_row: int, last_row: int, ncols: int,
               left_align_cols: tuple[int, ...] = ()) -> None:
    """
    正文样式：加边框；数字列右对齐，文字列左对齐。

    left_align_cols 必须显式指定——说明类文字右对齐会贴着单元格右侧，
    在窄列下看起来像串到了前一列。
    """
    for row in range(first_row, last_row + 1):
        for col in range(1, ncols + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = BORDER
            horizontal = "left" if col in left_align_cols else "right"
            cell.alignment = Alignment(horizontal=horizontal, vertical="center")


def write_table(ws, start_row: int, headers: list[str], rows: list[list],
                left_align_cols: tuple[int, ...] = ()) -> int:
    """从 start_row 起写一张带表头样式的表，返回最后一行行号。"""
    for col, name in enumerate(headers, 1):
        ws.cell(row=start_row, column=col, value=name)
    style_header(ws, start_row, len(headers))

    for offset, values in enumerate(rows, 1):
        for col, value in enumerate(values, 1):
            ws.cell(row=start_row + offset, column=col, value=value)
    last = start_row + len(rows)
    style_body(ws, start_row + 1, last, len(headers), left_align_cols)
    return last


def autosize(ws, widths: list[int]) -> None:
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def main() -> int:
    if not BI_DIR.exists():
        print("缺少 outputs/bi，先运行 python/export_bi.py")
        return 1

    wb = Workbook()

    # ---------------- Sheet 1：核心指标 ----------------
    ws = wb.active
    ws.title = "核心指标"
    ws["A1"] = "天猫复购预测数据分析 — 核心指标"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "数据源：天猫复购预测公开数据集（阿里云天池）"
    ws["A2"].font = NOTE_FONT

    last = write_table(ws, 4, ["指标", "数值", "口径说明"],
                       [list(r) for r in fetch_kpi()], left_align_cols=(3,))
    ws.cell(row=last + 2, column=1, value="模型效果（holdout 70% 训练集版本）").font = Font(bold=True)
    write_table(ws, last + 3, ["指标", "数值", "说明"],
                [list(r) for r in fetch_model_metrics()], left_align_cols=(3,))
    autosize(ws, [26, 20, 52])
    ws.freeze_panes = "A5"

    # ---------------- Sheet 2：每日趋势 ----------------
    daily = load_bi("bi_daily.csv")
    ws = wb.create_sheet("每日趋势")
    ws["A1"] = "每日行为量与购买事件（0511–1112）"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "注意：1112 仅 46 行，为数据截断尾巴；前 9 天（0511–0519）无点击记录，与 0519→0520 的结构性跳变一致"
    ws["A2"].font = NOTE_FONT

    rows = [[int(r.date_key), int(r.events), int(r.purchases), int(r.uv),
             int(r.clicks), int(r.carts), int(r.favs)] for r in daily.itertuples()]
    last = write_table(ws, 4, ["日期(mmdd)", "行为条数", "购买事件", "UV",
                               "点击", "加购", "收藏"], rows)
    autosize(ws, [12, 14, 12, 12, 12, 12, 12])

    chart = LineChart()
    chart.title = "每日行为量与购买事件（对数轴）"
    chart.y_axis.title = "条数"
    chart.x_axis.title = "日期（mmdd）"
    chart.height, chart.width = 8, 20
    # 1111 单日 1,058 万条，线性轴会把其余 185 天压成一条平线，
    # 所以用对数轴——这也是 Python 侧图表采用同样处理的原因。
    chart.y_axis.scaling.logBase = 10
    chart.x_axis.tickLblSkip = 20
    data = Reference(ws, min_col=2, max_col=3, min_row=4, max_row=last)
    cats = Reference(ws, min_col=1, min_row=5, max_row=last)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    ws.add_chart(chart, "I4")

    # ---------------- Sheet 3：行为结构 ----------------
    actions = load_bi("bi_action_summary.csv")
    ws = wb.create_sheet("行为结构")
    ws["A1"] = "四类行为的事件数与去重用户数（186 天累计）"
    ws["A1"].font = TITLE_FONT
    rows = [[r.action_name, int(r.events), int(r.users), float(r.event_share_pct)]
            for r in actions.itertuples()]
    last = write_table(ws, 3, ["行为", "事件数", "去重用户数", "事件占比%"], rows)
    autosize(ws, [14, 16, 16, 14])

    bar = BarChart()
    bar.title = "行为事件数"
    bar.y_axis.title = "事件数"
    bar.height, bar.width = 7, 12
    bar.add_data(Reference(ws, min_col=2, min_row=3, max_row=last), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=1, min_row=4, max_row=last))
    ws.add_chart(bar, "F3")
    ws.cell(row=last + 2, column=1,
            value="说明：全部 424,170 个用户都在 1111 有购买，因此「购买用户占比」恒为 100%，"
                  "没有信息量；这里用事件数占比才有区分度。").font = NOTE_FONT

    # ---------------- Sheet 4：品类与商家 TOP10 ----------------
    ws = wb.create_sheet("品类与商家TOP10")
    ws["A1"] = "购买事件 TOP10"
    ws["A1"].font = TITLE_FONT

    cats = load_bi("bi_category.csv").head(10)
    rows = [[int(r.rank), int(r.cat_id), int(r.purchases), int(r.buyers),
             float(r.share_pct), float(r.cumulative_pct)] for r in cats.itertuples()]
    last = write_table(ws, 3, ["排名", "品类ID", "购买事件", "买家数",
                               "份额%", "累计份额%"], rows)

    sellers = load_bi("bi_seller.csv").head(10)
    rows2 = [[int(r.rank), int(r.seller_id), int(r.purchases), int(r.buyers),
              float(r.share_pct), float(r.cumulative_pct)] for r in sellers.itertuples()]
    last2 = write_table(ws, last + 3, ["排名", "商家ID", "购买事件", "买家数",
                                       "份额%", "累计份额%"], rows2)
    autosize(ws, [10, 14, 14, 12, 10, 12])

    bar = BarChart()
    bar.title = "品类购买事件 TOP10"
    bar.height, bar.width = 8, 14
    bar.add_data(Reference(ws, min_col=3, min_row=3, max_row=last), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=2, min_row=4, max_row=last))
    ws.add_chart(bar, "H3")
    ws.cell(row=last2 + 2, column=1,
            value="共 1,324 个有购买事件的品类、4,995 个商家。"
                  "TOP10 品类合计占 23.21%，TOP10 商家合计占 4.28%——"
                  "商家侧高度长尾，说明运营资源不宜只押头部。").font = NOTE_FONT

    # ---------------- Sheet 5：用户分层与模型 ----------------
    ws = wb.create_sheet("用户分层与模型")
    ws["A1"] = "1111 前是否购买 → 后续复购率"
    ws["A1"].font = TITLE_FONT
    seg = load_bi("bi_segments.csv")
    rows = [[r.segment, int(r.samples), int(r.repurchase), float(r.repurchase_rate)]
            for r in seg.itertuples()]
    last = write_table(ws, 3, ["分层", "样本数", "复购样本数", "复购率%"], rows)

    ws.cell(row=last + 2, column=1,
            value="两项比例检验：z = 12.49、p ≈ 8.9e-36，差值 95% CI [1.28, 1.72] 个百分点；"
                  "显著不等于因果，两组并非随机分配。").font = NOTE_FONT

    biz_path = TABLE_DIR / "business_value.csv"
    if biz_path.exists():
        biz = pd.read_csv(biz_path, encoding="utf-8-sig")
        ws.cell(row=last + 4, column=1,
                value="不同触达预算下模型能多捞回多少真实复购用户").font = Font(bold=True)
        rows = [[r["触达比例"], int(r["触达人数"]), int(r["命中正例"]),
                 float(r["精确率"]), float(r["相对随机提升"]), int(r["较随机多命中"])]
                for _, r in biz.iterrows()]
        last = write_table(ws, last + 5,
                           ["触达比例", "触达人数", "命中正例", "精确率",
                            "相对随机提升", "较随机多命中"], rows)
        ws.cell(row=last + 2, column=1,
                value="离线模拟：假设触达不改变用户行为、不计触达成本，真实投放需 A/B 验证。"
                ).font = NOTE_FONT

    autosize(ws, [30, 14, 14, 14, 16, 16])

    # ---------------- Sheet 6：数据说明 ----------------
    ws = wb.create_sheet("数据说明")
    ws["A1"] = "数据口径与限制（先看这里）"
    ws["A1"].font = TITLE_FONT
    notes = [
        ("数据来源", "阿里云天池「天猫复购预测」公开数据集，版权归原始提供方所有"),
        ("样本性质", "424,170 个用户全部是 11.11 当天有购买的用户"),
        ("观测窗口", "0511–1112 共 186 天；1112 仅 46 行、0 次购买，为数据截断尾巴"),
        ("有效窗口", "185 天，所有日均类指标均按此口径"),
        ("时间粒度", "只有 mmdd，没有年份、没有小时"),
        ("无会话概念", "没有会话 ID，因此不存在会话级转化率、跳出率"),
        ("无金额字段", "因此不存在 GMV、客单价、ARPU"),
        ("复购标签", "来自比赛官方 label，观测窗口在数据之外，日志本身无法构造"),
        ("测试集", "无标签，只输出预测概率，不报任何准确率/AUC"),
        ("模型定位", "可做相对排序与期望人数估算，不宜当作单个用户的精确承诺"),
    ]
    last = write_table(ws, 3, ["项目", "说明"], [list(n) for n in notes])
    autosize(ws, [16, 78])
    for row in range(4, last + 1):
        ws.cell(row=row, column=2).alignment = Alignment(horizontal="left", wrap_text=True)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_PATH)
    print(f"已生成 Excel 看板：{OUT_PATH}")
    print(f"工作表：{[s.title for s in wb.worksheets]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
