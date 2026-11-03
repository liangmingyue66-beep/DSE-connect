#!/usr/bin/env python3
"""数据入库脚本：CSV（繁体母表）→ 繁简转换 → PostgreSQL。

设计要点：
- 幂等：TRUNCATE 后全量重建，重复执行不产生重复数据（"数据改动走全库回归"的前提）
- 繁简转换在入库前完成：库内统一简体，否则"计算机"查不到"計算機"
- 书数据只读：universities / programmes 只允许本脚本重建，业务代码只读

用法：
    .venv/Scripts/python.exe scripts/load_data.py
"""
import csv
import sys
from pathlib import Path

from opencc import OpenCC
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # 保证直接运行脚本能导入 app 包

from app import config  # noqa: E402
from app.db import get_conn, init_schema  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"

_cc = OpenCC("t2s")


def t2s(value: str | None) -> str | None:
    """繁体转简体，空值安全。"""
    if value is None:
        return None
    text = value.strip()
    return _cc.convert(text) if text else None


def parse_int(value: str | None) -> int | None:
    """CSV 空串 → NULL，数字串 → int。"""
    text = (value or "").strip()
    return int(text) if text else None


def parse_bool_cn(value: str | None) -> bool:
    """「是/否」→ True/False，空值视为否。"""
    return (value or "").strip() == "是"


def load_universities(cur) -> dict[str, int]:
    """读 universities_all.csv 写入 universities 表，返回 校名→id 映射。"""
    rows = []
    with (DATA_DIR / "universities_all.csv").open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(
                (
                    t2s(r["校名"]),
                    t2s(r["省份"]),
                    t2s(r["城市"]),
                    t2s(r["层次"]),
                    parse_int(r["资料页"]),
                    parse_int(r["专业表页"]),
                    t2s(r["一般入学要求"]),
                )
            )

    execute_values(
        cur,
        """
        INSERT INTO universities
            (name, province, city, tier, book_page, table_page, general_req)
        VALUES %s
        """,
        rows,
        page_size=500,
    )

    # 插入后回查 id，不依赖「自增从 1 开始」的隐式假设
    cur.execute("SELECT id, name FROM universities")
    return {name: uid for uid, name in cur.fetchall()}


def load_programmes(cur, uni_map: dict[str, int]) -> tuple[int, list[str]]:
    """读 programmes_all.csv 写入 programmes 表。

    返回 (插入行数, 未匹配校名列表)。出现未匹配校名属数据不一致，
    必须显式暴露而非静默跳过（全库回归原则）。
    """
    rows: list[tuple] = []
    unmatched: set[str] = set()

    with (DATA_DIR / "programmes_all.csv").open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            uni_name = t2s(r["院校"])
            uni_id = uni_map.get(uni_name)
            if uni_id is None:
                unmatched.add(uni_name)
                continue
            rows.append(
                (
                    uni_id,
                    t2s(r["省份"]),
                    t2s(r["城市"]),
                    t2s(r["院系"]),
                    t2s(r["专业名称"]),
                    t2s(r["授课语言"]),
                    parse_bool_cn(r["体艺类"]),
                    parse_int(r["2025/26录取人数"]),
                    # 单科列保留原始等级字符串（'5*'/'5**'/'--'/数字串），不做数值化
                    t2s(r["最高_中"]),
                    t2s(r["最高_英"]),
                    t2s(r["最高_数"]),
                    t2s(r["最低_中"]),
                    t2s(r["最低_英"]),
                    t2s(r["最低_数"]),
                    parse_int(r["最低院校志愿序号"]),
                    parse_int(r["最高总分"]),
                    parse_int(r["最低总分"]),
                    t2s(r["成绩区备注"]),
                    t2s(r["额外科目要求"]),
                    t2s(r["专业要求(中英数公民)"]),
                    parse_bool_cn(r["有2025/26收生数据"]),
                    parse_int(r["页码"]),
                )
            )

    execute_values(
        cur,
        """
        INSERT INTO programmes (
            university_id, province, city, department, major_name,
            teaching_language, is_art_pe, admitted_count,
            max_chinese, max_english, max_maths,
            min_chinese, min_english, min_maths,
            min_choice_order, max_total, min_total,
            score_note, extra_requirements, subject_requirements,
            has_admission_data, book_page
        ) VALUES %s
        """,
        rows,
        page_size=1000,
    )
    return len(rows), sorted(unmatched)


def main() -> int:
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                init_schema(cur)
                # 幂等全量重建：这两张表只允许本脚本写入
                cur.execute("TRUNCATE programmes, universities RESTART IDENTITY CASCADE")
                uni_map = load_universities(cur)
                inserted, unmatched = load_programmes(cur, uni_map)
                cur.execute("SELECT count(*) FROM programmes")
                total = cur.fetchone()[0]
    finally:
        conn.close()

    print(f"universities: {len(uni_map)} 所")
    print(f"programmes:   {inserted} 行（表内共 {total} 行）")
    if unmatched:
        print(f"[警告] {len(unmatched)} 个校名在 universities 表中找不到，相关行被跳过：")
        for name in unmatched:
            print(f"  - {name}")
        return 1
    print("入库完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
