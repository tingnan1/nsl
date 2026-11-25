-- =====================================================================
-- 数据导入脚本（Phase 4）
-- ---------------------------------------------------------------------
-- 前置条件：
--   1. 已执行 sql/create_table.sql 建好库和表。
--   2. mysqld 启动了 local_infile：mysql/start_mysql_local.ps1 里已包含
--      --local_infile=ON；如果是标准服务，需要在 my.ini 的 [mysqld] 段加。
--   3. 客户端也要加 --local-infile=1。
--   4. 执行命令（PowerShell，必须用 8.0 客户端，别用 PATH 里那个 5.6）：
--        cd D:\ChatGPT\数据分析实战
--        $env:MYSQL_PWD = '<你的密码>'
--        & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" `
--          -u root -h 127.0.0.1 -P 3306 --default-character-set=utf8mb4 `
--          --local-infile=1 < sql\load_data.sql
--
-- 【必须从项目根目录执行】因为下面用的是相对路径。
--   不能用绝对路径：项目目录含中文（数据分析实战），mysql 客户端把路径按
--   ANSI 代码页转换后会打不开文件，报 ERROR 2 (HY000) ... not found (OS errno 2)。
--   相对路径不含非 ASCII 字符，实测可正常读取。
--
-- 文件格式（已用字节级检查确认，不要凭经验猜）：
--   user_log_format1_clean.csv : 无 BOM，行尾 CRLF
--   train/test/user_info_clean : 有 UTF-8 BOM，行尾 CRLF
--   BOM 位于表头行，IGNORE 1 LINES 会把整行（含 BOM）跳过，所以无需额外处理。
--   空字段必须显式转 NULL，否则 numeric 列会写入 0，把"缺失"变成"真实的0"。
--
-- 【性能关键：先删索引再装，装完重建】
--   分区表 + 6 个二级索引的情况下逐行插入会严重写放大。本机实测：
--     带索引直接 LOAD：20 分钟只进了 650 万行，推算全量约 3 小时；
--     删索引后 LOAD  ：6.3 分钟装完 54,925,330 行；
--     一次性重建 6 个索引：2.5 分钟。
--   所以下面的顺序是：TRUNCATE -> 删索引 -> LOAD -> 建索引。
--   索引定义必须和 create_table.sql 里保持完全一致。
-- =====================================================================

SET NAMES utf8mb4;

USE tmall_analysis;

-- 清空，保证脚本可重复执行（分区表也用 TRUNCATE，结构保留）。
TRUNCATE TABLE fact_user_log;
TRUNCATE TABLE train_sample;
TRUNCATE TABLE test_sample;
TRUNCATE TABLE dim_user;

-- 1) 装载前删掉二级索引（表已空，删除是瞬时的）
ALTER TABLE fact_user_log
    DROP INDEX idx_date_action,
    DROP INDEX idx_user_date,
    DROP INDEX idx_seller_action,
    DROP INDEX idx_item_date,
    DROP INDEX idx_cat_action,
    DROP INDEX idx_month_action;


-- ---------------------------------------------------------------------
-- 事实表：用户行为日志，54,925,330 行（约 2.30 GB）
-- ---------------------------------------------------------------------
-- 文件列顺序：
--   user_id,item_id,cat_id,seller_id,brand_id,time_stamp,action_type,month,day,day_of_year
-- 表里没有 day 列（日粒度由 dim_date 提供），用 @skip_day 接住丢掉。
-- date_key 由原始 mmdd 字符串现算，保证和 time_stamp 逻辑同源。
-- brand_id 有 91,015 条空值，必须 NULLIF 转 NULL。
LOAD DATA LOCAL INFILE 'data/clean/user_log_format1_clean.csv'
INTO TABLE fact_user_log
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ','
       OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(user_id, item_id, cat_id, seller_id, @brand_id, time_stamp, action_type,
 month, @skip_day, day_of_year)
SET brand_id = NULLIF(@brand_id, ''),
    date_key = CAST(time_stamp AS UNSIGNED);


-- ---------------------------------------------------------------------
-- 训练集：260,864 行
-- ---------------------------------------------------------------------
LOAD DATA LOCAL INFILE 'data/clean/train_format1_clean.csv'
INTO TABLE train_sample
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ','
       OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(user_id, merchant_id, label);


-- ---------------------------------------------------------------------
-- 测试集：261,477 行，prob 原始为空，必须存成 NULL 而不是 0
-- ---------------------------------------------------------------------
LOAD DATA LOCAL INFILE 'data/clean/test_format1_clean.csv'
INTO TABLE test_sample
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ','
       OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(user_id, merchant_id, @prob)
SET prob = NULLIF(@prob, '');


-- ---------------------------------------------------------------------
-- 用户画像维度：424,170 行
-- ---------------------------------------------------------------------
-- 文件列顺序与 dim_user 列顺序一致：
--   user_id,age_range,gender,age_range_missing,gender_missing
-- age_range / gender 里的 0 是原始编码（表示未知），要保留为 0，不能转 NULL；
-- 只有真正的空字段才转 NULL。
LOAD DATA LOCAL INFILE 'data/clean/user_info_format1_clean.csv'
INTO TABLE dim_user
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ','
       OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(user_id, @age_range, @gender, age_range_missing, gender_missing)
SET age_range = NULLIF(@age_range, ''),
    gender    = NULLIF(@gender, '');


-- 2) 装载完成后一次性重建全部二级索引（一条 ALTER 只扫一遍表，比 6 条独立 ALTER 快）
SET SESSION sort_buffer_size = 256 * 1024 * 1024;

ALTER TABLE fact_user_log
    ADD INDEX idx_date_action (date_key, action_type),
    ADD INDEX idx_user_date (user_id, date_key),
    ADD INDEX idx_seller_action (seller_id, action_type),
    ADD INDEX idx_item_date (item_id, action_type),
    ADD INDEX idx_cat_action (cat_id, action_type),
    ADD INDEX idx_month_action (action_type, date_key);

-- 刷新优化器统计信息（否则 information_schema 里的行数是过期估算，会误导执行计划）
ANALYZE TABLE fact_user_log, train_sample, test_sample, dim_user;
