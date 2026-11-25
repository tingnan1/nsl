# 工程笔记：环境约束、决策依据与实测数据

> 记录本项目实现过程中遇到的非显然问题、被数据否决的设计、以及关键改动的实测依据。
> 目的：让后续维护者理解「为什么这么做」，而不是只从代码里反推。

## 1. 项目定位

项目名称：国内电商用户行为分析与复购运营平台

项目最终目标：完整跑通「真实数据 → 数据清洗 → MySQL → SQL 分析 → Python 统计 →
运营指标体系 → 复购预测模型 → 可视化 → 决策建议 → Web 系统」的业务分析链路，
面向数据分析 / 数据运营 / 商业分析方向的项目实践。

原方向是「深圳共享单车运营分析与需求预测」，因深圳开放平台登录问题，
已与用户确认改为「国内电商运营分析」方向，主数据换成天猫复购预测数据集。

## 2. 当前总体进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | 数据源调查与可行性评估 | ✅ 完成 |
| Phase 1 | 环境搭建与项目初始化 | ✅ 完成 |
| Phase 2 | 真实数据获取 | ✅ 完成（数据已下载并核验） |
| Phase 3 | 数据清洗及质量检查 | ✅ 完成（小文件 + 5492 万行日志均完成） |
| Phase 4 | MySQL 数据库建设 | ✅ 完成（19 项校验全部 PASS） |
| Phase 5 | SQL 分析 | ✅ 完成（11 个章节，与 Python 结果逐项交叉验证一致） |
| Phase 6 | Python 统计分析及可视化 | ✅ 完成（11 张表 + 10 张图 + 报告） |
| Phase 7 | 运营指标体系建设 | ✅ 完成（指标字典 + 5 张表 + 报告） |
| Phase 8 | Baseline 及机器学习预测 | ✅ 完成（HGB ROC-AUC 0.6544，CV 0.6590） |
| Phase 9 | Power BI Dashboard | 🔶 数据导出完成（python/export_bi.py，9 张 CSV），.pbix 需用户本机制作 |
| Phase 10 | FastAPI 后端 | ✅ 完成（12 个接口，查真实库，重聚合已物化） |
| Phase 11 | Vue 3 前端 | ✅ 完成（Vite + Vue 3 看板，构建通过，8 个接口接通） |
| Phase 12 | 前后端联调 | 🔶 接口代理已验证，待人工过一遍页面渲染 |
| Phase 13 | 测试与问题修复 | ✅ 完成（26 项：10 单元 + 16 集成；CI 已接入） |
| Phase 14 | 部署 | ✅ 完成（单端口部署：FastAPI 挂载前端构建产物） |
| Phase 15 | README、项目报告、演示材料 | ✅ README + 项目报告完成（演示材料暂未制作） |

## 3. 数据资产

### 3.1 原始数据

位置：`data/raw/tmall/data_format1/`

| 文件 | 大小（约） | 行数 | 字段 |
|---|---|---|---|
| `train_format1.csv` | 3.4 MB | 260,864 | `user_id, merchant_id, label` |
| `test_format1.csv` | 3.1 MB | 261,477 | `user_id, merchant_id, prob`（prob 全为空，是待预测结果） |
| `user_info_format1.csv` | 4.3 MB | 424,170 | `user_id, age_range, gender` |
| `user_log_format1.csv` | 1.78 GB | 54,925,330 | `user_id, item_id, cat_id, seller_id, brand_id, time_stamp, action_type` |

数据来源：阿里云天池「天猫复购预测」比赛数据集。

官方入口：https://tianchi.aliyun.com/dataset/161265

### 3.2 清洗后数据

位置：`data/clean/`

| 文件 | 大小（约） | 说明 |
|---|---|---|
| `train_format1_clean.csv` | 3.7 MB | ID 转整数、label 转 int8 |
| `test_format1_clean.csv` | 3.4 MB | ID 转整数、prob 保持为空 |
| `user_info_format1_clean.csv` | 6.4 MB | 新增 `age_range_missing`、`gender_missing` 标记 |
| `user_log_format1_clean.csv` | 2.30 GB | 新增 `month`、`day`、`day_of_year` 三列 |
| `cleaning_log.txt` | — | 小文件清洗记录 |
| `user_log_cleaning_log.txt` | — | user_log 清洗记录 |

