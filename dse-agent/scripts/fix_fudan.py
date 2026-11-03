#!/usr/bin/env python3
"""复旦 P125 数据修复：用 find_tables 重抽，替换坐标启发式产生的错位数据。

背景与根因见 docs/数据问题记录.md。

实现要点：
- find_tables 正确识别列归属（专业名完整），但院系合并单元格 cell 左边界丢首字；
- 故院系名改用 words 坐标（x<115）提取完整文字，再按 y 顺序与数据行对齐。
- 幂等：本脚本可重复执行（每次先剔除旧复旦 P125 行再重建）。
"""
import csv

import pymupdf

PDF = r"E:\deepseek test\Handbook2026_27.pdf"
CSV = r"E:\deepseek test\data_all\programmes_all.csv"
FUDAN_PAGE = 125  # PDF 物理页（复旦专业表）
GRADE_POINTS = {"5**": 7, "5*": 6, "5": 5, "4": 4, "3": 3, "2": 2, "1": 1}


def clean(v):
    if v is None:
        return ""
    return v.replace("\n", "").strip()


def parse_score(v):
    v = clean(v)
    return v if (v in GRADE_POINTS or (v and v.isdigit())) else ""


def clean_extra(v):
    v = clean(v)
    if v in ("", "無", "无"):
        return ""
    return v


def main():
    doc = pymupdf.open(PDF)
    page = doc[FUDAN_PAGE]
    words = page.get_text("words")
    tabs = page.find_tables()
    big = next(t for t in tabs.tables if len(t.extract()) >= 40)
    data = big.extract()[4:]  # 跳过 4 行表头，得 44 个数据行

    # 院系名：words 坐标（x<115）完整提取，按 y 聚类
    dept_ws = [w for w in words if w[0] < 115 and 242 < w[1] < 770]
    dept_ws.sort(key=lambda w: w[1])
    depts = []
    for w in dept_ws:
        if depts and w[1] - depts[-1]["y"] < 8:
            depts[-1]["text"] += w[4]
            depts[-1]["y"] = w[1]
        else:
            depts.append({"y": w[1], "text": w[4]})
    dept_names = [d["text"] for d in depts]

    # 原 CSV 复旦的省份/城市（保持原值）
    province, city = "上海市", "上海"
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["院校"].strip() == "复旦大学" and r["页码"].strip() == "125":
                province, city = r["省份"].strip(), r["城市"].strip()
                break

    # 生成 44 行
    new_rows = []
    di = -1
    for row in data:
        if row[0] and row[0].strip():
            di += 1
        dept = dept_names[di] if 0 <= di < len(dept_names) else ""
        major = clean(row[1])
        hi = [parse_score(row[9]), parse_score(row[10]), parse_score(row[11])]
        lo = [parse_score(row[12]), parse_score(row[13]), parse_score(row[14])]
        admit = clean(row[8])
        rank = clean(row[15])

        filled = [admit] + hi + lo
        has_data = any(x.isdigit() or x in GRADE_POINTS for x in filled)

        def total(vals):
            return str(sum(GRADE_POINTS[x] for x in vals)) if all(x in GRADE_POINTS for x in vals) else ""

        new_rows.append({
            "页码": "125", "院校": "复旦大学", "省份": province, "城市": city,
            "院系": dept, "专业名称": major, "授课语言": clean(row[2]), "体艺类": clean(row[3]),
            "2025/26录取人数": admit if has_data else "",
            "最高_中": hi[0] if has_data else "", "最高_英": hi[1] if has_data else "", "最高_数": hi[2] if has_data else "",
            "最低_中": lo[0] if has_data else "", "最低_英": lo[1] if has_data else "", "最低_数": lo[2] if has_data else "",
            "最低院校志愿序号": rank if has_data else "",
            "最高总分": total(hi) if has_data else "", "最低总分": total(lo) if has_data else "",
            "成绩区备注": "", "额外科目要求": clean_extra(row[5]), "专业要求(中英数公民)": "",
            "有2025/26收生数据": "是" if has_data else "否",
        })

    # 写回：剔除旧复旦 P125 行，原位插入新行
    rows = []
    inserted = False
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["院校"].strip() == "复旦大学" and r["页码"].strip() == "125":
                if not inserted:
                    rows.extend(new_rows)
                    inserted = True
                continue
            rows.append(r)
    if not inserted:
        rows.extend(new_rows)

    fieldnames = list(rows[0].keys())
    with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"复旦 P125 重建 {len(new_rows)} 行，已写回母表")
    # 抽查：有收生数据的行
    for r in new_rows:
        if r["有2025/26收生数据"] == "是":
            print(f"  抽查: {r['院系']} | {r['专业名称']} | 录取{r['2025/26录取人数']} | 最高{r['最高总分']}/最低{r['最低总分']}")


if __name__ == "__main__":
    main()
