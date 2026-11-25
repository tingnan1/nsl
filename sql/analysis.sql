-- =====================================================================
-- Phase 5：SQL 分析
-- ---------------------------------------------------------------------
-- 前置：数据库已建好并导入（Phase 4），启动方式见 start_mysql.cmd
-- 执行：
--   $env:MYSQL_PWD = '<见 .env>'
--   & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" `
--       -u root -h 127.0.0.1 -P 3306 --default-character-set=utf8mb4 `
--       < sql\analysis.sql
--
-- 口径边界（先说清楚，避免误读）：
--   * 数据只有 mmdd，**没有年份、没有小时**：
--       - 不做小时级分析（无小时字段）；
--       - 不做工作日/周末对比（无年份无法确定星期，dim_date.is_weekend 故意留 NULL）。
--   * 没有会话 ID、订单号、金额：
--       - 不算转化率、客单价、GMV；
--       - 「用户级重叠率」是 186 天累计口径，不是单次转化。
--   * 日志在 1112 基本结束（1112 仅 46 行），观测窗口实际到 1111。
--   * 全部数据都是 11.11 当天有购买的用户，所以「购买用户占比」恒为 100%，无信息量。
-- =====================================================================

USE tmall_analysis;

SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- =====================================================================
-- 1. 总览
-- =====================================================================
SELECT '【1. 总览】' AS 章节;

SELECT
    COUNT(*)                    AS 行为总条数,
    COUNT(DISTINCT user_id)     AS 去重用户数,
    MIN(date_key)               AS 起始日期,
    MAX(date_key)               AS 结束日期,
    COUNT(DISTINCT date_key)    AS 覆盖天数
FROM fact_user_log;


-- =====================================================================
-- 2. 每日趋势：行为量、购买事件、日环比（LAG）、7 日移动平均（窗口聚合）
-- =====================================================================
SELECT '【2. 每日趋势：环比 + 7 日移动平均】' AS 章节;

WITH daily AS (
    SELECT
        date_key,
        COUNT(*)              AS 行为条数,
        SUM(action_type = 2)  AS 购买事件
    FROM fact_user_log
    GROUP BY date_key
)
SELECT
    date_key AS 日期,
    行为条数,
    购买事件,
    LAG(行为条数) OVER (ORDER BY date_key) AS 前一日行为条数,
    ROUND(100.0 * (行为条数 - LAG(行为条数) OVER (ORDER BY date_key))
          / LAG(行为条数) OVER (ORDER BY date_key), 2) AS 日环比百分比,
    ROUND(AVG(行为条数) OVER (ORDER BY date_key
                              ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 0) AS 七日移动平均
FROM daily
ORDER BY date_key;


-- =====================================================================
-- 3. 双十一（1111）对比：当天 vs 前 7 天均值
-- =====================================================================
SELECT '【3. 双十一对比】' AS 章节;

WITH daily AS (
    SELECT date_key, COUNT(*) AS 行为条数, SUM(action_type = 2) AS 购买事件,
           COUNT(DISTINCT user_id) AS UV
    FROM fact_user_log
    WHERE date_key BETWEEN 1104 AND 1111
    GROUP BY date_key
)
SELECT
    ROUND(AVG(CASE WHEN date_key BETWEEN 1104 AND 1110 THEN 行为条数 END), 1) AS 前7天日均行为,
    MAX(CASE WHEN date_key = 1111 THEN 行为条数 END)                          AS 当天行为,
    ROUND(MAX(CASE WHEN date_key = 1111 THEN 行为条数 END)
          / AVG(CASE WHEN date_key BETWEEN 1104 AND 1110 THEN 行为条数 END), 2) AS 行为倍数,
    ROUND(AVG(CASE WHEN date_key BETWEEN 1104 AND 1110 THEN 购买事件 END), 1) AS 前7天日均购买,
    MAX(CASE WHEN date_key = 1111 THEN 购买事件 END)                          AS 当天购买,
    ROUND(MAX(CASE WHEN date_key = 1111 THEN 购买事件 END)
          / AVG(CASE WHEN date_key BETWEEN 1104 AND 1110 THEN 购买事件 END), 2) AS 购买倍数,
    ROUND(AVG(CASE WHEN date_key BETWEEN 1104 AND 1110 THEN UV END), 1)       AS 前7天日均UV,
    MAX(CASE WHEN date_key = 1111 THEN UV END)                                AS 当天UV
FROM daily;


-- =====================================================================
-- 4. 行为结构：事件数与去重用户数
-- =====================================================================
SELECT '【4. 行为结构（186 天累计）】' AS 章节;

-- 先聚合、再开窗：不要在 GROUP BY 的同一层里把 COUNT(DISTINCT ...) 塞进窗口函数，
-- 那种写法 MySQL 8 不保证接受，容易报 Invalid use of group function。
WITH a AS (
    SELECT
        action_type,
        COUNT(*)                AS 事件数,
        COUNT(DISTINCT user_id) AS 用户数
    FROM fact_user_log
    GROUP BY action_type
)
SELECT
    CASE action_type WHEN 0 THEN '点击' WHEN 1 THEN '加入购物车'
                     WHEN 2 THEN '购买' ELSE '加入收藏夹' END AS 行为,
    action_type,
    事件数,
    ROUND(100.0 * 事件数 / SUM(事件数) OVER (), 2) AS 事件占比百分比,
    用户数,
    ROUND(100.0 * 用户数 / MAX(用户数) OVER (), 2) AS 用户占全量百分比
FROM a
ORDER BY action_type;


-- =====================================================================
-- 5. 品类 TOP10 + 帕累托累计占比（RANK + 累计窗口 SUM）
-- =====================================================================
SELECT '【5. 品类 TOP10 与帕累托累计占比】' AS 章节;

WITH cat AS (
    SELECT cat_id, COUNT(*) AS 购买事件
    FROM fact_user_log
    WHERE action_type = 2
    GROUP BY cat_id
), ranked AS (
    SELECT
        cat_id,
        购买事件,
        RANK() OVER (ORDER BY 购买事件 DESC) AS 名次,
        SUM(购买事件) OVER (ORDER BY 购买事件 DESC, cat_id
                            ROWS UNBOUNDED PRECEDING) AS 累计购买事件,
        SUM(购买事件) OVER () AS 全部购买事件
    FROM cat
)
SELECT
    名次,
    cat_id AS 品类ID,
    购买事件,
    ROUND(100.0 * 购买事件 / 全部购买事件, 2) AS 占比百分比,
    ROUND(100.0 * 累计购买事件 / 全部购买事件, 2) AS 累计占比百分比
FROM ranked
WHERE 名次 <= 10
ORDER BY 名次;


-- =====================================================================
-- 6. 商家 TOP10：RANK 与 DENSE_RANK 的并列差异
-- =====================================================================
SELECT '【6. 商家 TOP10（RANK vs DENSE_RANK）】' AS 章节;

WITH s AS (
    SELECT
        seller_id,
        COUNT(*)                AS 购买事件,
        COUNT(DISTINCT user_id) AS 购买用户数
    FROM fact_user_log
    WHERE action_type = 2
    GROUP BY seller_id
)
SELECT
    RANK()       OVER (ORDER BY 购买事件 DESC) AS RANK名次,
    DENSE_RANK() OVER (ORDER BY 购买事件 DESC) AS DENSE_RANK名次,
    seller_id AS 商家ID,
    购买事件,
    购买用户数
FROM s
ORDER BY 购买事件 DESC, seller_id
LIMIT 10;


-- =====================================================================
-- 7. 用户购买频次分布
-- =====================================================================
SELECT '【7. 用户购买频次分布】' AS 章节;

WITH u AS (
    SELECT user_id, COUNT(*) AS 购买次数
    FROM fact_user_log
    WHERE action_type = 2
    GROUP BY user_id
)
SELECT
    区间 AS 购买次数区间,
    COUNT(*) AS 用户数,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS 占比百分比
FROM (
    SELECT
        CASE
            WHEN 购买次数 = 1  THEN '1次'
            WHEN 购买次数 = 2  THEN '2次'
            WHEN 购买次数 = 3  THEN '3次'
            WHEN 购买次数 <= 5 THEN '4-5次'
            WHEN 购买次数 <= 10 THEN '6-10次'
            WHEN 购买次数 <= 20 THEN '11-20次'
            WHEN 购买次数 <= 50 THEN '21-50次'
            WHEN 购买次数 <= 100 THEN '51-100次'
            ELSE '100次以上'
        END AS 区间,
        CASE
            WHEN 购买次数 = 1  THEN 1
            WHEN 购买次数 = 2  THEN 2
            WHEN 购买次数 = 3  THEN 3
            WHEN 购买次数 <= 5 THEN 4
            WHEN 购买次数 <= 10 THEN 5
            WHEN 购买次数 <= 20 THEN 6
            WHEN 购买次数 <= 50 THEN 7
            WHEN 购买次数 <= 100 THEN 8
            ELSE 9
        END AS 排序键
    FROM u
) t
GROUP BY 区间, 排序键
ORDER BY 排序键;


-- =====================================================================
-- 8. 复购标签：整体分布 + 商家复购率分布（train_sample）
-- =====================================================================
SELECT '【8. 复购标签整体分布】' AS 章节;

SELECT
    label,
    CASE label WHEN 1 THEN '复购买家' WHEN 0 THEN '非复购买家'
               ELSE '非该商家新客户（不在预测范围）' END AS 含义,
    COUNT(*) AS 样本数,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS 占比百分比
FROM train_sample
GROUP BY label
ORDER BY label;

SELECT '【8.1 商家维度复购率分布（按官方样本量分组）】' AS 章节;

WITH m AS (
    SELECT merchant_id, COUNT(*) AS 样本数, SUM(label) AS 复购样本数,
           ROUND(100.0 * SUM(label) / COUNT(*), 2) AS 商家复购率
    FROM train_sample
    GROUP BY merchant_id
)
SELECT
    CASE WHEN 样本数 < 30 THEN '样本量 <30（不参与排名）'
         WHEN 样本数 < 100 THEN '30-99'
         WHEN 样本数 < 500 THEN '100-499'
         ELSE '500 以上' END AS 样本量分组,
    COUNT(*) AS 商家数,
    ROUND(AVG(商家复购率), 2) AS 组内平均复购率,
    ROUND(MIN(商家复购率), 2) AS 最低复购率,
    ROUND(MAX(商家复购率), 2) AS 最高复购率
FROM m
GROUP BY 样本量分组
ORDER BY 组内平均复购率 DESC;


-- =====================================================================
-- 9. 用户分层：1111 前是否购买 × 复购率（用 SQL 复现 Python 的结论）
-- =====================================================================
SELECT '【9. 用户分层：1111 前是否购买 × 复购率】' AS 章节;

WITH pre AS (
    SELECT user_id, COUNT(*) AS 前期购买次数
    FROM fact_user_log
    WHERE action_type = 2 AND date_key < 1111
    GROUP BY user_id
)
SELECT
    CASE WHEN p.user_id IS NULL THEN '1111前无购买（新客）'
         ELSE '1111前有购买（老客）' END AS 分层,
    COUNT(*)                AS 样本数,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS 样本占比,
    SUM(t.label)            AS 复购样本数,
    ROUND(100.0 * SUM(t.label) / COUNT(*), 2) AS 复购率百分比
FROM train_sample t
LEFT JOIN pre p ON p.user_id = t.user_id
GROUP BY 分层
ORDER BY 复购率百分比 DESC;


-- =====================================================================
-- 10. 商家内每日购买排名（ROW_NUMBER 分区排名）
-- =====================================================================
SELECT '【10. 购买事件最多的 3 个商家中，各自最旺的 3 天】' AS 章节;

WITH top_seller AS (
    SELECT seller_id, COUNT(*) AS 购买事件
    FROM fact_user_log
    WHERE action_type = 2
    GROUP BY seller_id
    ORDER BY 购买事件 DESC
    LIMIT 3
), daily AS (
    SELECT f.seller_id, f.date_key, COUNT(*) AS 当日购买
    FROM fact_user_log f
    JOIN top_seller s ON s.seller_id = f.seller_id
    WHERE f.action_type = 2
    GROUP BY f.seller_id, f.date_key
), ranked AS (
    SELECT seller_id, date_key, 当日购买,
           ROW_NUMBER() OVER (PARTITION BY seller_id ORDER BY 当日购买 DESC) AS 名次
    FROM daily
)
SELECT seller_id AS 商家ID, 名次, date_key AS 日期, 当日购买
FROM ranked
WHERE 名次 <= 3
ORDER BY 商家ID, 名次;


-- =====================================================================
-- 11. 分区裁剪验证：EXPLAIN 应只命中 p11
-- =====================================================================
SELECT '【11. 分区裁剪（EXPLAIN 只看 partitions 列）】' AS 章节;

EXPLAIN
SELECT COUNT(*) FROM fact_user_log
WHERE date_key BETWEEN 1101 AND 1111 AND action_type = 2;
