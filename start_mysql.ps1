# =====================================================================
# 免管理员启动本地 MySQL 实例
# ---------------------------------------------------------------------
# 为什么需要这个脚本？
#   1. MySQL80 服务默认以 NT AUTHORITY\NetworkService 运行，启动它必须
#      管理员权限（Start-Service 会失败）。本脚本用当前用户身份直接跑 mysqld，
#      不需要管理员。
#   2. 这台机器的计算机名是中文（南笙离），而 my.ini 里 MySQL 安装器自动生成了
#      这些文件名：
#         general_log_file="南笙离.log"
#         slow_query_log_file="南笙离-slow.log"
#         log-error="南笙离.err"
#         log-bin="南笙离-bin"
#      mysqld 在 Windows 下创建这种非 ASCII 文件名会失败，报
#         mysqld: File '.\<乱码>bin.index' not found (OS errno 2)
#         [ERROR] [MY-013236] The designated data directory ... is unusable.
#      所以下面用命令行参数把它们全部覆盖成 ASCII 文件名。
#      命令行参数优先级高于 my.ini，因此不用改那个需要管理员权限的配置文件。
#
# 用法：
#   在 PowerShell 里执行  .\start_mysql.ps1
#   然后新开一个窗口连：mysql -u root -h 127.0.0.1 -P 3306
#   停止：在这个窗口按 Ctrl+C，或另开窗口执行 mysqladmin shutdown
#
# 注意：
#   - 这是前台进程，窗口关掉数据库就停。要长期后台运行，请让管理员把
#     my.ini 里上面 4 个文件名改成 ASCII，再 Start-Service -Name MySQL80。
#   - innodb_doublewrite=0 是为了批量装载提速（本项目数据可从 CSV 重建）。
#     如果这个库开始存放不可重建的数据，请去掉这个参数。
# =====================================================================

$mysqlBin = 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe'
$config   = 'C:\ProgramData\MySQL\MySQL Server 8.0\my.ini'
# 数据目录已从 C 盘迁到 D 盘（清理 C 盘空间）。
# my.ini 里仍写着旧路径且该文件需要管理员才能改，所以下面用 --datadir 覆盖它。
$dataDir  = 'D:/ChatGPT/mysql_data'
# MySQL 的 tmpdir 默认在 C 盘（大查询排序/分组会往那写临时文件，实测校验脚本能吃掉 1.5 GB），
# 必须指到 D 盘，否则清完 C 盘很快又会被临时文件占回去。
$tmpDir   = 'D:/ChatGPT/mysql_tmp'

if (-not (Test-Path $mysqlBin)) { throw "找不到 mysqld：$mysqlBin" }
if (-not (Test-Path $config))   { throw "找不到 my.ini：$config" }
if (-not (Test-Path $dataDir))  { throw "找不到数据目录：$dataDir（可能还没迁移）" }
if (-not (Test-Path $tmpDir))   { New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null }

Write-Host "启动 MySQL（3306，数据在 $dataDir，前台运行，Ctrl+C 停止）..." -ForegroundColor Cyan

& $mysqlBin --defaults-file="$config" `
    --datadir="$dataDir" `
    --tmpdir="$tmpDir" `
    --skip-log-bin `
    --slow-query-log=0 `
    --general-log=0 `
    --log-error="$dataDir/mysql-error.log" `
    --pid-file="$dataDir/mysql.pid" `
    --local_infile=ON `
    --innodb_buffer_pool_size=4G `
    --innodb_buffer_pool_instances=4 `
    --innodb_flush_log_at_trx_commit=2 `
    --innodb_doublewrite=0 `
    --innodb_redo_log_capacity=1G `
    --console