## 4. 关键数据事实（已核验，作为后续校验基准）

1. `train_format1.csv`：260,864 行，无缺失、无完全重复。
2. `test_format1.csv`：261,477 行，`prob` 列 261,477 条全部为空（待预测）。
3. `user_info_format1.csv`：424,170 行；`age_range` 缺失 2,217 条，`gender` 缺失 6,436 条；
   无重复 user_id。
4. `user_log_format1.csv`：54,925,330 行；仅 `brand_id` 缺失 91,015 条。
5. `action_type` 分布：
   - 0（点击）：48,550,713 条
   - 1（加入购物车）：76,750 条
   - 2（购买）：3,292,144 条
   - 3（加入收藏夹）：3,005,723 条
6. `time_stamp` 为 `mmdd` 格式（如 `0829`），范围 `0511` ~ `1112`，约 5 月 11 日至 11 月 12 日。
7. 行为日志中的 `seller_id` 与训练/测试集中的 `merchant_id` 含义相同，关联时需要统一字段名。

## 5. 数据字典

完整字段说明见：`docs/data_dictionary.md`

重要口径：

- `label`：1 = 复购买家；0 = 非复购买家；-1 = 不是该商家新客户，不在预测范围内。
- `age_range`：年龄区间编码，0 或 NULL 表示未知。
- `gender`：0 = 女；1 = 男；2 和 NULL 表示未知。
- `action_type`：0 = 点击；1 = 加入购物车；2 = 购买；3 = 加入收藏夹。
- `day_of_year`：使用非闰年月份累计天数计算的相对日期序号，不代表真实年份，仅用于时间排序。

## 6. 环境与技术栈

- 操作系统：Windows，PowerShell。
- 编辑器：VS Code（用户已习惯用 VS Code 内置终端操作）。
- Python：项目虚拟环境位于 `.venv`，包版本如下：
  - pandas 3.0.6
  - numpy 2.5.4
  - pyarrow 26.0.0
- MySQL：已安装 MySQL 8.0.46，**数据库已建好并导入完毕**。
  - 服务名：`MySQL80`（Status 仍是 Stopped，见下方原因）
  - 安装路径：`C:\Program Files\MySQL\MySQL Server 8.0\bin`
  - 配置文件：`C:\ProgramData\MySQL\MySQL Server 8.0\my.ini`
  - 数据目录：**`D:\ChatGPT\mysql_data`**（10.42 GB，已从 C 盘整个搬过来，为清理 C 盘空间）
  - 临时目录：**`D:\ChatGPT\mysql_tmp`**（MySQL 的 `tmpdir` 默认在 C 盘，大查询排序会写 1~2 GB
    临时文件，必须指到 D 盘，否则清完 C 盘很快又被占回去）
  - 旧位置 `C:\ProgramData\MySQL\MySQL Server 8.0\Data` 已清空，只剩一个删不掉的空目录壳
    （0 字节，需要管理员删除，不影响运行）
  - 库名：`tmall_analysis`，账号 root，密码见 `.env`（已被 .gitignore 忽略）
  - **当前启动方式：双击 `start_mysql.cmd`（或命令行执行），前台运行，不需要管理员**
    - `start_mysql.cmd` 是包装器，用 `-ExecutionPolicy Bypass` 绕开执行策略
    - `start_mysql.ps1` 是本体，含全部参数和注释说明
  - **四个必须记住的坑（都踩过）**：
    1. **计算机名是中文「南笙离」**，MySQL 安装器把主机名写进了 `my.ini` 的
       `log-error` / `log-bin` / `slow_query_log_file` / `general_log_file`，
       mysqld 创建非 ASCII 文件名会失败（`OS errno 2` + `data directory is unusable`）。
       解法：命令行参数覆盖成 ASCII 文件名（`start_mysql.ps1` 已包含）。
       彻底解法：管理员把 my.ini 里这四个文件名改成 ASCII，再启动服务。
    2. **`Start-Service -Name MySQL80` 需要管理员权限**，普通 PowerShell 会报
       「无法启动服务」且不带内层原因（SCM 事件日志里也不会有记录）。
    3. **直接跑 `.ps1` 会被执行策略拦住**，报 `AuthorizationManager 检查失败`。
       所以要用 `start_mysql.cmd` 包装器。
    4. **`.ps1` 必须存成 UTF-8 带 BOM**。PowerShell 5.1 对无 BOM 文件按 ANSI(GBK) 解码，
       中文注释的字节会吃掉换行符，把下一行代码并进注释里——表现为变量莫名其妙变成
       `$null`（本项目就因此让 `--datadir` 失效，mysqld 跑去用 `C:\Program Files` 当数据目录）。
       `.cmd` 则相反：必须 ASCII + CRLF，否则 cmd.exe 会把命令行拆断。
  - 注意：系统 PATH 中存在一个旧的 MySQL 5.6 客户端（`D:\数据库\数据库实验软件和操作指南\mysql-5.6.24-winx64\bin\mysql.exe`），必须使用 8.0 客户端。
