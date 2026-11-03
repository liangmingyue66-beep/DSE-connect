#!/usr/bin/env python3
"""命令行对话入口（ReAct 版）。

用法：
    .venv/Scripts/python.exe app/main.py
"""
import asyncio
import sys
from pathlib import Path

# 直接运行本文件（python app/main.py）时，默认 sys.path 只有 app/ 目录，
# 找不到 app 包；把项目根插进去，保证任何方式启动都能导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.messages import HumanMessage

from app.graph import graph
from app import observability


async def _repl() -> None:
    messages: list = []
    memory: dict = {}
    print("DSE 志愿填报助手（输入 exit / 退出 结束）\n")
    while True:
        try:
            # input() 是阻塞的：放线程里，别卡住事件循环
            user = (await asyncio.to_thread(input, "你：")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user.lower() in ("exit", "quit", "退出", "q"):
            print("再见！")
            break
        if not user:
            continue
        messages.append(HumanMessage(content=user))
        observability.set_trace_id(observability.new_trace_id())
        try:
            # 图的节点是 async 的；整个 REPL 共用一个事件循环，
            # 否则 LLM 客户端的连接池会绑定在已关闭的旧循环上
            state = await graph.ainvoke({"messages": messages, "memory": memory})
        except Exception as e:
            # 详情进日志，用户看友好提示；回滚这条失败的用户消息
            observability.log_event("error", error_type=type(e).__name__, detail=str(e)[:300])
            print("\n助手：抱歉，系统处理时出了点问题，请重试。")
            messages.pop()
            continue
        messages = list(state["messages"])
        memory = state.get("memory") or {}
        print("\n助手：" + (state["messages"][-1].content or "（无回答）"))


def main() -> None:
    asyncio.run(_repl())


if __name__ == "__main__":
    main()
