"""数据库连接与表结构定义。

why: 书数据（universities / programmes）是只读业务表——
只由 scripts/load_data.py 全量重建，业务代码一律只读（项目不变量 1）。
"""
import psycopg2

from app import config

# 连接超时必须显式写数值（默认=无限等待，是生产事故的常见来源）
CONNECT_TIMEOUT_SECONDS = 5

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS universities (
        id          SERIAL PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        province    TEXT NOT NULL,
        city        TEXT,
        tier        TEXT,
        book_page   SMALLINT,
        table_page  SMALLINT,
        general_req TEXT,
        created_at  TIMESTAMP NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS programmes (
        id                   SERIAL PRIMARY KEY,
        university_id        INTEGER NOT NULL REFERENCES universities(id),
        province             TEXT,
        city                 TEXT,
        department           TEXT,
        major_name           TEXT NOT NULL,
        teaching_language    TEXT,
        is_art_pe            BOOLEAN NOT NULL DEFAULT FALSE,
        admitted_count       SMALLINT,
        -- 单科列存原始等级字符串（'5*'/'5**'/'4'/'--'）：
        -- 数据里等级与数字混存，且产品只"陈列分数不做判断"（不变量 2），
        -- 展示需要原貌，筛选排序由总分数值列承担
        max_chinese          TEXT,
        max_english          TEXT,
        max_maths            TEXT,
        min_chinese          TEXT,
        min_english          TEXT,
        min_maths            TEXT,
        min_choice_order     SMALLINT,
        max_total            SMALLINT,
        min_total            SMALLINT,
        score_note           TEXT,
        extra_requirements   TEXT,
        subject_requirements TEXT,
        has_admission_data   BOOLEAN NOT NULL DEFAULT FALSE,
        book_page            SMALLINT,
        created_at           TIMESTAMP NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_programmes_province   ON programmes (province)",
    "CREATE INDEX IF NOT EXISTS idx_programmes_university ON programmes (university_id)",
    "CREATE INDEX IF NOT EXISTS idx_programmes_min_total  ON programmes (min_total)",
]


def get_conn():
    """新建数据库连接（每次调用返回新连接）。"""
    return psycopg2.connect(config.dsn(), connect_timeout=CONNECT_TIMEOUT_SECONDS)


def init_schema(cur) -> None:
    """建表与索引（幂等，可重复执行）。"""
    for stmt in SCHEMA_STATEMENTS:
        cur.execute(stmt)
