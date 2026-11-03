"""query_schools 单元测试。

验收标准：查询结果与 webv0 网页筛选逻辑一致。
测试依赖 dse_agent 库已通过 scripts/load_data.py 入库。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools.query_schools import MAX_RESULTS, count_schools, query_schools, total_of


def test_total_of():
    assert total_of("4", "5", "4") == 13
    assert total_of("5*", "5", "5**") == 18  # 6 + 5 + 7
    assert total_of("1", "1", "1") == 3
    assert total_of("", "5", "4") is None
    assert total_of("4", "5", "X") is None


def test_query_guangdong_computer():
    rows = query_schools(province="广东省", major_keywords=["计算机"])
    assert rows, "广东+计算机 应有结果"
    for r in rows:
        assert r["province"] == "广东省"
        assert "计算机" in r["major"] or "計算機" in r["major"]


def test_query_multi_keywords():
    # 多关键词 OR 匹配：电子信息 或 电气工程
    rows = query_schools(province="广东省", major_keywords=["电子信息", "电气工程"])
    assert rows, "广东+电子信息/电气工程 应有结果"
    for r in rows:
        assert "电子信息" in r["major"] or "电气" in r["major"]


def test_query_score_filter():
    # 我的总分 13，只返回"最低总分 <= 13"且有收生数据的专业
    rows = query_schools(province="广东省", major_keywords=["计算机"], scores=("4", "5", "4"))
    assert rows, "应返回符合分数条件的专业"
    for r in rows:
        assert r["min_total"] is not None and r["min_total"] <= 13


def test_query_traditional_keyword():
    # 繁体输入也能命中（繁简归一，等价网页 norm）
    rows = query_schools(major_keywords=["計算機"])
    assert rows, "繁体关键词应能命中"


def test_query_tier():
    rows = query_schools(province="广东省", tiers=["985"], major_keywords=["计算机"])
    for r in rows:
        assert r["tier"] == "985"


def test_count_reflects_real_total_beyond_limit():
    """count_schools 必须是真实总数，query_schools 受 MAX_RESULTS 截断。"""
    total = count_schools(province="广东省")
    rows = query_schools(province="广东省")
    assert total > len(rows), "广东省应超过单次返回上限"
    assert len(rows) == MAX_RESULTS


def test_count_matches_query_when_under_limit():
    rows = query_schools(school_names=["北京大学"], major_keywords=["计算机"])
    assert count_schools(school_names=["北京大学"], major_keywords=["计算机"]) == len(rows)


def test_diversify_avoids_single_school_monopoly():
    """大结果集抽样：不能让一所学校占满展示位。"""
    from app.tools.agent_tools import _diversify

    rows = ([{"university": "中山大学"}] * 20
            + [{"university": "华南理工大学"}] * 5
            + [{"university": "深圳大学"}] * 3)
    picked = _diversify(rows, 6)

    assert len(picked) == 6
    assert len({r["university"] for r in picked}) == 3, "三所学校都应出现"
