-- =====================================================================
-- 天猫复购预测数据集：MySQL 8.0 建库建表脚本（Phase 4）
-- ---------------------------------------------------------------------
-- 目标库：tmall_analysis
-- 数据来源：本地 data/clean/ 下的清洗产物（Phase 3 已核验可复现）
-- 设计原则：
--   1. 字段全部来自真实数据，不虚构金额、库存、城市、订单号等不存在的字段。
--   2. 列类型由 python/probe_id_ranges.py 实测的取值决定，不是凭感觉定的。
--   3. 数据集里没有年份和小时，因此不生成任何"真实日期"或"小时"字段。
--   4. 事实表不建代理主键：分析场景只做聚合，用二级索引即可；
--      重载数据用分区 TRUNCATE，不依赖行级定位。
--
-- 实测证据（python/probe_id_ranges.py 输出）：
--   user_id    1 ~ 424,170      -> MEDIUMINT UNSIGNED (上限 16,777,215)
--   item_id    1 ~ 1,113,166    -> MEDIUMINT UNSIGNED
--   cat_id     1 ~ 1,671        -> SMALLINT UNSIGNED  (上限 65,535)
--   seller_id  1 ~ 4,995        -> SMALLINT UNSIGNED
--   brand_id   1 ~ 8,477，缺失 91,015 -> SMALLINT UNSIGNED NULL
--   merchant_id 2 ~ 4,993       -> SMALLINT UNSIGNED
--
-- 注意：本脚本开头会 DROP 已有表，重跑等于重建，会清空数据。
-- =====================================================================

SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS tmall_analysis
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE tmall_analysis;

-- ---------------------------------------------------------------------
-- 0. 清理（按依赖顺序，先事实后维度）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS fact_user_log;
DROP TABLE IF EXISTS dim_item;
DROP TABLE IF EXISTS dim_brand;
DROP TABLE IF EXISTS dim_category;
DROP TABLE IF EXISTS dim_seller;
DROP TABLE IF EXISTS dim_user;
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS train_sample;
DROP TABLE IF EXISTS test_sample;


-- ---------------------------------------------------------------------
-- 1. 维度表
-- ---------------------------------------------------------------------

