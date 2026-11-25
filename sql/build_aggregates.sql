-- =====================================================================
-- 物化聚合表（Phase 10 后端前置）
-- ---------------------------------------------------------------------
-- 为什么需要：
--   /api/actions 要算 COUNT(DISTINCT user_id) GROUP BY action_type，
--   在 54,925,330 行上实测要 100 秒以上；/api/segments 要 45 秒。
--   这类结果只有 4 行 / 2 行，属于典型的「算得慢、用得多」，
--   正确做法是物化一次、后续直接读小表。
--
-- 执行（只需一次，数据不变时无需重跑）：
--   $env:MYSQL_PWD = '<见 .env>'
--   & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" `
--       -u root -h 127.0.0.1 -P 3306 --default-character-set=utf8mb4 `
--       < sql\build_aggregates.sql
--
-- 实测耗时：动作聚合约 100–200 秒，分层聚合约 45 秒。
-- 数据重新导入后需要重跑本脚本，否则聚合表会与事实表不一致。
-- =====================================================================

USE tmall_analysis;


-- ---------------------------------------------------------------------
-- 1. 四类行为累计聚合（4 行）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS agg_action_summary;

CREATE TABLE agg_action_summary (
    action_type TINYINT UNSIGNED NOT NULL,
    action_name VARCHAR(20)      NOT NULL,
    events      BIGINT UNSIGNED  NOT NULL,
    users       BIGINT UNSIGNED  NOT NULL,
    PRIMARY KEY (action_type)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '四类行为的累计事件数与去重用户数（物化，源：fact_user_log）';

INSERT INTO agg_action_summary (action_type, action_name, events, users)
SELECT
    action_type,
    CASE action_type WHEN 0 THEN '点击'
                     WHEN 1 THEN '加入购物车'
                     WHEN 2 THEN '购买'
                     ELSE '加入收藏夹' END,
    COUNT(*),
    COUNT(DISTINCT user_id)
FROM fact_user_log
GROUP BY action_type;


-- ---------------------------------------------------------------------
-- 2. 用户分层复购率（2 行）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS agg_segment_repurchase;

CREATE TABLE agg_segment_repurchase (
    segment         VARCHAR(40)  NOT NULL,
    samples         INT UNSIGNED NOT NULL,
    repurchase      INT UNSIGNED NOT NULL,
    repurchase_rate DECIMAL(6,2) NOT NULL,
    PRIMARY KEY (segment)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '1111 前是否购买 × 官方复购标签（物化，源：fact_user_log + train_sample）';

INSERT INTO agg_segment_repurchase (segment, samples, repurchase, repurchase_rate)
WITH pre AS (
    SELECT user_id, COUNT(*) AS cnt
    FROM fact_user_log
    WHERE action_type = 2 AND date_key < 1111
    GROUP BY user_id
)
SELECT
    CASE WHEN p.user_id IS NULL THEN '1111前无购买（新客）'
         ELSE '1111前有购买（老客）' END,
    COUNT(*),
    SUM(t.label),
    ROUND(100.0 * SUM(t.label) / COUNT(*), 2)
FROM train_sample t
LEFT JOIN pre p ON p.user_id = t.user_id
GROUP BY 1;


SELECT * FROM agg_action_summary ORDER BY action_type;
SELECT * FROM agg_segment_repurchase ORDER BY repurchase_rate DESC;

-- ---------------------------------------------------------------------
-- 3. 每日聚合（186 行）
-- ---------------------------------------------------------------------
-- /api/daily 和 /api/double11 都要按日去重 UV，现算实测 58 秒。
-- 日粒度只有 186 行，物化后接口降到毫秒级。
--
-- effective 字段说明：
--   日志名义覆盖 186 天，但 1112 只有 46 行、0 次购买——是数据截断的尾巴。
--   把它算进「日均」会低估真实强度（含 1112 是 295,297，排除后是 296,893）。
--   所以这里显式打标记，日均类指标一律只统计 effective = 1 的 185 天，
--   而不是悄悄过滤掉——原始数据保持完整，口径显式可查。
DROP TABLE IF EXISTS agg_daily;

CREATE TABLE agg_daily (
    date_key  SMALLINT UNSIGNED NOT NULL,
    events    BIGINT UNSIGNED   NOT NULL,
    purchases BIGINT UNSIGNED   NOT NULL,
    uv        INT UNSIGNED      NOT NULL,
    effective TINYINT UNSIGNED  NOT NULL DEFAULT 1
        COMMENT '1=纳入统计口径；0=截断尾（1112 仅 46 行、0 次购买）',
    PRIMARY KEY (date_key)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = '每日行为量/购买事件/去重UV（物化，源：fact_user_log）';

INSERT INTO agg_daily (date_key, events, purchases, uv, effective)
SELECT date_key,
       COUNT(*),
       SUM(action_type = 2),
       COUNT(DISTINCT user_id),
       IF(date_key = 1112, 0, 1)
FROM fact_user_log
GROUP BY date_key;


-- ---------------------------------------------------------------------
-- 4. 一致性断言
-- ---------------------------------------------------------------------
-- 物化表最危险的失效方式不是报错，而是数据重新导入后没人重跑本脚本，
-- 接口继续返回旧数字且完全不报错。所以这里主动比对。
SELECT
    (SELECT COUNT(*) FROM fact_user_log)          AS fact_rows,
    (SELECT SUM(events) FROM agg_daily)           AS agg_events,
    (SELECT SUM(action_type = 2) FROM fact_user_log) AS fact_purchases,
    (SELECT SUM(purchases) FROM agg_daily)        AS agg_purchases,
    IF(
        (SELECT COUNT(*) FROM fact_user_log) = (SELECT SUM(events) FROM agg_daily)
        AND (SELECT SUM(action_type = 2) FROM fact_user_log)
            = (SELECT SUM(purchases) FROM agg_daily)
        AND (SELECT SUM(events) FROM agg_action_summary) = (SELECT COUNT(*) FROM fact_user_log),
        'PASS',
        'FAIL —— 物化表与事实表不一致，接口会返回旧数据'
    ) AS consistency_check;

