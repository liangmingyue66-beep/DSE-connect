"""Agent 状态定义（LangGraph State）。

ReAct 循环下，核心状态就是 messages（对话历史），
外加 memory 用于跨轮的工具参数补全（避免模型回忆历史抄错）。
"""
from langgraph.graph import MessagesState


class AgentState(MessagesState):
    memory: dict       # {"last_slots": {...}} 跨轮短期记忆，供工具参数补全
    guard_count: int   # answer_guard 强制重查次数（防死循环）
    needs_retry: bool  # answer_guard 判定需重查（疑似编造分数）
    tool_count: int    # 工具调用次数（max_steps 上限）
    total_tokens: int  # 累计 token（单任务成本预算）
    score_mismatch_count: int  # 分数原文校验不匹配次数（>=2 则让模型向用户确认）
