"""政策 RAG 工具：检索政策语料，返回带出处（和相关性标注）的片段。

检索策略基于实测数据（18 个 chunk、20 个标注问题）：
- 相关组 top1 分数 0.588~0.769，无关组 0.375~0.652，**两组在 0.588~0.652 完全重叠**
  → 绝对阈值不可行（任何阈值都会误伤一边），所以**不过滤**，只做"置信标注"。
- gap(top1-top2)：相关组 0.003~0.191，无关组 0.002~0.050
  → gap ≥ 0.08 只出现在"真相关"的情况，可作为"top1 明显独占"的强信号 → 只返回 top1，减少噪声。
- embedding 失败时降级为关键词（2-gram）匹配，避免"一挂就全查不到"。
"""
from app.retriever import POLICY_DIR, PolicyRetriever, _split_markdown

_retriever: PolicyRetriever | None = None

# 实测依据：相关组最低 0.588、无关组最高 0.652 → 取重叠区中位作为"低置信"标注线。
# 注意：只用于"标注"，不用于"过滤"（过滤会误杀 0.588 这类正确召回）。
LOW_CONFIDENCE = 0.62
# 实测依据：gap ≥ 0.08 的样本全部来自相关组，无关组最大仅 0.050。
GAP_CUT = 0.08

DEGRADED_NOTE = "（降级检索：按关键词匹配，可能不全；如无直接依据请如实说明，不要推测。）"


def _get_retriever() -> PolicyRetriever:
    global _retriever
    if _retriever is None:
        _retriever = PolicyRetriever()   # 加载一次，向量缓存
    return _retriever


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _keyword_search(question: str, top_k: int) -> list[dict]:
    """离线关键词兜底：不依赖 embedding，直接读语料按 2-gram 命中数打分。"""
    q_grams = _bigrams(question)
    if not q_grams:
        return []
    scored = []
    for f in sorted(POLICY_DIR.glob("*.md")):
        for title, body in _split_markdown(f.read_text(encoding="utf-8")):
            hit = len(q_grams & _bigrams(title + body))
            if hit:
                ratio = hit / len(q_grams)
                scored.append((ratio, {"title": title, "text": body,
                                       "source": f.name, "score": round(ratio, 3)}))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:top_k]]


def search_policy(question: str, top_k: int = 3) -> list[dict]:
    """检索政策片段，返回带 score 的列表。

    embedding 失败 → 降级关键词检索（结果带 degraded 标记，量纲不同不参与 gap 判断）。
    """
    try:
        results = _get_retriever().search(question, top_k)
        for r in results:
            r["degraded"] = False
        return results
    except Exception:
        results = _keyword_search(question, top_k)
        for r in results:
            r["degraded"] = True
        return results


def format_for_llm(results: list[dict]) -> str:
    """把检索结果格式化成给模型的文本：含出处、相关性标注、gap 截断。"""
    if not results:
        return "政策语料中未检索到相关内容，建议查阅《指南2026/27》或官方报名系统。"

    degraded = bool(results[0].get("degraded"))

    # gap 截断：top1 明显领先时，后面的基本是噪声（实测无关片段 gap 从未超过 0.05）
    if not degraded and len(results) > 1 and (results[0]["score"] - results[1]["score"]) >= GAP_CUT:
        results = results[:1]

    body = "\n\n".join(f"【{r['source']}｜{r['title']}】{r['text']}" for r in results)

    if degraded:
        return body + "\n\n" + DEGRADED_NOTE
    if results[0]["score"] < LOW_CONFIDENCE:
        return (body + f"\n\n（注：以上片段与问题的相关性偏低（{results[0]['score']:.2f}），"
                       "可能没有完全对应的条款；若确无直接依据，请如实说明「资料里没有明确规定」，"
                       "不要用推断代替原文。）")
    return body