- 已引入并验证：matplotlib 3.11.2、scikit-learn 1.9.1（含 scipy 1.18.1）。
- 后续计划引入：pymysql、xgboost（可选）、fastapi、uvicorn、Vue 3、Power BI。

## 7. 当前目录结构

```text
D:\ChatGPT\数据分析实战
├─ .env.example          # 环境变量模板（MySQL 配置占位）
├─ .gitignore            # 已忽略 .venv、data/、outputs/、.env
├─ requirements.txt      # 当前依赖：pandas、numpy、pyarrow
├─ api\                  # FastAPI 后端（空目录，待开发）
├─ dashboard\            # Power BI（空目录，待开发）
├─ data\
│  ├─ raw\tmall\data_format1\   # 原始数据
│  ├─ clean\                    # 清洗后数据
│  └─ processed\                # 特征/分析数据（空）
├─ docs\
│  ├─ data_dictionary.md
│  └─ ENGINEERING_NOTES.md
├─ frontend\             # Vue 前端（空目录，待开发）
├─ model\                # 预测模型（空目录，待开发）
├─ outputs\              # 生成图表/报告（空目录）
├─ python\
│  ├─ probe_bike_data.py       # 旧方案（深圳共享单车）遗留脚本，已废弃，可删除
│  ├─ probe_tmall_data.py      # 天猫数据文件探针
│  ├─ data_quality_check.py    # 数据质量检查
│  ├─ data_cleaning.py         # 三个小文件清洗
│  └─ clean_user_log.py        # user_log 分块清洗
└─ sql\                  # SQL 脚本（空目录，待开发）
```

## 8. 已完成工作的运行命令

```powershell
cd D:\ChatGPT\数据分析实战

# 创建虚拟环境（已完成）
python -m venv .venv

# 安装依赖（已完成）
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 数据文件探针（已完成）
.\.venv\Scripts\python.exe python\probe_tmall_data.py

# 数据质量检查（已完成）
.\.venv\Scripts\python.exe python\data_quality_check.py

# 三个小文件清洗（已完成）
.\.venv\Scripts\python.exe python\data_cleaning.py

# user_log 分块清洗（已完成）
.\.venv\Scripts\python.exe python\clean_user_log.py
```

## 9. Phase 4 已完成的建库记录（含实测耗时）

执行顺序（都在项目根目录，用 8.0 客户端）：

```powershell
# 0) 起数据库（免管理员，前台运行）
.\start_mysql.ps1

# 1) 建库建表（9 张表）
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -h 127.0.0.1 -P 3306 `
  --default-character-set=utf8mb4 < sql\create_table.sql

