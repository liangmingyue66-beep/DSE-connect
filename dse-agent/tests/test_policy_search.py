"""检索层策略的单测：gap 截断、低置信标注、关键词兜底（不依赖 embedding）。"""
from app.tools.search_policy import _keyword_search, format_for_llm


def _r(score, title="条款", degraded=False):
    return {"score": score, "title": title, "text": "正文内容", "source": "政策.md", "degraded": degraded}


def test_empty_returns_hint():
    assert "未检索到" in format_for_llm([])


def test_gap_cut_keeps_only_top1():
    """top1 明显领先（gap ≥ 0.08）时只给 top1，减少噪声。"""
    out = format_for_llm([_r(0.769, "入学与体检"), _r(0.578, "其他"), _r(0.575, "其他2")])
    assert out.count("【") == 1
    assert "入学与体检" in out


def test_small_gap_keeps_all():
    """gap 小（0.01）说明多条都相关，全部保留。"""
    out = format_for_llm([_r(0.726, "报名方式和时间"), _r(0.694, "报名资格")])
    assert out.count("【") == 2


def test_low_confidence_note_added():
    """top1 低于 0.62（实测相关组最低 0.588）→ 加低置信标注，但仍返回内容。"""
    out = format_for_llm([_r(0.588, "奖学金"), _r(0.572, "其他")])
    assert "相关性偏低" in out
    assert "奖学金" in out          # 不过滤，只标注


def test_high_confidence_no_note():
    out = format_for_llm([_r(0.700, "录取原则"), _r(0.680, "其他")])
    assert "相关性偏低" not in out


def test_degraded_note():
    out = format_for_llm([_r(0.500, "报名", degraded=True)])
    assert "降级检索" in out


def test_keyword_fallback_hits_relevant_chunk():
    """embedding 挂掉时的兜底：关键词要能命中"报名"相关条款。"""
    results = _keyword_search("什么时候报名", 3)
    assert results, "关键词兜底不应返回空"
    assert any("报名" in r["title"] for r in results)


def test_keyword_fallback_returns_empty_for_nonsense():
    """完全无关的输入不应硬凑结果。"""
    assert _keyword_search("zzzzz", 3) == []
