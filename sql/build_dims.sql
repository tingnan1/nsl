-- =====================================================================
-- 维度表补充脚本（Phase 4）
-- ---------------------------------------------------------------------
-- 执行顺序：create_table.sql -> load_data.sql -> build_dims.sql -> verify_data.sql
--
-- 为什么维度要"从事实表反推"？
--   这份数据集没有独立提供的商家表、品类表、品牌表、商品表。
--   唯一可信的来源就是行为日志里出现过的 ID。
--   所以这里只把真实出现过的 ID 写进维度表，不虚构任何商家名、品类名、价格。
-- =====================================================================

SET NAMES utf8mb4;

USE tmall_analysis;

TRUNCATE TABLE dim_seller;
TRUNCATE TABLE dim_category;
TRUNCATE TABLE dim_brand;
TRUNCATE TABLE dim_item;


-- ---------------------------------------------------------------------
-- 1. 商家维度
-- ---------------------------------------------------------------------
-- seller_id（日志）和 merchant_id（样本集）是同一个概念，两边都要收进来。
-- 实测 seller_id 最大 4995 而 merchant_id 最大 4993，
-- 说明有商家只出现在日志里、没有出现在样本集中，属于正常情况。
INSERT INTO dim_seller (seller_id)
SELECT seller_id FROM fact_user_log GROUP BY seller_id
UNION
SELECT merchant_id FROM train_sample GROUP BY merchant_id
UNION
SELECT merchant_id FROM test_sample GROUP BY merchant_id;


-- ---------------------------------------------------------------------
-- 2. 品类维度
-- ---------------------------------------------------------------------
INSERT INTO dim_category (cat_id)
SELECT cat_id FROM fact_user_log GROUP BY cat_id;


-- ---------------------------------------------------------------------
-- 3. 品牌维度
-- ---------------------------------------------------------------------
-- 事实表有 91,015 条 brand_id 为空，这些行不属于任何品牌，不进维度表。
INSERT INTO dim_brand (brand_id)
SELECT brand_id FROM fact_user_log
WHERE brand_id IS NOT NULL
GROUP BY brand_id;


-- ---------------------------------------------------------------------
-- 4. 商品维度
-- ---------------------------------------------------------------------
-- 数据里没有商品主表，item -> cat/brand 的归属关系只能从日志里推导。
-- 如果同一个 item_id 在日志里对应过多组 (cat_id, brand_id)（数据噪声），
-- 取出现次数最多的那一组；并列时按 cat_id、brand_id 升序取第一个，
-- 保证结果确定、可复现，而不是随机留一个。
--
-- 这一步要对 5492 万行做分组，是整套导入里最慢的一步，属预期。
-- 写法上先聚合出现次数、再在聚合结果上排名，避免把聚合函数直接写进
-- 窗口函数的 ORDER BY 里（那种写法在 MySQL 8 上容易触发不可预期的报错）。
INSERT INTO dim_item (item_id, cat_id, brand_id)
SELECT item_id, cat_id, brand_id
FROM (
    SELECT
        item_id,
        cat_id,
        brand_id,
        ROW_NUMBER() OVER (
            PARTITION BY item_id
            ORDER BY cnt DESC, cat_id ASC, IFNULL(brand_id, 0) ASC
        ) AS rn
    FROM (
        SELECT
            item_id,
            cat_id,
            brand_id,
            COUNT(*) AS cnt
        FROM fact_user_log
        GROUP BY item_id, cat_id, brand_id
    ) AS agg
) AS ranked
WHERE rn = 1;


-- ---------------------------------------------------------------------
-- 5. 日期维度：is_weekend 保持 NULL
-- ---------------------------------------------------------------------
-- 数据集只有 mmdd，没有年份。没有确凿的日历年出处就不回填星期，
-- 避免把"猜的星期"当成真实结论写进报告。
-- 将来确认年份后，用类似下面这条语句回填即可，无需改表结构：
--
--   UPDATE dim_date
--   SET is_weekend = IF(WEEKDAY(DATE(CONCAT('<年份>-', LPAD(date_key, 4, '0')))) >= 5, 1, 0);
--
-- 在年份存疑期间，Phase 5/6 不做"工作日 vs 周末"的对比分析。