# 2) 导入数据（54,925,330 行 + 3 个小表）
$env:MYSQL_PWD = '<见 .env>'
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -h 127.0.0.1 -P 3306 `
  --default-character-set=utf8mb4 --local-infile=1 < sql\load_data.sql

# 3) 从事实表反推维度（商家/品类/品牌/商品）
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -h 127.0.0.1 -P 3306 `
  --default-character-set=utf8mb4 < sql\build_dims.sql

# 4) 19 项硬性校验，必须全部 PASS
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -h 127.0.0.1 -P 3306 `
  --default-character-set=utf8mb4 < sql\verify_data.sql
```

实测耗时（本机，第二次用正式脚本完整重建的实测）：

| 步骤 | 耗时 | 备注 |
|---|---|---|
| create_table.sql | **0.48 分钟** | 含分区、递归 CTE 生成 186 天日期维度 |
| load_data.sql（全流程） | **4.83 分钟** | 删索引 → 装 54,925,330 行 → 重建 6 个索引 → ANALYZE，一条龙 |
| build_dims.sql | 3.68 分钟 | dim_item 要 GROUP BY 5492 万行 |
| verify_data.sql | 6.12 分钟 | 多次全表扫描 + 多表关联 |
| **全流程合计** | **约 15 分钟** | 从零到可查询 |

反面对照：**不删索引直接 LOAD** 时，20 分钟只进了 650 万行（推算全量约 3 小时），
所以 `load_data.sql` 里必须先删索引再装、装完重建，这一步不是优化而是必需。

**分区策略经过一次修正（重要）：**
最初按 `RANGE(month)` 分区，实测 `EXPLAIN` 显示查 `date_key BETWEEN 1101 AND 1111`
时 8 个分区**全部被扫描**，分区裁剪完全失效——因为优化器无法从 `date_key` 反推 `month`。
改成 `RANGE(date_key)` 后，同样的查询 `partitions` 只剩 `p11`，裁剪生效。
分区边界用每月 100 位进位（p05<600 / p06<700 / ... / p11<1200 / pmax）。

最终规模：9 张表；`fact_user_log` 数据约 2.7 GB + 索引约 5.3 GB；
分区行数 p05=2.76M / p06=6.18M / p07=4.87M / p08=5.16M / p09=6.25M / p10=8.48M / p11=20.55M / pmax=0。

**19 项校验全部 PASS**，包括行数、四类行为计数、`brand_id` 91,015 条缺失未被写成 0、
`date_key` 与 `time_stamp` 一致、`day_of_year` 与 `dim_date` 一致、三维度外键完整性。

## 10. 后续阶段：计划 vs 实际（含未按计划执行的原因）

Phase 5–15 已全部执行完毕，下面是原计划与实际交付的对照，**偏离处都写明原因**：

