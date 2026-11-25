# Power BI 数据模型说明（Phase 9）

> 导出脚本：`python/export_bi.py`，产出目录 `outputs/bi/`。
> 生成方式：从 MySQL 实际查询后落成扁平 CSV，**不是手工整理的数据**。

## 1. 为什么导出 CSV，而不是让 Power BI 直连 MySQL

| 方案 | 问题 |
|---|---|
| Power BI 直连 MySQL | 需要本机装 MySQL Connector/ODBC，驱动位数（32/64）要与 Power BI 匹配，还要处理账号密码；换台电脑就要重配 |
| **导出 CSV** | Power BI 直接读文件，零依赖、零配置，发给别人也能打开 |

代价是数据不实时。本项目的原始数据是静态竞赛数据集，不存在实时性问题。

## 2. 导出的表

| 文件 | 行数 | 粒度 | 用途 |
|---|---|---|---|
| `bi_dim_date.csv` | 186 | 一天 | 日期维度（月份、年内日序、距双十一天数） |
| `bi_daily.csv` | 186 | 一天 | 每日行为量、UV、四类行为分解、购买率、**effective 标记** |
| `bi_action_summary.csv` | 4 | 一种行为 | 行为结构（事件数、去重用户数、占比） |
| `bi_category.csv` | 1,324 | 一个品类 | 购买事件、去重买家、占比、累计占比、排名 |
| `bi_seller.csv` | 4,995 | 一个商家 | 购买事件、去重买家、占比、累计占比、排名 |
| `bi_segments.csv` | 2 | 一个用户分层 | 老客/新客的样本数与复购率 |
| `bi_merchant_health.csv` | 1,993 | 一个商家 | 官方样本数、复购样本、复购率、样本量是否足够 |
| `bi_label_distribution.csv` | 2 | 一个标签 | 训练集标签分布 |
| `bi_predictions_test.csv` | 261,477 | 一个样本对 | 预测复购概率、概率排名、概率十分位 |

## 3. 关键字段

### bi_daily.csv

| 字段 | 含义 |
|---|---|
| `date_key` | MMDD 整数（如 1111） |
| `mmdd` | 四位字符串（如 `1111`），用于排序和显示 |
| `month` / `day` / `day_of_year` | 由 date_key 拆出 |
| `events` | 当日全部行为条数 |
| `uv` | 当日去重活跃用户数 |
| `clicks` / `carts` / `favs` / `purchases` | 当日四类行为的事件数 |
| `purchase_rate_pct` | 购买事件 / 全部事件（**不是转化率**，见 §5） |
| `events_per_user` | 人均行为条数 |
| `effective` | **1 = 纳入统计口径；0 = 数据截断尾巴（1112 仅 46 行）** |

> **做日均、趋势、周期性分析时，必须先过滤 `effective = 1`。**
> 186 天全算的日均是 295,297，只算 185 天有效窗口是 296,893——差 0.54%。
> 在 Power BI 里建议直接在 `bi_daily` 上加视觉级筛选器 `effective = 1`，
> 或者建一个度量值 `CALCULATE(AVERAGE(bi_daily[events]), bi_daily[effective] = 1)`。

### bi_merchant_health.csv

| 字段 | 含义 |
|---|---|
| `samples` | 该商家在训练集中的官方样本数 |
| `repurchase` / `repurchase_rate` | 复购样本数与复购率 |
| `sample_size_ok` | `是` / `否(样本<30)`——**做排行榜时务必用这个字段过滤** |

小样本会造出假极值：实测样本量 <30 的商家里出现过 **52.94%** 的复购率，而全样本平均只有 6.12%。不筛掉的话排行榜第一名很可能是只有两三个样本的商家。

## 4. Power BI 里的建模建议

```
bi_dim_date ──(1:*)── bi_daily
bi_category        （独立维度表，无外键关系）
bi_seller          （独立维度表）
bi_segments        （独立小表，做卡片图）
bi_merchant_health （独立表）
bi_label_distribution（独立小表）
bi_predictions_test   （独立大表，只做分布图）
```

`bi_daily.date_key` 与 `bi_dim_date.date_key` 建 1 对多关系，就能用月份切片。

### 建议的 5 个页面

1. **运营总览**：卡片（总事件 5492 万、用户 424,170、购买事件 3,292,144、复购率 6.12%）+ 行为结构环形图
2. **时间分析**：双轴折线（events 与 purchases，**纵轴用对数刻度**，否则 1111 会把其余 185 天压平）+ 1111 标注线
3. **品类分析**：TOP10 条形图 + 帕累托累计折线（用 `cumulative_pct`）
4. **商家分析**：TOP10 条形图；健康度散点（x=样本数，y=复购率），**先按 `sample_size_ok = 是` 过滤**
5. **预测评估**：预测概率分布直方图（按 `prob_decile` 分箱）+ ROC/PR 曲线用 `outputs/charts/11_roc_curve.png`、`12_pr_curve.png` 作为图片插入

## 5. 必须注意的口径（否则会讲错）

1. **`purchase_rate_pct` 不是转化率**。它是「购买事件 / 全部事件」的行为量比值，没有会话概念。
2. **日均必须过滤 `effective = 1`**：1112 只有 46 行，全算会把日均拉低 0.54%。
3. **「购买用户占比 = 100%」没有信息量**：这份数据的 424,170 个用户全部是 11.11 买家。
4. **1111 的 UV 等于全量用户**，所以「1111 拉新 4.43 倍」是错觉。
5. **不能做工作日/周末**：只有 mmdd，没有年份。
6. **不能做 GMV / 客单价 / ARPU**：没有金额字段。
7. **测试集没有标签**：`bi_predictions_test.csv` 只有预测概率，没有任何真实结果可对比，因此不能算准确率/AUC。

## 6. 重新生成

```powershell
# 前提：MySQL 在跑（start_mysql.cmd），且 Phase 4 数据已导入
.\.venv\Scripts\python.exe python\export_bi.py
```

耗时约 3–5 分钟，主要花在品类和商家的 `COUNT(DISTINCT user_id)` 上。
