"""
环境自检：一眼看出这台机器能不能跑本项目

为什么需要：
    这个项目对环境有若干隐含要求（Python 版本、Node、MySQL 是否在跑、
    数据文件是否就位、PowerShell 执行策略、脚本编码），任何一个不满足，
    报错信息往往指向错误的地方（例如「变量是 null」其实是文件编码问题）。
    跑这一个脚本就能把环境问题定位清楚。

运行：
    .\\.venv\\Scripts\\python.exe python\\check_env.py

退出码：0 全部通过或有警告；1 存在阻塞项。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "tmall" / "data_format1"
FRONTEND = PROJECT_ROOT / "frontend"

results: list[tuple[str, str, str]] = []  # (状态, 检查项, 说明)


def record(status: str, item: str, detail: str) -> None:
    results.append((status, item, detail))


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = PROJECT_ROOT / ".env"
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def check_python() -> None:
    version = sys.version_info
    if version >= (3, 11):
        record("PASS", "Python 版本", f"{version.major}.{version.minor}.{version.micro}")
    else:
        record("FAIL", "Python 版本", f"{version.major}.{version.minor}，需要 >= 3.11")


def check_packages() -> None:
    required = {
        "pandas": "数据处理",
        "numpy": "数值计算",
        "pyarrow": "CSV 加速",
        "matplotlib": "绘图（Phase 6）",
        "sklearn": "建模（Phase 8）",
        "fastapi": "后端（Phase 10）",
        "uvicorn": "ASGI 服务器",
        "pymysql": "MySQL 驱动",
        "pytest": "测试（Phase 13）",
    }
    missing = []
    for module, purpose in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(f"{module}（{purpose}）")
    if missing:
        record("FAIL", "Python 依赖", "缺少：" + "、".join(missing)
               + " → pip install -r requirements.txt")
    else:
        record("PASS", "Python 依赖", f"{len(required)} 个关键包均可导入")


def check_node() -> None:
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        record("FAIL", "Node.js", "未找到 node/npm → 前端与 CI 无法构建")
        return
    try:
        version = subprocess.run([node, "--version"], capture_output=True,
                                 text=True, timeout=20).stdout.strip()
        record("PASS", "Node.js", f"{version}（{node}）")
    except Exception as exc:
        record("WARN", "Node.js", f"存在但无法执行：{exc}")

    if (FRONTEND / "node_modules").is_dir():
        record("PASS", "前端依赖", "frontend/node_modules 已安装")
    else:
        record("WARN", "前端依赖", "未安装 → cd frontend && npm install")

    if (FRONTEND / "dist" / "index.html").exists():
        record("PASS", "前端构建产物", "frontend/dist 已构建（单端口部署可用）")
    else:
        record("WARN", "前端构建产物", "未构建 → cd frontend && npm run build")


def check_data() -> None:
    required = {
        "user_log_format1.csv": "原始行为日志",
        "user_info_format1.csv": "原始用户画像",
    }
    clean_required = {
        "user_log_format1_clean.csv": 2000,   # MB 下限，防止只看文件名
        "train_format1_clean.csv": 3,
        "test_format1_clean.csv": 3,
        "user_info_format1_clean.csv": 5,
    }
    missing_raw = [n for n in required if not (RAW_DIR / n).exists()]
    if missing_raw:
        record("WARN", "原始数据", f"缺少 {missing_raw}（只有复跑 Phase 0-4 才需要）")
    else:
        record("PASS", "原始数据", "data/raw/tmall/data_format1 齐全")

    problems = []
    for name, min_mb in clean_required.items():
        path = CLEAN_DIR / name
        if not path.exists():
            problems.append(f"{name} 不存在")
        elif path.stat().st_size < min_mb * 1024 * 1024:
            problems.append(f"{name} 仅 {path.stat().st_size / 1024 / 1024:.1f} MB（预期 ≥ {min_mb} MB）")
    if problems:
        record("FAIL", "清洗后数据", "；".join(problems) + " → 跑 python/data_cleaning.py 等")
    else:
        record("PASS", "清洗后数据", "4 个清洗产物齐全且体积合理")


def check_mysql() -> None:
    env = load_env()
    host = env.get("MYSQL_HOST", "127.0.0.1")
    port = int(env.get("MYSQL_PORT", "3306"))
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
        record("PASS", "MySQL 端口", f"{host}:{port} 可连接")
    except OSError:
        record("FAIL", "MySQL 端口",
               f"{host}:{port} 连不上 → 先在另一个窗口执行 start_mysql.cmd")
        return

    try:
        import pymysql
        conn = pymysql.connect(
            host=host, port=port,
            user=env.get("MYSQL_USER", "root"),
            password=env.get("MYSQL_PASSWORD", ""),
            database=env.get("MYSQL_DATABASE", "tmall_analysis"),
            connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM fact_user_log")
                rows = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA = DATABASE()")
                tables = cur.fetchone()[0]
        finally:
            conn.close()
        if rows == 54_925_330:
            record("PASS", "数据库内容", f"{tables} 张表，fact_user_log {rows:,} 行（与校验值一致）")
        else:
            record("WARN", "数据库内容",
                   f"{tables} 张表，fact_user_log {rows:,} 行（预期 54,925,330）")
    except Exception as exc:
        record("FAIL", "数据库连接", f"端口通但认证/查询失败：{exc}（检查 .env 里的密码）")


def check_model_and_aggregates() -> None:
    model = PROJECT_ROOT / "outputs" / "models" / "repurchase_model.joblib"
    meta = PROJECT_ROOT / "outputs" / "models" / "repurchase_model_meta.json"
    if model.exists() and meta.exists():
        record("PASS", "模型产物", "repurchase_model.joblib + 元信息齐全")
    elif model.exists():
        record("WARN", "模型产物", "有模型文件但缺元信息 → 重跑 python/train_model.py")
    else:
        record("FAIL", "模型产物", "缺失 → python/build_features.py && python/train_model.py")

    if (PROJECT_ROOT / "outputs" / "tables" / "prediction_test.csv").exists():
        record("PASS", "测试集预测", "prediction_test.csv 存在")
    else:
        record("WARN", "测试集预测", "缺失 → python/train_model.py")


def check_powershell() -> None:
    """Windows 上 .ps1 受执行策略限制，这是本项目踩过的坑。"""
    if os.name != "nt":
        return
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-ExecutionPolicy"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        if out.lower() in ("restricted", "allsigned"):
            record("WARN", "PowerShell 执行策略",
                   f"{out} → 直接用 .\\start_mysql.cmd（内部已用 Bypass），不要直接跑 .ps1")
        else:
            record("PASS", "PowerShell 执行策略", out)
    except Exception as exc:
        record("WARN", "PowerShell 执行策略", f"无法读取：{exc}")


def check_script_encoding() -> None:
    """
    .ps1 必须 UTF-8 带 BOM，否则 PowerShell 5.1 按 GBK 解码会把变量解析成 null；
    .cmd 必须纯 ASCII，否则 cmd.exe 会拆断命令行。
    """
    if os.name != "nt":
        return
    for name in ("start_mysql.ps1", "start_api.cmd", "start_frontend.cmd"):
        path = PROJECT_ROOT / name
        if not path.exists():
            continue
        data = path.read_bytes()
        if name.endswith(".ps1"):
            has_bom = data[:3] == b"\xef\xbb\xbf"
            record("PASS" if has_bom else "FAIL", f"{name} 编码",
                   "UTF-8 带 BOM" if has_bom else "缺 BOM → PowerShell 5.1 会按 GBK 解码出错")
        else:
            non_ascii = sum(1 for b in data if b > 127)
            has_crlf = b"\r\n" in data
            ok = non_ascii == 0 and has_crlf
            record("PASS" if ok else "FAIL", f"{name} 编码",
                   f"非 ASCII 字节 {non_ascii}，CRLF={'有' if has_crlf else '无'}"
                   + ("" if ok else " → 必须纯 ASCII + CRLF"))


def main() -> int:
    print("=" * 62)
    print("项目环境自检")
    print("=" * 62)

    check_python()
    check_packages()
    check_node()
    check_data()
    check_mysql()
    check_model_and_aggregates()
    check_powershell()
    check_script_encoding()

    width = max(len(item) for _, item, _ in results)
    for status, item, detail in results:
        mark = {"PASS": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]"}[status]
        print(f"{mark} {item:<{width}}  {detail}")

    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    print("-" * 62)
    print(f"通过 {len(results) - len(fails) - len(warns)} 项，"
          f"警告 {len(warns)} 项，阻塞 {len(fails)} 项")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
