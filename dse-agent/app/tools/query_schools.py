"""确定性院校专业查询（LLM 不计算）。

复现 webv0 网页筛选逻辑，保证 Agent 查出来的结果与网页 100% 一致：
- 筛选维度：省份 / 城市 / 层次 / 院校名 / 专业词 / 分数
- 分数筛选 = 有收生数据 且 最低总分 <= 我的总分（客观筛选，不做"够不够"判断）
- 分数换算：POINTS（5**=7、5*=6、5=5 … 1=1）
- 繁体输入归一：opencc t2s（与网页 norm 等价）
"""
from opencc import OpenCC

from app.db import get_conn

POINTS = {"5**": 7, "5*": 6, "5": 5, "4": 4, "3": 3, "2": 2, "1": 1}
MAX_RESULTS = 100
_cc = OpenCC("t2s")


def total_of(zh: str, en: str, ma: str) -> int | None:
    """三科等级 → 总分；任一缺失或非法返回 None。"""
    if not all(x in POINTS for x in (zh, en, ma)):
        return None
    return POINTS[zh] + POINTS[en] + POINTS[ma]


# 宽泛门类词（不是具体专业名，LIKE 匹配不到，直接忽略，避免误筛出 0 条）
_VAGUE_MAJOR = {"理工", "工科", "理科", "文科", "商科", "医学", "农学", "艺术", "综合", "管理", "经济"}

# 对外统一口径：双一流以下都算"普通"（公办/民办/合作办学不再区分）。
# 库里保留原始值不加改动，只在查询时归并——数据仍可追溯。
NORMAL_TIERS = ("普通公办", "民办", "合作办学")


def _norm_list(items) -> list[str]:
    """归一关键词数组：去空、strip。

    只处理 list——字符串由工具层 Pydantic 严格拦截（让模型改，而不是代码猜分隔符）。
    """
    if not items:
        return []
    return [x.strip() for x in items if isinstance(x, str) and x.strip()]


def _expand_tiers(tiers) -> list[str] | None:
    """把对外口径展开成库里的原始值。

    "普通" = 普通公办 + 民办 + 合作办学（双一流以下统一口径）。
    多个层次求并集——这样"不要985"可以由模型填 ["211","双一流","普通"] 来表达。
    """
    norm = _norm_list(tiers)
    if not norm:
        return None
    out: list[str] = []
    for t in norm:
        out.extend(NORMAL_TIERS if t == "普通" else [t])
    return sorted(set(out))


def _build_where(
    province: str | None,
    city: str | None,
    tiers: list[str] | None,
    school_names: list[str] | None,
    major_keywords: list[str] | None,
    scores: tuple[str, str, str] | None,
) -> tuple[str, list]:
    """构造 WHERE 子句和参数（query_schools 与 count_schools 共用，保证条件完全一致）。"""
    clauses: list[str] = []
    params: list = []

    if province:
        clauses.append("u.province = %s")
        params.append(province)
    if city:
        clauses.append("u.city LIKE %s")
        params.append("%" + city + "%")
    expanded_tiers = _expand_tiers(tiers)
    if expanded_tiers:
        clauses.append("u.tier = ANY(%s)")
        params.append(expanded_tiers)
    names = _norm_list(school_names)
    if names:
        name_ors = ["u.name LIKE %s" for _ in names]
        for n in names:
            params.append("%" + _cc.convert(n) + "%")
        clauses.append("(" + " OR ".join(name_ors) + ")")

    kws = [k for k in _norm_list(major_keywords) if k not in _VAGUE_MAJOR]
    if kws:
        ors = []
        for kw in kws:
            kw = _cc.convert(kw)
            ors.append("(p.major_name LIKE %s OR p.score_note LIKE %s)")
            params.extend(["%" + kw + "%", "%" + kw + "%"])
        clauses.append("(" + " OR ".join(ors) + ")")

    total = total_of(*scores) if scores else None
    if total is not None:
        clauses.append("p.has_admission_data AND p.min_total IS NOT NULL AND p.min_total <= %s")
        params.append(total)

    return (" AND ".join(clauses) if clauses else "TRUE"), params


def query_schools(
    province: str | None = None,
    city: str | None = None,
    tiers: list[str] | None = None,
    school_names: list[str] | None = None,
    major_keywords: list[str] | None = None,
    scores: tuple[str, str, str] | None = None,
    conn=None,
) -> list[dict]:
    """返回符合条件的专业清单。

    **注意**：受 MAX_RESULTS 限制（默认 100），条数 ≠ 真实总数；
    要真实总数请用 count_schools()（条件构造与之完全一致）。

    tiers: 院校层次数组，多个层次 OR 匹配（"普通"会展开成 普通公办/民办/合作办学）。
    school_names: 院校名数组，多个学校 OR 匹配（字符串由工具层 Pydantic 拦截）。
    major_keywords: 专业关键词数组，多个词 OR 匹配。
    scores: 三科等级 (中, 英, 数)，如 ("4", "5", "4")；None 表示不筛分数。
    conn:   可注入数据库连接（测试用）；None 时自建并负责关闭。
    """
    where, params = _build_where(province, city, tiers, school_names, major_keywords, scores)
    sql = f"""
        SELECT u.name        AS university,
               u.tier       AS tier,
               u.province   AS province,
               u.city       AS city,
               p.department AS department,
               p.major_name AS major,
               p.min_total  AS min_total,
               p.max_total  AS max_total,
               p.admitted_count AS admitted_count,
               p.book_page  AS book_page,
               p.extra_requirements AS extra_requirements
        FROM programmes p
        JOIN universities u ON u.id = p.university_id
        WHERE {where}
        ORDER BY (p.has_admission_data AND p.min_total IS NOT NULL) DESC,
                 p.min_total DESC NULLS LAST,
                 u.name, p.major_name
        LIMIT %s
    """
    params.append(MAX_RESULTS)

    own = conn is None
    if own:
        conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        if own:
            conn.close()


def count_schools(
    province: str | None = None,
    city: str | None = None,
    tiers: list[str] | None = None,
    school_names: list[str] | None = None,
    major_keywords: list[str] | None = None,
    scores: tuple[str, str, str] | None = None,
    conn=None,
) -> int:
    """返回符合条件的**真实总条数**（不受 MAX_RESULTS 限制）。"""
    where, params = _build_where(province, city, tiers, school_names, major_keywords, scores)
    sql = f"""
        SELECT COUNT(*)
        FROM programmes p
        JOIN universities u ON u.id = p.university_id
        WHERE {where}
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]
    finally:
        if own:
            conn.close()
