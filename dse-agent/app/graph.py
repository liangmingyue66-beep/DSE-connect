"""LangGraph ReAct 循环：agent 决策 ⇄ tools 执行，answer_guard 兜底防编造分数。

模型在循环里自己决定下一步（调工具 / 追问 / 回答），
查分数走确定性工具（query_schools_tool），代码兜底保证数据准确、模型不算数。
answer_guard 在模型要回答时校验：含分数却全程没查库 → 强制重查。
"""
import asyncio
import json
import re
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app import llm
from app import observability
from app.geo import normalize_province, province_of_city
from app.state import AgentState
from app.tools.agent_tools import query_schools_tool, search_policy_tool

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
SYSTEM_PROMPT = (_PROMPT_DIR / "agent" / "v1.0.0.md").read_text(encoding="utf-8")

TOOLS = [query_schools_tool, search_policy_tool]
_TOOLS_BY_NAME = {t.name: t for t in TOOLS}

# 滑动窗口：只把最近 N 轮对话发给模型（控 context，防长对话爆）
# 查询条件已存在 memory.last_slots，历史文本可以丢——不需要摘要压缩
MAX_HISTORY_TURNS = 6
_llm_with_tools = llm.get_llm().bind_tools(TOOLS)
_llm_with_tools_backup = llm.get_llm_backup().bind_tools(TOOLS)


async def _invoke_llm_with_fallback(messages):
    """主模型（Qwen）失败时自动降级备用（DeepSeek）。

    用 ainvoke（异步 HTTP）：等待网络期间不占线程，多个并发请求由事件循环轮流处理。
    若用同步 invoke，每个请求会独占一个线程干等（实测并发时先发的请求被拖慢一倍以上）。
    """
    try:
        return await _llm_with_tools.ainvoke(messages)
    except Exception as e:
        observability.log_event("llm.fallback", primary_error=type(e).__name__, detail=str(e)[:200])
        return await _llm_with_tools_backup.ainvoke(messages)

# 用户问题里出现这些词 = 在问分数类问题（answer_guard 据此判断，避免误伤政策问题）
_SCORE_ASK_WORDS = ("分数线", "最低分", "最高分", "多少分", "录取分", "总分", "能上", "能去", "几分", "什么成绩")
# 用户问题里出现这些词 = 在问政策（没查 search_policy 就拦截，防政策幻觉）
_POLICY_ASK_WORDS = ("志愿", "报名", "几科", "几所", "几个专业", "录取规则", "录取原则", "填报", "招生办法")
MAX_GUARD_RETRY = 2  # 强制重查上限，防死循环
INTERNAL_REMINDER = "【系统强化指令】上一轮回答涉及事实信息（分数或政策），但数据并非来自工具。涉及分数/录取数据时，必须先调用 query_schools_tool；涉及政策（志愿/报名/计分科目/录取规则）时，必须先调用 search_policy_tool 并只依据原文回答。严禁凭记忆编造。此指令仅供你遵守，不要向用户复述。"
MAX_STEPS = 5        # 最多 5 次工具调用（防死循环）
TOKEN_BUDGET = 80000  # 单请求 token 预算（只防极端异常烧钱；正常多轮对话远低于此，防死循环主要靠 max_steps）


def _truncate_messages(messages: list, max_turns: int = MAX_HISTORY_TURNS) -> list:
    """滑动窗口：保留最近 N 轮（必须从 human 边界截断）。

    why: 不能按"消息条数"截断——否则会把 AI(tool_calls) 和对应 ToolMessage 拆开，
         模型/API 会因"tool_calls 没有匹配结果"报错。从第 N 个最近的 human 消息开始切，
         保证一轮内的 ai/tool 消息完整。
    """
    if not messages:
        return []
    human_count = 0
    start = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].type == "human":
            human_count += 1
            if human_count == max_turns:
                start = i
                break
    return messages[start:]


def _to_api_messages(state: AgentState) -> list:
    """构造发给模型的 messages：**保留标准 tool_calls / ToolMessage 结构**，不做扁平化。

    （早期曾把工具结果伪装成 user 消息，是误诊 400 后的 hack——实测 DashScope 完全支持
    标准多轮 function 历史，且伪装成 user 会让"工具返回"与"用户输入"语义混淆、多工具时更乱。）
    """
    system = SYSTEM_PROMPT
    if state.get("needs_retry"):
        # 内部提醒拼进 system（模型只遵守、不会"回应"），且不进对话历史（用户不可见）
        system = system + "\n\n" + INTERNAL_REMINDER
    return [SystemMessage(content=system)] + _truncate_messages(list(state["messages"]))


