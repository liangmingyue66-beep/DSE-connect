#!/usr/bin/env python3
"""入库后的数据验证（全库回归）：行数一致性 + 繁简转换抽查 + 核心查询冒烟。

why: 项目不变量 4——数据改动必须走全库回归。
每次 load_data.py 之后运行本脚本，任何一项 FAIL 都要当场查清。

用法：
    .venv/Scripts/python.exe scripts/verify_load.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_conn  # noqa: E402


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM universities")
            uni = cur.fetchone()[0]
            checks.append(("universities 行数 = 165", uni == 165, f"实际 {uni}"))

            cur.execute("SELECT count(*) FROM programmes")
            prog = cur.fetchone()[0]
            checks.append(("programmes 行数 = 3794", prog == 3794, f"实际 {prog}"))

            # 繁简转换：库内不应残留常见繁体字（以「機」「學」为例）
            cur.execute("SELECT count(*) FROM programmes WHERE major_name LIKE '%機%'")
            trad = cur.fetchone()[0]
            checks.append(("专业名无残留繁体「機」", trad == 0, f"残留 {trad} 行"))

            # 简体关键词可命中 → 证明转换生效
            cur.execute("SELECT count(*) FROM programmes WHERE major_name LIKE '%计算机%'")
            cs = cur.fetchone()[0]
            checks.append(("简体「计算机」可命中", cs > 0, f"命中 {cs} 行"))

            # 核心查询冒烟：广东 + 计算机 + 有最低总分，按总分降序
            cur.execute(
                """
                SELECT u.name, p.major_name, p.min_total, p.book_page
                FROM programmes p JOIN universities u ON u.id = p.university_id
                WHERE p.province = %s AND p.major_name LIKE %s
                  AND p.min_total IS NOT NULL
                ORDER BY p.min_total DESC
                LIMIT 5
                """,
                ("广东省", "%计算机%"),
            )
            sample = cur.fetchall()
    finally:
        conn.close()

    ok = True
    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {name}（{detail}）")
        ok = ok and passed

    print("\n=== 核心查询冒烟：广东省 · 计算机 · 按最低总分降序 ===")
    for name, major, total, page in sample:
        print(f"  {name} | {major} | 最低总分 {total} | 书页 P{page}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
