#!/usr/bin/env python3
"""Agent 评测脚本（L1 确定性断言）。

用法：
    .venv/Scripts/python.exe eval/run_eval.py                  # 跑全部
    .venv/Scripts/python.exe eval/run_eval.py --limit 3        # 先跑 3 条验证
"""
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.messages import HumanMessage

from app.graph import graph

RECURSION_LIMIT = 30  # 节点执行上限（护栏重查/多省会消耗较多节点；防死循环主要靠 max_steps）


async def run_agent(user_input) -> dict:
    """user_input 可为 str（单轮）或 list[str]（多轮）；'__LONG__' 替换为超长文本。

    注意：必须全程跑在**同一个事件循环**里（由 main 统一 asyncio.run 驱动）。
    若每条用例各调一次 asyncio.run，LLM 客户端的连接池会绑定在已关闭的旧循环上，
    报 "Event loop is closed"。
    """
    if isinstance(user_input, str):
        user_input = [user_input]
    t0 = time.time()
    messages: list = []
    memory: dict = {}
    tools_called: list = []
    output = ""
    for text in user_input:
        if text == "__LONG__":
            text = "计算机" * 200  # 约 600 字超长输入
        messages.append(HumanMessage(content=text))
        state = await graph.ainvoke(
            {"messages": messages, "memory": memory},
            config={"recursion_limit": RECURSION_LIMIT},
        )
        messages = list(state["messages"])
        memory = state.get("memory") or {}
        for m in state["messages"]:
            if getattr(m, "tool_calls", None):
                tools_called.extend(tc["name"] for tc in m.tool_calls)
        output = state["messages"][-1].content or ""
    return {
        "tools_called": tools_called,
        "output": output,
        "steps": len(tools_called),
        "latency_ms": int((time.time() - t0) * 1000),
    }


def check(case: dict, actual: dict) -> list[str]:
    failures = []
    expect = case.get("expect", {})
    for t in expect.get("must_call_tool", []):
        if t not in actual["tools_called"]:
            failures.append(f"未调用必需工具 {t}")
    for t in expect.get("must_not_call_tool", []):
        if t in actual["tools_called"]:
            failures.append(f"调用了禁止工具 {t}")
    for kw in expect.get("output_contains", []):
        if kw not in actual["output"]:
            failures.append(f"输出缺「{kw}」")
    for kw in expect.get("must_not_contain", []):
        if kw in actual["output"]:
            failures.append(f"输出不应含「{kw}」")
    if expect.get("output_contains_any"):
        if not any(kw in actual["output"] for kw in expect["output_contains_any"]):
            failures.append(f"输出未包含任一：{expect['output_contains_any']}")
    for kw in expect.get("must_not_leak", []):
        if kw in actual["output"]:
            failures.append(f"泄露「{kw}」")
    # 安全断言：查了库（数据可信）或没给具体分数（没编造），二者满足其一即可
    if expect.get("output_safe"):
        called = "query_schools_tool" in actual["tools_called"]
        # 排除否定语境（"哪怕只差1分"不是给分数）
        cleaned = re.sub(r"差\s*\d+\s*分", "", actual["output"])
        has_score = bool(re.search(r"\d+\s*分", cleaned))
        if not called and has_score:
            failures.append("安全问题：未查库却给出具体分数")
    if not actual["output"].strip():
        failures.append("输出为空")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(PROJECT_ROOT / "eval" / "cases.jsonl"))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "eval" / "report.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--category", default="", help="按能力/分类过滤，如 意图理解 / 政策问答（多值用逗号分隔）")
    args = ap.parse_args()

    cases = [json.loads(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.category:
        wanted = {x.strip() for x in args.category.split(",") if x.strip()}
        # v2 用例用 capability 字段，旧用例用 category；两者都兼容
        cases = [c for c in cases if (c.get("capability") or c.get("category")) in wanted]
        if not cases:
            print(f"没有匹配「{args.category}」的用例，已退出（不跑空集）")
            return 1
    if args.limit:
        cases = cases[: args.limit]

    return asyncio.run(_run_cases(cases, args.out))


async def _run_cases(cases: list, out_path: str) -> int:
    """所有用例跑在同一个事件循环里（见 run_agent 的说明）。"""
    results = []
    for case in cases:
        try:
            actual = await run_agent(case["input"])
            failures = check(case, actual)
        except Exception as e:
            failures = [f"运行异常：{e}"]
            actual = {"tools_called": [], "output": "", "steps": 0, "latency_ms": 0}
        group = case.get("capability") or case.get("category") or "other"
        results.append({
            "id": case["id"], "category": group,
            "input": case["input"],
            "output": actual["output"],
            "passed": not failures, "failures": failures,
            "tools": actual["tools_called"], "steps": actual["steps"],
            "latency_ms": actual["latency_ms"],
        })
        inp = case["input"]
        inp_str = inp if isinstance(inp, str) else " / ".join(inp)
        print(f"{'PASS' if not failures else 'FAIL'}  {case['id']}  {group:>10}  "
              f"{inp_str[:22]:<24}  {'; '.join(failures)}")

    total = len(results)
    passed_n = sum(1 for r in results if r["passed"])
    by_cat: dict = {}
    for r in results:
        k = r["category"]
        by_cat.setdefault(k, {"total": 0, "passed": 0})
        by_cat[k]["total"] += 1
        by_cat[k]["passed"] += 1 if r["passed"] else 0

    report = {
        "total": total, "passed": passed_n,
        "pass_rate": passed_n / total if total else 0,
        "by_category": by_cat, "results": results,
    }
    Path(out_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n通过率：{passed_n}/{total} = {report['pass_rate']:.0%}")
    for cat, v in by_cat.items():
        print(f"  {cat:>12}: {v['passed']}/{v['total']}")
    # 有失败则退出码 1（便于 CI/回归）
    return 1 if passed_n < total else 0


if __name__ == "__main__":
    sys.exit(main())
