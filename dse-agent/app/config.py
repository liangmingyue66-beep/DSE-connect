"""配置加载：凭据只从 .env 读取，禁止硬编码。

why: 旧项目 models.py 曾把数据库密码明文写进代码并入库，
这是安全事故级反模式；.env 已加入 .gitignore，永不进版本库。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 以项目根目录为基准加载 .env，不依赖启动时的工作目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")
PG_DB = os.getenv("PG_DB", "dse_agent")


def dsn() -> str:
    """psycopg2 key=value 形式的连接串。"""
    return (
        f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} "
        f"user={PG_USER} password={PG_PASSWORD}"
    )
