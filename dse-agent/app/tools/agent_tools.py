"""Agent 工具集：把确定性查询包装成 LangChain Tool，供 ReAct 循环调用。

核心原则：工具内部做"排序 / 分档 / 格式化"等确定性处理，返回处理好的结果；
模型只负责"决定调哪个工具 + 把结果转述成自然语言"，不参与数据计算。
"""
import json
from collections import defaultdict
from typing import Literal

from langchain_core.tools import tool

from app.tools.query_schools import NORMAL_TIERS, count_schools, query_schools, total_of
from app.tools.search_policy import format_for_llm, search_policy

TIER_RANK = {"985": 0, "211": 1, "双一流": 2}
PREVIEW_N = 20            # 工具最多展示 20 条
DIVERSIFY_THRESHOLD = 40  # 真实结果超过这个数就按院校去重抽样（否则前 20 条会被顶尖校霸榜）

# 院校层次：对外只保留 4 档——985 / 211 / 双一流 / 普通。
# "普通"覆盖库里的 普通公办 + 民办 + 合作办学（详见 query_schools.NORMAL_TIERS）。
# 用 Literal 约束成枚举，让模型只能从固定值里选（选不了就 schema 校验失败 → 报错回传让它改），
# 而不是让代码去猜"985高校""双一流大学"该怎么映射。
TierLevel = Literal["985", "211", "双一流", "普通"]


def _diversify(rows: list[dict], limit: int) -> list[dict]:
    """按院校去重抽样：第 1 轮每校取 1 条，不够再第 2 轮补。

    目的：结果集很大时，排序后的前 N 条往往全被顶尖校霸榜（广东 731 条时前 20 条全是中大），
    抽样让"概览"覆盖更多学校。顺序仍保持原排序（高分学校在前）。
    """
    by_school: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_school[r.get("university") or ""].append(r)

    out: list[dict] = []
    max_rounds = max((len(v) for v in by_school.values()), default=0)
    for round_no in range(max_rounds):
        for items in by_school.values():
            if len(items) > round_no:
                out.append(items[round_no])
                if len(out) >= limit:
                    return out
    return out


def _format_results(results: list[dict], my_total: int | None, real_total: int | None = None) -> dict:
    """排序（层次优先+分数降序）+ 客观分档 + 格式化（确定性，代码做）。

    real_total: 真实总条数（不受 MAX_RESULTS 限制）；不传则退回 len(results)。
    结果集很大（> DIVERSIFY_THRESHOLD）时按院校去重抽样，避免概览被单校霸榜。
    """
    def sort_key(r):
        tier = TIER_RANK.get(r.get("tier") or "", 3)
        has_data = 0 if r.get("min_total") is not None else 1
        score = -(r.get("min_total") or 0)
        return (tier, has_data, score, r.get("university", ""))

    results = sorted(results, key=sort_key)
    if real_total is not None and real_total > DIVERSIFY_THRESHOLD:
        picked = _diversify(results, PREVIEW_N)
    else:
        picked = results[:PREVIEW_N]

    lines = []
    for r in picked:
        raw_tier = r.get("tier") or ""
        # 对外统一口径：普通公办/民办/合作办学 一律显示为"普通"
        tier = f"[{'普通' if raw_tier in NORMAL_TIERS else raw_tier}]" if raw_tier else ""
        dept = f"（{r['department']}）" if r.get("department") else ""
        if r.get("min_total") is not None:
            score = f"最低{r['min_total']}/最高{r['max_total']}"
            if my_total is not None:
                gap = my_total - r["min_total"]
                if gap <= 0:
                    score += "（卡线）"
                elif gap <= 2:
                    score += f"（低你{gap}分）"
                else:
                    score += f"（低你{gap}分·稳）"
        else:
            score = "暂无分数"
        lines.append(f"{r['university']}{tier}｜{dept}{r['major']}｜{score}｜书页P{r['book_page']}")
    return {
        "total": real_total if real_total is not None else len(results),
        "shown": len(lines),
        "lines": lines,
    }