async def agent(state: AgentState) -> dict:
    """LLM 决策节点：看历史 + 工具结果，决定下一步（调工具或回答）。"""
    t0 = time.time()
    resp = await _invoke_llm_with_fallback(_to_api_messages(state))
    usage = getattr(resp, "usage_metadata", {}) or {}
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    tool_calls = getattr(resp, "tool_calls", None) or []
    observability.log_event(
        "llm.chat",
        latency_ms=int((time.time() - t0) * 1000),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tools=[tc["name"] for tc in tool_calls] or None,   # 这一轮决定调哪些工具（None=直接回答）
        answer_chars=len(resp.content or ""),
    )
    # 内部提醒已消费，重置（下次 answer_guard 再按需触发）
    return {"messages": [resp], "needs_retry": False, "total_tokens": state.get("total_tokens", 0) + input_tokens + output_tokens}


def should_continue(state: AgentState):
    """有 tool_calls → 去执行工具；否则 → 回答前校验。"""
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None):
        return "answer_guard"
    return "stop" if _over_budget(state) else "tools"


def _over_budget(state: AgentState) -> bool:
    return state.get("tool_count", 0) >= MAX_STEPS or state.get("total_tokens", 0) >= TOKEN_BUDGET


def _brief_args(args: dict) -> dict:
    """日志用的参数摘要：只留关键字段，避免把长文本打进日志。"""
    keys = ("province", "city", "tiers", "school_names", "major_keywords",
            "scores", "question")
    return {k: args[k] for k in keys if args.get(k) not in (None, "", [])}


def _brief_result(result) -> dict | str:
    """日志用的结果摘要：结构化结果只记 total/shown，纯文本截断。

    排查"为什么 0 条"时，看 total 和 hint 就够；不必把整份结果打进日志。
    """
    text = str(result)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "total" in data:
            brief = {"total": data.get("total"), "shown": data.get("shown")}
            if data.get("hint"):
                brief["hint"] = str(data["hint"])[:40]
            return brief
    except (ValueError, TypeError):
        pass
    return text[:80]


def _verify_scores_in_text(scores, user_text: str) -> bool:
    """分数原文溯源：校验模型给的分数能否在用户原话里找到。

    归一化：只保留数字和星号（"我4/3/4想去" → "434"）。
    例：["4","3","4"] → "434"，原文"我434..." → 命中。
    局限：原文有多个数字/顺序不同可能误判（抓得住大部分抄错，抓不住全部）。
    """
    normalized = re.sub(r"[^0-9*]", "", user_text or "")
    joined = "".join(str(s).strip() for s in scores)
    return bool(joined) and joined in normalized


def _latest_human_text_with_digits(state: AgentState) -> str:
    """最近一条"含数字"的用户消息——模型给的分数应对应这条（而非不含数字的省略句）。"""
    for m in reversed(state["messages"]):
        if m.type == "human" and re.search(r"\d", m.content or ""):
            return m.content or ""
    return ""


