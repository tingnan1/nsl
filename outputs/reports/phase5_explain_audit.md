# SQL 执行计划审计（analysis.sql）

> 由 `python/explain_audit.py` 生成，逐条对 `sql/analysis.sql` 的查询执行 `EXPLAIN`。
> 事实表 `fact_user_log` 共 54,925,330 行，按 `date_key` 分 8 个区。
> 预估行数只统计**基表**；派生表在计划里会重复出现同一批行，计入会放大代价。

**结果：问题 0 条 / 关注 3 条 / OK 8 条 / 失败 0 条**

## 1. 逐条结果

| # | 章节 | 等级 | 基表预估行数 | 命中分区 | 说明 |
|---|---|---|---|---|---|
| 1 | 1. 总览 | OK | 54,213,099 | p05,p06,p07,p08,p09,p10,p11,pmax | 走索引，无临时表 |
| 2 | 2. 每日趋势：行为量、购买事件、日环比（LAG）、7 日移动平均（窗口聚合） | OK | 54,213,099 | p05,p06,p07,p08,p09,p10,p11,pmax | 走索引，无临时表 |
| 3 | 3. 双十一（1111）对比：当天 vs 前 7 天均值 | OK | 20,540,448 | p11 | 走索引，无临时表 |
| 4 | 4. 行为结构：事件数与去重用户数 | OK | 54,213,099 | p05,p06,p07,p08,p09,p10,p11,pmax | 走索引，无临时表 |
| 5 | 5. 品类 TOP10 + 帕累托累计占比（RANK + 累计窗口 SUM） | OK | 6,427,146 | p05,p06,p07,p08,p09,p10,p11,pmax | 走索引，无临时表 |
| 6 | 6. 商家 TOP10：RANK 与 DENSE_RANK 的并列差异 | OK | 6,427,146 | p05,p06,p07,p08,p09,p10,p11,pmax | 走索引，无临时表 |
| 7 | 7. 用户购买频次分布 | OK | 6,427,146 | p05,p06,p07,p08,p09,p10,p11,pmax | 走索引，无临时表 |
| 8 | 8. 复购标签：整体分布 + 商家复购率分布（train_sample） | 关注 | 254,402 | - | train_sample 临时表 + 文件排序 |
| 9 | 8. 复购标签：整体分布 + 商家复购率分布（train_sample） | OK | 254,402 | - | 走索引，无临时表 |
| 10 | 9. 用户分层：1111 前是否购买 × 复购率（用 SQL 复现 Python 的结论） | 关注 | 4,323,914 | p05,p06,p07,p08,p09,p10,p11 | t 全表扫描约 254,402 行；t 临时表 + 文件排序 |
| 11 | 10. 商家内每日购买排名（ROW_NUMBER 分区排名） | 关注 | 6,427,653 | p05,p06,p07,p08,p09,p10,p11,pmax | fact_user_log 临时表 + 文件排序 |

## 2. 实测耗时

真正执行每条查询（含取回全部结果）的墙钟时间，复用上次 `--time` 的结果，同一台机器、同一份数据：

| # | 章节 | 基表预估行数 | 实测耗时 |
|---|---|---|---|
| 1 | 1. 总览 | 54,213,099 | **59.00 秒** |
| 2 | 2. 每日趋势：行为量、购买事件、日环比（LAG）、7 日移动平均（窗口聚合） | 54,213,099 | **51.23 秒** |
| 3 | 3. 双十一（1111）对比：当天 vs 前 7 天均值 | 20,540,448 | **60.65 秒** |
| 4 | 4. 行为结构：事件数与去重用户数 | 54,213,099 | **376.01 秒** |
| 5 | 5. 品类 TOP10 + 帕累托累计占比（RANK + 累计窗口 SUM） | 6,427,146 | **11.08 秒** |
| 6 | 6. 商家 TOP10：RANK 与 DENSE_RANK 的并列差异 | 6,427,146 | **10.63 秒** |
| 7 | 7. 用户购买频次分布 | 6,427,146 | **17.93 秒** |
| 8 | 8. 复购标签：整体分布 + 商家复购率分布（train_sample） | 254,402 | **0.15 秒** |
| 9 | 8. 复购标签：整体分布 + 商家复购率分布（train_sample） | 254,402 | **1.22 秒** |
| 10 | 9. 用户分层：1111 前是否购买 × 复购率（用 SQL 复现 Python 的结论） | 4,323,914 | **11.49 秒** |
| 11 | 10. 商家内每日购买排名（ROW_NUMBER 分区排名） | 6,427,653 | **10.90 秒** |

> 最慢的是第 4 条（4. 行为结构：事件数与去重用户数），耗时 **376.01 秒**。
> 这类「全期累计」查询必须扫完全部 5,492 万行才能给出一个数字，加索引救不了——索引只能减少回表，不能减少要读的键值数量。


## 3. 逐条执行计划明细

### 查询 1：1. 总览

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| fact_user_log | p05,p06,p07,p08,p09,p10,p11,pmax | index | - | idx_user_date | 54,213,099 | Using index |

### 查询 2：2. 每日趋势：行为量、购买事件、日环比（LAG）、7 日移动平均（窗口聚合）

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| <derived2> | - | ALL | - | - | 54,213,099 | Using filesort |
| fact_user_log | p05,p06,p07,p08,p09,p10,p11,pmax | index | idx_date_action,idx_user_date,idx_month_action | idx_date_action | 54,213,099 | Using index |