@tool
def query_schools_tool(
    province: str = "",
    city: str = "",
    tiers: list[TierLevel] = [],
    school_names: list = [],
    major_keywords: list = [],
    scores: list = [],
) -> str:
    """查询内地高校各专业的录取分数线（最低/最高总分、书页页码）。

    当用户问「分数线、录取分数、我的成绩能上哪些学校/专业」时，**必须调用本工具**，
    绝不要凭记忆编造分数。**只填用户明确提到的参数，不要自己添加专业词**。
    **所有参数都是可选的——没有省份也能查（不指定省份=全国范围），不要因为缺省份就不查或反问用户。**

    参数说明：
    - province: 省份全称，如「广东省」
    - city: 城市，如「广州」
    - tiers: 院校层次**数组**，多个层次 OR 匹配。取值只能从这 4 个里选：
      985 / 211 / 双一流 / 普通（"普通"含公办普通本科、民办、合作办学）。
      · 用户说"985" → ["985"]；说"211" → ["211"]；说"双非/普通院校/一般本科" → ["普通"]
      · 用户说**排除某档**（如"不要985"）→ 填**其余全部层次**，即 ["211","双一流","普通"]，
        不要只填一个（否则会漏掉其它层次）
      · 用户没提层次 → 不填
    - school_names: 院校名**数组**。用户提到多个学校时**必须拆成数组**，如 ["湖南大学","中南大学"]，
      不要把多个校名挤成一个字符串（分隔符由你理解，工具只认数组）
    - major_keywords: 专业关键词**数组**。用户说「电子信息或者电气工程」时**必须拆成数组**
      ["电子信息","电气工程"]，不要把多个专业挤成一个字符串（分隔符由你理解，工具只认数组）
    - scores: 三科 DSE 成绩 [中文,英文,数学]，如 ["4","5","4"]
    """
    my_total = total_of(*scores) if scores and len(scores) == 3 else None
    filters = dict(
        province=province or None,
        city=city or None,
        tiers=tiers or None,
        school_names=school_names or None,
        major_keywords=major_keywords or None,
        scores=tuple(scores) if scores and len(scores) == 3 else None,
    )
    results = query_schools(**filters)
    # 真实总数：query_schools 有 MAX_RESULTS 上限，len(results) 不等于总数
    real_total = count_schools(**filters)
    formatted = _format_results(results, my_total, real_total)
    if not results:
        # 0 条诊断：去掉专业词/省份再查，让模型能说清"为什么 0 条"（而不是误以为没有这些学校）
        broader = query_schools(
            province=province or None,
            tiers=tiers or None,
            scores=tuple(scores) if scores and len(scores) == 3 else None,
        )
        if broader:
            mins = [r["min_total"] for r in broader if r["min_total"] is not None]
            hint = f"当前条件无结果；去掉专业词限制后该范围有 {len(broader)} 条"
            if mins:
                hint += f"（最低分 {min(mins)} 起）"
            hint += "。请如实告知用户是条件太窄、建议放宽，禁止编造具体分数。"
        else:
            hint = "即使放宽专业词也没有结果，可能分数或省份条件太严。请如实告知没查到，禁止编造。"
        formatted["hint"] = hint
    elif real_total > formatted["shown"]:
        # 结果集远大于展示数（如"整个广东省"731 条）：不要硬列，先概览再引导收敛
        formatted["hint"] = (
            f"共 {real_total} 条匹配，本次只展示前 {formatted['shown']} 条（已按院校层次与分数排序）。"
            "回答时**先给一段概览**（涉及哪些学校、大致分数区间），**不要逐条罗列全部**；"
            "然后引导用户缩小范围：请他说出三科成绩、想去的院校或专业方向，就能筛出可报范围。"
            "可以举个例子说明怎么问更准，例如「中山大学临床医学多少分」「华南理工计算机多少分」。"
        )
    return json.dumps(formatted, ensure_ascii=False)


@tool
def search_policy_tool(question: str) -> str:
    """检索政策、招生规则、报名时间、录取原则等资料（带出处）。

    当用户问「报名时间、录取规则、校长推荐计划、志愿填报」等**政策规则类**问题时调用本工具。
    **不要用它查录取分数/分数线**——分数必须用 query_schools_tool（政策语料里没有各专业分数，
    硬查只会拿到语义相近的规则条款，容易误导用户）。
    参数 question 是要查的问题，如「什么时候报名」。
    """
    return format_for_llm(search_policy(question))
