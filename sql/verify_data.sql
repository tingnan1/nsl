-- =====================================================================
-- 导入后校验脚本（Phase 4）
-- ---------------------------------------------------------------------
-- 执行顺序：create_table.sql -> load_data.sql -> build_dims.sql -> verify_data.sql
--
-- 作用：把 Phase 3 已经核验过的真实数字写成硬性校验，
--       任何一项 FAIL 就说明导入过程中丢了行、错了类型或把缺失值变成了 0。
-- 注意：这里的"预期值"全部来自 Phase 3 实测输出，不是拍脑袋写的。
-- =====================================================================

SET NAMES utf8mb4;

USE tmall_analysis;


-- ---------------------------------------------------------------------
-- 一、硬性校验：必须全部 PASS
-- ---------------------------------------------------------------------
SELECT 'fact_user_log 行数'  AS 检查项, '54,925,330' AS 预期,
       FORMAT(COUNT(*), 0)   AS 实际,
       IF(COUNT(*) = 54925330, 'PASS', 'FAIL') AS 结果
FROM fact_user_log

UNION ALL
SELECT 'action_type=0 点击', '48,550,713', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 48550713, 'PASS', 'FAIL')
FROM fact_user_log WHERE action_type = 0

UNION ALL
SELECT 'action_type=1 加购', '76,750', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 76750, 'PASS', 'FAIL')
FROM fact_user_log WHERE action_type = 1

UNION ALL
SELECT 'action_type=2 购买', '3,292,144', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 3292144, 'PASS', 'FAIL')
FROM fact_user_log WHERE action_type = 2

UNION ALL
SELECT 'action_type=3 收藏', '3,005,723', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 3005723, 'PASS', 'FAIL')
FROM fact_user_log WHERE action_type = 3

UNION ALL
SELECT 'brand_id 缺失(应为 NULL 不是 0)', '91,015', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 91015, 'PASS', 'FAIL')
FROM fact_user_log WHERE brand_id IS NULL

UNION ALL
SELECT 'brand_id 被误写成 0 的行数', '0', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 0, 'PASS', 'FAIL')
FROM fact_user_log WHERE brand_id = 0

UNION ALL
SELECT 'train_sample 行数', '260,864', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 260864, 'PASS', 'FAIL')
FROM train_sample

UNION ALL
SELECT 'test_sample 行数', '261,477', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 261477, 'PASS', 'FAIL')
FROM test_sample

UNION ALL
SELECT 'test_sample prob 全为空', '261,477', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 261477, 'PASS', 'FAIL')
FROM test_sample WHERE prob IS NULL

UNION ALL
SELECT 'dim_user 行数', '424,170', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 424170, 'PASS', 'FAIL')
FROM dim_user

UNION ALL
SELECT 'dim_user age_range 缺失', '2,217', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 2217, 'PASS', 'FAIL')
FROM dim_user WHERE age_range IS NULL

UNION ALL
SELECT 'dim_user gender 缺失', '6,436', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 6436, 'PASS', 'FAIL')
FROM dim_user WHERE gender IS NULL

UNION ALL
SELECT 'dim_date 天数(0511~1112)', '186', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 186, 'PASS', 'FAIL')
FROM dim_date

UNION ALL
SELECT 'date_key 与 time_stamp 不一致的行', '0', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 0, 'PASS', 'FAIL')
FROM fact_user_log WHERE date_key <> CAST(time_stamp AS UNSIGNED)

UNION ALL
SELECT 'day_of_year 与 dim_date 不一致的行', '0', FORMAT(COUNT(*), 0),
       IF(COUNT(*) = 0, 'PASS', 'FAIL')
FROM fact_user_log f JOIN dim_date d ON d.date_key = f.date_key
WHERE f.day_of_year <> d.day_of_year

UNION ALL
SELECT 'fact 出现但 user 维度缺失的用户数', '0', FORMAT(COUNT(DISTINCT f.user_id), 0),
       IF(COUNT(DISTINCT f.user_id) = 0, 'PASS', 'FAIL')
FROM fact_user_log f LEFT JOIN dim_user u ON u.user_id = f.user_id
WHERE u.user_id IS NULL

UNION ALL
SELECT 'fact 出现但 brand 维度缺失的品牌数', '0', FORMAT(COUNT(DISTINCT f.brand_id), 0),
       IF(COUNT(DISTINCT f.brand_id) = 0, 'PASS', 'FAIL')
FROM fact_user_log f LEFT JOIN dim_brand b ON b.brand_id = f.brand_id
WHERE f.brand_id IS NOT NULL AND b.brand_id IS NULL

UNION ALL
SELECT 'train 出现但 seller 维度缺失的商家数', '0', FORMAT(COUNT(DISTINCT t.merchant_id), 0),
       IF(COUNT(DISTINCT t.merchant_id) = 0, 'PASS', 'FAIL')
FROM train_sample t LEFT JOIN dim_seller s ON s.seller_id = t.merchant_id
WHERE s.seller_id IS NULL;


-- ---------------------------------------------------------------------
-- 二、信息性统计：只展示，不做 PASS/FAIL
-- ---------------------------------------------------------------------

-- 分区实际行数分布（确认分区裁剪真的按月份落位）
SELECT
    PARTITION_NAME       AS 分区,
    TABLE_ROWS           AS 估算行数,
    PARTITION_DESCRIPTION AS 边界上限
FROM information_schema.PARTITIONS
WHERE TABLE_SCHEMA = 'tmall_analysis'
  AND TABLE_NAME = 'fact_user_log'
ORDER BY PARTITION_ORDINAL_POSITION;

-- 训练集标签分布（label=-1 是"非该商家新客户"，不在预测范围内）
SELECT
    label,
    COUNT(*)  AS 样本数,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM train_sample), 2) AS 占比百分比
FROM train_sample
GROUP BY label
ORDER BY label;

-- 维度表行数一览
SELECT 'dim_user'     AS 表名, COUNT(*) AS 行数 FROM dim_user
UNION ALL SELECT 'dim_seller',   COUNT(*) FROM dim_seller
UNION ALL SELECT 'dim_category', COUNT(*) FROM dim_category
UNION ALL SELECT 'dim_brand',    COUNT(*) FROM dim_brand
UNION ALL SELECT 'dim_item',     COUNT(*) FROM dim_item
UNION ALL SELECT 'dim_date',     COUNT(*) FROM dim_date;

-- 训练/测试集里 (user_id, merchant_id) 是否唯一
SELECT
    'train' AS 数据集,
    COUNT(*) - COUNT(DISTINCT user_id, merchant_id) AS 重复用户商家组合数
FROM train_sample
UNION ALL
SELECT 'test', COUNT(*) - COUNT(DISTINCT user_id, merchant_id)
FROM test_sample;