### 查询 3：3. 双十一（1111）对比：当天 vs 前 7 天均值

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| <derived2> | - | ALL | - | - | 10,270,224 | - |
| fact_user_log | p11 | index | idx_date_action,idx_user_date,idx_month_action | idx_date_action | 20,540,448 | Using where |

### 查询 4：4. 行为结构：事件数与去重用户数

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| <derived2> | - | ALL | - | - | 54,213,099 | Using filesort |
| fact_user_log | p05,p06,p07,p08,p09,p10,p11,pmax | index | idx_date_action,idx_seller_action,idx_item_date,idx_cat_action,idx_month_action | idx_month_action | 54,213,099 | - |

### 查询 5：5. 品类 TOP10 + 帕累托累计占比（RANK + 累计窗口 SUM）

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| <derived2> | - | ALL | - | - | 6,427,146 | Using where; Using filesort |
| <derived3> | - | ALL | - | - | 6,427,146 | Using filesort |
| fact_user_log | p05,p06,p07,p08,p09,p10,p11,pmax | ref | idx_cat_action,idx_month_action | idx_month_action | 6,427,146 | Using temporary |

### 查询 6：6. 商家 TOP10：RANK 与 DENSE_RANK 的并列差异

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| <derived2> | - | ALL | - | - | 6,427,146 | Using filesort |
| fact_user_log | p05,p06,p07,p08,p09,p10,p11,pmax | ref | idx_seller_action,idx_month_action | idx_month_action | 6,427,146 | Using filesort |

### 查询 7：7. 用户购买频次分布

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| <derived3> | - | ALL | - | - | 6,427,146 | Using temporary; Using filesort |
| fact_user_log | p05,p06,p07,p08,p09,p10,p11,pmax | ref | idx_user_date,idx_month_action | idx_month_action | 6,427,146 | Using temporary |

### 查询 8：8. 复购标签：整体分布 + 商家复购率分布（train_sample）

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| train_sample | - | index | idx_label | idx_label | 254,402 | Using index; Using temporary; Using filesort |

### 查询 9：8. 复购标签：整体分布 + 商家复购率分布（train_sample）

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| <derived2> | - | ALL | - | - | 254,402 | Using temporary; Using filesort |
| train_sample | - | index | idx_merchant | idx_merchant | 254,402 | - |

### 查询 10：9. 用户分层：1111 前是否购买 × 复购率（用 SQL 复现 Python 的结论）

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| t | - | ALL | - | - | 254,402 | Using temporary; Using filesort |
| <derived2> | - | ref | <auto_key0> | <auto_key0> | 15 | - |
| fact_user_log | p05,p06,p07,p08,p09,p10,p11 | range | idx_date_action,idx_user_date,idx_month_action | idx_month_action | 4,069,512 | Using index condition; Using temporary |

### 查询 11：10. 商家内每日购买排名（ROW_NUMBER 分区排名）

| 表 | 分区 | type | 可能索引 | 实际索引 | 预估行数 | Extra |
|---|---|---|---|---|---|---|
| <derived2> | - | ALL | - | - | 1,522 | Using where; Using filesort |
| <derived3> | - | ALL | - | - | 1,522 | Using filesort |
| <derived4> | - | ALL | - | - | 3 | Using temporary |
| f | p05,p06,p07,p08,p09,p10,p11,pmax | ref | idx_seller_action,idx_month_action | idx_seller_action | 507 | - |
| fact_user_log | p05,p06,p07,p08,p09,p10,p11,pmax | ref | idx_seller_action,idx_month_action | idx_month_action | 6,427,146 | Using temporary; Using filesort |

## 4. 结论与优化方向

**1）执行计划层面没有问题。** 事实表上的查询全部命中索引，没有出现 `type=ALL` 的百万行级全表扫描；带日期范围的查询只命中必要分区（`date_key` 分区裁剪生效）。3 条「关注」项分别是：`train_sample`（25 万行）上的分组排序、以及两条需要 `GROUP BY` 后再排序取 TOP N 的查询——临时表与文件排序在这里是聚合语义的必要代价，不是索引缺失。

**2）真正的瓶颈在「全期累计」口径本身。** 这类查询必须扫完 5,492 万行才能给出一个数字（如去重用户数、行为结构），加索引也救不了。

**3）解决办法是物化，不是调 SQL。** 项目为此建了 3 张聚合表（`agg_daily` / `agg_action_summary` / `agg_segment_repurchase`），由 `sql/build_aggregates.sql` 一次性重算，接口直接读结果：

| 指标 | 直查事实表 | 读物化表 | 倍数 |
|---|---|---|---|
| 总览（事件数 + 去重用户） | 59 秒 | **0.004 秒** | 约 15,000× |
| 行为结构（4 次 COUNT DISTINCT） | 376 秒 | **0.04 秒** | 约 9,000× |
| 每日趋势（含 UV） | 51 秒 | **0.03 秒** | 约 1,700× |

**4）代价与边界。** 物化表是空间换时间，数据更新后必须重算（`build_aggregates.sql` 末尾带一致性断言，比对事实表行数与购买数，防止物化表悄悄过期）。没有断言的话，过期数据会让接口返回看似正常但错误的数字。