async def call_tools(state: AgentState) -> dict:
    """执行模型要调的工具；query_schools 做参数补全 + 分数原文校验。

    工具内部是同步阻塞的（psycopg2 查库、httpx 调 embedding），所以用
    asyncio.to_thread 丢到线程池执行——否则会卡住事件循环，并发请求互相拖慢。
    """
    last = state["messages"][-1]
    memory = dict(state.get("memory") or {})
    msgs = []
    mismatch_total = state.get("score_mismatch_count", 0)

    for tc in last.tool_calls:
        name = tc["name"]
        args = dict(tc["args"])
        tool = _TOOLS_BY_NAME.get(name)
        t0 = time.time()

        if name == query_schools_tool.name:
            model_args = dict(args)  # 补全前：判断"本轮是否新给了分数"
            # 参数补全：模型这轮没给的，从 memory 补（模型只给增量，不回忆历史，避免 434→454）
            last_slots = dict(memory.get("last_slots") or {})
            for k in ("province", "city", "tiers", "school_names", "major_keywords", "scores"):
                if not args.get(k) and last_slots.get(k):
                    args[k] = last_slots[k]
            # 具体学校名优先：提到具体学校时清空省份/城市，避免"上海市的中南大学"这种矛盾条件
            if args.get("school_names"):
                args.pop("province", None)
                args.pop("city", None)

            # 省份归一（枚举校验：模型可能输出"华东省""广州"等非法省份）
            param_error = None
            if args.get("province") and not args.get("school_names"):
                norm, err = normalize_province(args["province"])
                if err:
                    param_error = err
                else:
                    args["province"] = norm

            # 城市与省份冲突 → 程序清空省份（城市属于哪个省是事实，代码判断，不靠模型）
            if not param_error and args.get("city") and args.get("province"):
                city_prov = province_of_city(args["city"])
                if city_prov and city_prov != args["province"]:
                    observability.log_event("param.province_cleared",
                                             city=args["city"], old_province=args["province"])
                    args.pop("province", None)

            # 分数原文溯源：只校验模型"本轮新给的"分数（继承来的上一轮已校验过）
            score_invalid = False
            if not param_error and model_args.get("scores"):
                user_text = _latest_human_text_with_digits(state)
                if not user_text or not _verify_scores_in_text(model_args["scores"], user_text):
                    score_invalid = True
                    mismatch_total += 1
                    if mismatch_total < 2:
                        result = (f"分数校验失败：你提取的分数 {model_args['scores']} 无法在用户原话中找到。"
                                  f"用户原话是「{user_text[:80]}」。请重新提取分数；若用户没明确说分数，就不要填。")
                    else:
                        result = ("分数校验多次失败：请直接询问用户其 DSE 三科成绩（中文、英文、数学），"
                                  "不要自行推测或沿用不确定的分数。")
                    observability.log_event("score.mismatch", scores=model_args["scores"], user_text=user_text[:50])

            if param_error:
                result = param_error
            elif not score_invalid:
                try:
                    result = await asyncio.to_thread(query_schools_tool.invoke, args)
                    memory["last_slots"] = {k: v for k, v in args.items() if v not in (None, "", [])}
                except Exception as e:
                    # schema/参数校验失败 → 返回错误给模型，让它修正后重试（ReAct 的"观察错误"）
                    result = (f"工具调用失败（参数格式错误）：{e}。请检查后重试——"
                              "school_names 必须是字符串数组，如 [\"湖南大学\",\"中南大学\"]；scores 必须是三个等级的数组。")
        elif tool is not None:
            try:
                result = await asyncio.to_thread(tool.invoke, args)
            except Exception as e:
                result = f"工具调用失败：{e}。请检查参数格式后重试。"
        else:
            result = f"未知工具：{name}"

        msgs.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
        observability.log_event(
            "tool.call",
            tool=name,
            latency_ms=int((time.time() - t0) * 1000),
            args=_brief_args(args),
            result=_brief_result(result),
        )

    return {"messages": msgs, "memory": memory,
            "tool_count": state.get("tool_count", 0) + 1,
            "score_mismatch_count": mismatch_total}


def answer_guard(state: AgentState) -> dict:
    """回答后校验（防编造分数，避免误伤政策问题）。

    1. 用户问的是分数类问题、没调 query_schools、回答却有具体分数 → 编造；
    2. 工具返回 0 条，但回答出现具体分数（\\d+分）→ 编造。
    政策问题（如"校长推荐计划有什么要求"）回答里的"8分"不算编造，不触发。
    """
    last = state["messages"][-1]
    content = last.content or ""
    user_q = ""
    for m in reversed(state["messages"]):
        if m.type == "human":
            user_q = m.content or ""
            break
    asks_score = any(k in user_q for k in _SCORE_ASK_WORDS)
    asks_policy = any(k in user_q for k in _POLICY_ASK_WORDS)
    has_concrete_score = bool(re.search(r"\d+\s*分", content))
    called_schools = any(
        getattr(m, "tool_calls", None) and any(tc["name"] == query_schools_tool.name for tc in m.tool_calls)
        for m in state["messages"]
    )
    called_policy = any(
        getattr(m, "tool_calls", None) and any(tc["name"] == search_policy_tool.name for tc in m.tool_calls)
        for m in state["messages"]
    )
    tool_empty = any(m.type == "tool" and '"total": 0' in (m.content or "") for m in state["messages"])
    guard_count = state.get("guard_count", 0)

    if guard_count >= MAX_GUARD_RETRY:
        return {"needs_retry": False}
    if asks_score and not called_schools and has_concrete_score:
        return {"guard_count": guard_count + 1, "needs_retry": True}
    if tool_empty and has_concrete_score:
        return {"guard_count": guard_count + 1, "needs_retry": True}
    # 政策问题没查 search_policy → 强制重查（防"5所/四科"这类政策幻觉）
    if asks_policy and not called_policy:
        return {"guard_count": guard_count + 1, "needs_retry": True}
    return {"needs_retry": False}


def route_after_guard(state: AgentState):
    return "agent" if state.get("needs_retry") else END


def stop(state: AgentState) -> dict:
    """预算超限：优雅停止，返回友好提示。"""
    return {"messages": [AIMessage(content="（本次查询的步数或 token 已达上限，请把问题拆开问，或换种更简洁的问法。）")]}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("agent", agent)
    g.add_node("tools", call_tools)
    g.add_node("answer_guard", answer_guard)
    g.add_node("stop", stop)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", should_continue, {"tools": "tools", "answer_guard": "answer_guard", "stop": "stop"})
    g.add_edge("tools", "agent")
    g.add_conditional_edges("answer_guard", route_after_guard, {"agent": "agent", END: END})
    g.add_edge("stop", END)
    return g.compile()


graph = build_graph()