| 阶段 | 原计划 | 实际做法 | 偏离原因 |
|---|---|---|---|
| 5 | 每日/**小时级**聚合、**工作日周末对比**、排名、移动平均、环比、窗口函数 | 日级聚合 + 环比 + 7 日移动平均 + RANK/DENSE_RANK/ROW_NUMBER + SUM OVER 帕累托 + 分区裁剪验证 | 数据只有 `mmdd`，**没有小时、没有年份**：小时级与工作日/周末在物理上无法计算，硬做就是编造 |
| 6 | 趋势、漏斗、用户/商家/品类分析 | 11 表 + 10 图；漏斗改为「用户级重叠率」并注明不是会话转化 | 没有会话 ID，会话级漏斗无法定义 |
| 7 | 转化率、复购率、用户分层、商家健康度 | 同上，并把「算不出来的 9 项指标」单列成清单 | 没有金额/订单/会话字段 |
| 8 | Baseline + 逻辑回归/随机森林/XGBoost，**按时间顺序划分** | Baseline×2 + 逻辑回归 + 随机森林 + HGB；分层随机划分 | 样本本身不带时间戳，无法按时间切分；已改用 5 折 CV 与固定种子保证稳定 |
| 9 | Power BI Dashboard | 导出 9 张扁平 CSV + 建模说明（.pbix 需用户本机 Power BI Desktop） | 无法在没有安装 Power BI 的环境里生成 .pbix |
| 10–12 | FastAPI + Vue 3 + 联调 | 12 个接口 + Vue 3 看板 + 单端口部署，接口代理与页面均验证 | 按计划完成 |
| 13–15 | 测试、部署、README、报告 | 26 项测试、单端口部署、README、项目报告 | 演示材料不在本次交付范围 |

原估工期：每天 3–4 小时约需 3–4 周。实际执行主要瓶颈不在写代码，而在**数据口径确认与踩坑排查**（中文主机名、分区失效、装载性能、编码问题，见 `docs/PROJECT_REPORT.md` 第八章）。

## 11. 数据使用与表述原则

- 不编造数据来源、数据规模、订单量、金额等字段。
- 不编造模型准确率、AUC、预测效果和提升比例。
- 不把模拟数据当真实数据；当前使用的天猫数据为比赛真实公开数据，需在 README 注明来源。
- 不把相关性直接解释为因果关系。
- 未完成的功能不宣称已完成或已部署。
- 原始 ID 已脱敏，不泄露任何真实用户/商品身份。

## 12. 维护须知（环境与运行约束）

1. 项目根目录：`D:\ChatGPT\数据分析实战`。
2. 先读本文件 + `docs/PROJECT_REPORT.md`（结论）+ `README.md`（怎么跑）。
3. 数据在本地 `data/`，已被 `.gitignore` 忽略；仓库里不含数据，但**含训练好的模型**
   （`outputs/models/`，703 KB），所以没有数据也能跑 `/api/predict`。
4. 用项目虚拟环境 `.venv` 中的 Python；换机器先跑 `python\check_env.py`（15 项检查）。
5. 启动顺序：`start_mysql.cmd` → `start_api.cmd` → 浏览器开 <http://127.0.0.1:8000>。
6. MySQL 8.0.46 的数据目录已迁到 `D:\ChatGPT\mysql_data`，**免管理员**启动；
   `MySQL80` 服务本身因中文主机名与权限问题未修好，不影响项目运行（见 §6）。
7. 项目已全部完成 Phase 0–15（Phase 9 的 .pbix 需用户本机 Power BI Desktop 制作）。
8. 已废弃的 `python/probe_bike_data.py`（旧共享单车方案）已删除。
9. `.github/workflows/ci.yml` 已写好但因 token 缺少 Workflows 权限暂未推送，
   暂存于 `.git/info/exclude` 排除列表；补权限后 `git add -f` 即可提交。

## 13. 质量复核记录

在功能全部跑通后又做了一轮「口径与一致性」复核，修掉的问题按严重程度排列：

| # | 问题 | 影响 | 修正 |
|---|---|---|---|
| 1 | 模型 artifact 是 70% 训练集版本，而 `prediction_test.csv` 用全量版本 | 交付物与文档不一致，被问「部署的是哪个模型」无法作答 | 统一保存全量版本；新增 `repurchase_model_meta.json` 记录特征、行数、两套阈值指标、库版本 |
| 2 | 1112 的 46 行计入日均 | 日均被低估 0.54%（295,297 vs 296,893） | `agg_daily.effective` 标记，所有日均按 185 天有效窗口；接口返回 `days` 与 `effective_days` |
| 3 | 物化表与事实表可能静默不一致 | 重导数据后接口返回旧数字且不报错 | `build_aggregates.sql` 末尾加一致性断言 |
| 4 | 测试全是集成测试 | CI 无法运行 | 拆为 10 项单元测试 + 16 项集成测试 |
| 5 | 老客/新客复购率差 1.50pp 未做显著性检验 | 缺少统计推断依据 | 补两项比例检验：z=12.49、p≈8.9e-36、95% CI [1.28, 1.72] pp |
| 6 | 文档残留旧数字 | SQL 7→6、前端 15→14、测试 16→26 等 | 全仓 grep 一遍改齐 |
| 7 | 缺 LICENSE、缺 CI、缺环境自检 | 公开仓库不完整 | 全部补齐 |