-- 用户画像：源自 user_info_format1_format1（424,170 行，user_id 无重复）
CREATE TABLE dim_user (
    user_id           MEDIUMINT UNSIGNED NOT NULL COMMENT '用户ID，实测 1~424170',
    age_range         TINYINT UNSIGNED NULL     COMMENT '年龄区间编码；原始 0 表示未知',
    gender            TINYINT UNSIGNED NULL     COMMENT '0=女 1=男 2/NULL=未知',
    age_range_missing TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '1=原始 age_range 为空',
    gender_missing    TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '1=原始 gender 为空',
    PRIMARY KEY (user_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '用户画像维度（年龄、性别）';


-- 商家维度
-- 行为日志里字段叫 seller_id，训练/测试集里叫 merchant_id，指同一个东西。
-- 注意：seller_id 实测最大 4995，merchant_id 最大 4993，
--       说明有商家只出现在行为日志里、没有出现在样本集中，这是正常的。
CREATE TABLE dim_seller (
    seller_id SMALLINT UNSIGNED NOT NULL COMMENT '商家ID，实测 1~4995；等同于 merchant_id',
    PRIMARY KEY (seller_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '商家维度（seller_id 与 merchant_id 同义）';


-- 品类维度：实测 cat_id 取值 1~1671
CREATE TABLE dim_category (
    cat_id SMALLINT UNSIGNED NOT NULL COMMENT '品类ID，实测 1~1671',
    PRIMARY KEY (cat_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '商品品类维度';


-- 品牌维度：实测 brand_id 取值 1~8477，且事实表中有 91,015 条缺失
CREATE TABLE dim_brand (
    brand_id SMALLINT UNSIGNED NOT NULL COMMENT '品牌ID，实测 1~8477',
    PRIMARY KEY (brand_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '商品品牌维度';


-- 商品维度：数据里没有独立的商品表，item -> cat/brand 的归属关系
-- 只能从行为日志里去重推导（一个 item 可能在不同行出现不同 cat/brand，
-- 建表时取出现次数最多的那一组，推导逻辑写在 sql/build_dim_item.sql）。
CREATE TABLE dim_item (
    item_id  MEDIUMINT UNSIGNED NOT NULL COMMENT '商品ID，实测 1~1113166',
    cat_id   SMALLINT UNSIGNED  NOT NULL COMMENT '该商品所属品类',
    brand_id SMALLINT UNSIGNED  NULL     COMMENT '该商品所属品牌，可为空',
    PRIMARY KEY (item_id),
    KEY idx_cat (cat_id),
    KEY idx_brand (brand_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '商品维度（cat/brand 由行为日志去重推导）';


-- 日期维度
-- 数据集里的 time_stamp 是 mmdd（如 0829），只有月和日，没有年份和小时。
-- 因此这张表：
--   1. 主键用 MMDD 整数，不构造伪日期主键；
--   2. day_of_year 采用非闰年的月份累计天数，与 Phase 3 清洗产物口径完全一致
--      （0511 -> 131，1112 -> 316），否则和事实表 join 不上；
--   3. is_weekend 故意留 NULL：没有确凿的日历年出处就不编造星期几。
--      将来若确认了年份，再用一条 UPDATE 回填即可，不需要改表结构。
CREATE TABLE dim_date (
    date_key    SMALLINT UNSIGNED NOT NULL COMMENT 'MMDD，如 0829',
    month       TINYINT UNSIGNED  NOT NULL COMMENT '月份 5~11',
    day         TINYINT UNSIGNED  NOT NULL COMMENT '日 1~31',
    day_of_year SMALLINT UNSIGNED NOT NULL COMMENT '非闰年相对日序号，实测范围 131~316',
    is_weekend  TINYINT UNSIGNED  NULL     COMMENT 'NULL=未回填；缺少年份，不编造星期',
    days_to_1111 SMALLINT         NULL     COMMENT '距离 11月11日的天数，1111 当天为 0；负数表示双十一之后',
    PRIMARY KEY (date_key)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '日期维度（仅 mmdd 粒度，无年份无小时）';


-- ---------------------------------------------------------------------
-- 2. 事实表：用户行为日志（54,925,330 行）
-- ---------------------------------------------------------------------
-- 分区策略：按 date_key（MMDD 整数）做 RANGE 分区，边界就是每月的 100 位进位。
-- 为什么不用 month 作为分区键：
--   实测发现 PARTITION BY RANGE (month) 时，查询条件写 date_key BETWEEN ...
--   优化器无法从 date_key 反推 month，EXPLAIN 显示 8 个分区全扫，分区裁剪完全失效。
--   改成按 date_key 分区后，date_key 范围条件可以直接裁剪分区。
-- 分区键必须出现在每个唯一键里，所以这张表干脆不建唯一键（见文件头原则 4）。
CREATE TABLE fact_user_log (
    user_id     MEDIUMINT UNSIGNED NOT NULL COMMENT '用户ID',
    item_id     MEDIUMINT UNSIGNED NOT NULL COMMENT '商品ID',
    cat_id      SMALLINT UNSIGNED  NOT NULL COMMENT '品类ID',
    seller_id   SMALLINT UNSIGNED  NOT NULL COMMENT '商家ID（=merchant_id）',
    brand_id    SMALLINT UNSIGNED  NULL     COMMENT '品牌ID，有 91,015 条缺失',
    action_type TINYINT UNSIGNED   NOT NULL COMMENT '0=点击 1=加入购物车 2=购买 3=加入收藏夹',
    time_stamp  CHAR(4)            NOT NULL COMMENT '原始 mmdd 字符串，保留用于回溯',
    date_key    SMALLINT UNSIGNED  NOT NULL COMMENT 'MMDD 整数，用于关联 dim_date，同时是分区键',
    day_of_year SMALLINT UNSIGNED  NOT NULL COMMENT '非闰年相对日序号',
    month       TINYINT UNSIGNED   NOT NULL COMMENT '月份 5~11，取自 time_stamp 前两位',
    KEY idx_date_action (date_key, action_type),
    KEY idx_user_date (user_id, date_key),
    KEY idx_seller_action (seller_id, action_type),
    KEY idx_item_date (item_id, action_type),
    KEY idx_cat_action (cat_id, action_type),
    KEY idx_month_action (action_type, date_key)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  ROW_FORMAT = DYNAMIC
  COMMENT = '用户行为事实表，5492 万行，按 date_key 月分区'
PARTITION BY RANGE (date_key) (
    PARTITION p05 VALUES LESS THAN (600),
    PARTITION p06 VALUES LESS THAN (700),
    PARTITION p07 VALUES LESS THAN (800),
    PARTITION p08 VALUES LESS THAN (900),
    PARTITION p09 VALUES LESS THAN (1000),
    PARTITION p10 VALUES LESS THAN (1100),
    PARTITION p11 VALUES LESS THAN (1200),
    PARTITION pmax VALUES LESS THAN MAXVALUE
);


-- ---------------------------------------------------------------------
-- 3. 样本表
-- ---------------------------------------------------------------------

-- 训练集：260,864 行
CREATE TABLE train_sample (
    user_id     MEDIUMINT UNSIGNED NOT NULL COMMENT '用户ID',
    merchant_id SMALLINT UNSIGNED  NOT NULL COMMENT '商家ID',
    label       TINYINT            NOT NULL COMMENT '1=复购买家 0=非复购买家 -1=非该商家新客户，不在预测范围',
    KEY idx_user (user_id),
    KEY idx_merchant (merchant_id),
    KEY idx_label (label)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '复购预测训练集';


-- 测试集：261,477 行，prob 原始为空，是待预测目标
CREATE TABLE test_sample (
    user_id     MEDIUMINT UNSIGNED NOT NULL COMMENT '用户ID',
    merchant_id SMALLINT UNSIGNED  NOT NULL COMMENT '商家ID',
    prob        DECIMAL(8, 6)      NULL     COMMENT '待预测的复购概率；原始数据为空',
    KEY idx_user (user_id),
    KEY idx_merchant (merchant_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '复购预测测试集（prob 待填）';


-- ---------------------------------------------------------------------
-- 4. 日期维度数据：0511 ~ 1112，共 186 天
-- ---------------------------------------------------------------------
-- 只用非闰年做日历算术，产物是 month/day/day_of_year，
-- 不写入任何"真实年份"，避免编造。用 2001 年是因为它非闰年。
INSERT INTO dim_date (date_key, month, day, day_of_year, is_weekend, days_to_1111)
WITH RECURSIVE seq (n) AS (
    SELECT 0
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 185
)
SELECT
    CAST(DATE_FORMAT(d, '%m%d') AS UNSIGNED) AS date_key,
    MONTH(d)                                 AS month,
    DAYOFMONTH(d)                            AS day,
    DAYOFYEAR(d)                             AS day_of_year,
    NULL                                     AS is_weekend,
    DATEDIFF(DATE('2001-11-11'), d)          AS days_to_1111
FROM (
    SELECT DATE_ADD(DATE('2001-05-11'), INTERVAL n DAY) AS d
    FROM seq
) AS dates;
