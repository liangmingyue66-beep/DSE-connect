# 源码导读 · `app/graph.py`

> 这是整个项目的心脏（323 行）。**读透这一个文件，面试的核心问题就都能答**。
> 读法：先看第 0 节的全貌图（5 分钟），再按顺序读函数，每读完一个回答文末的自测题。

---

## 0. 先看全貌：这个图长什么样

```
                    ┌──────────────────────────────┐
                    │                              │
   START ──▶ [agent] ──无 tool_calls──▶ [answer_guard] ──needs_retry──┐
              │  ▲                          │                        │
   有 tool_calls│  │                        │ 不需要重试              │
              ▼  │                          ▼                        │
           [tools] ┘                       END                       │
              │                                                      │
        超预算 │                                                      │
              ▼                                                      │
            [stop] ──▶ END                              └──▶ 回到 agent ─┘
```

**四个节点，各管一件事**：

| 节点 | 干什么 | 对应函数 |
|---|---|---|
| `agent` | 调模型，决定"调工具"还是"回答" | `agent()` |
| `tools` | 真正执行工具（查库/查政策） | `call_tools()` |
| `answer_guard` | 回答后校验：是不是在编？ | `answer_guard()` |
| `stop` | 预算超限，优雅收尾 | `stop()` |

**一句话记住**：`agent` 想 → `tools` 做 → 回到 `agent` 想 → 要回答时先过 `answer_guard` 检查 → 通过就结束，不通过打回重来。

---

## 1. 文件头：全局配置（第 22-54 行）

```python
SYSTEM_PROMPT = (_PROMPT_DIR / "agent" / "v1.0.0.md").read_text(encoding="utf-8")
TOOLS = [query_schools_tool, search_policy_tool]
_llm_with_tools = llm.get_llm().bind_tools(TOOLS)
_llm_with_tools_backup = llm.get_llm_backup().bind_tools(TOOLS)
```

**四个常量，每个都有原因，面试问到就是知识点**：

| 常量 | 值 | 为什么是这个数 |
|---|---|---|
| `MAX_HISTORY_TURNS` | 6 | 只发最近 6 轮给模型，控 context；条件已存 memory，历史文本可丢 |
| `MAX_GUARD_RETRY` | 2 | 护栏最多打回 2 次，再不行就放行，防死循环 |
| `MAX_STEPS` | 5 | 最多 5 次工具调用 |
| `TOKEN_BUDGET` | 80000 | 只防极端烧钱；正常对话远低于此（防死循环主要靠 `MAX_STEPS`） |

> ⚠️ **诚实点**：`TOKEN_BUDGET` 在服务路径（`astream`）下实际拿不到 usage，所以恒为 0、不生效——**真正起作用的是 `MAX_STEPS`**。这点被问到要承认。

**`bind_tools(TOOLS)` 做了什么？** 把工具列表交给模型，模型就知道"我有哪些工具、参数长什么样"。注意：**模型只是"知道"，不会自己执行**。

---

## 2. `_invoke_llm_with_fallback`（第 35-45 行）

```python
async def _invoke_llm_with_fallback(messages):
    try:
        return await _llm_with_tools.ainvoke(messages)
    except Exception as e:
        observability.log_event("llm.fallback", primary_error=..., detail=...)
        return await _llm_with_tools_backup.ainvoke(messages)
```

**做什么**：调 Qwen，失败就切 DeepSeek。

**三个面试点**：

1. **为什么用 `ainvoke` 而不是 `invoke`？**
   同步 `invoke` 会占着一个线程干等 3~10 秒；异步 `ainvoke` 等待时不占线程，事件循环能去处理别的请求。实测：两条并发从 3.00s → 1.51s（假模型）。

2. **降级有什么风险？**
   DeepSeek 和 Qwen 对 prompt 的服从度不同，**降级后护栏效果可能变化**。所以降级是"保底"，不是"等价替换"。

3. **为什么不重试同一个模型？**
   底层 `ChatOpenAI` 已经有 `max_retries=2`（配置在 `llm.py`）；这里是**换供应商**，不是重试。

---

## 3. `_truncate_messages`（第 57-74 行）

```python
def _truncate_messages(messages, max_turns=MAX_HISTORY_TURNS):
    # 从最近的 human 消息往前数，数到第 max_turns 个就开始切
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].type == "human":
            human_count += 1
            if human_count == max_turns:
                start = i
                break
    return messages[start:]
```

**做什么**：只保留最近 6 轮对话。

**关键设计：为什么必须"从 human 边界"截断？**

一条完整的轮次是：

```
HumanMessage          ← 用户说
AIMessage(tool_calls) ← 模型要调工具
ToolMessage           ← 工具结果
AIMessage             ← 模型回答
```

如果按"条数"截断（比如 `messages[-24:]`），可能把一个轮次切一半——**只剩 `AIMessage(tool_calls)` 却没有对应的 `ToolMessage`，API 直接报错**。

**所以按 human 数，不从中间切。**

**追问**：为什么不摘要压缩？
→ 因为查询条件已经结构化存在 `memory.last_slots` 里，历史文本的信息价值低，直接截断就够。摘要要多一次 LLM 调用，不值。

---

## 4. `_to_api_messages`（第 77-87 行）

```python
system = SYSTEM_PROMPT
if state.get("needs_retry"):
    system = system + "\n\n" + INTERNAL_REMINDER
return [SystemMessage(content=system)] + _truncate_messages(list(state["messages"]))
```

**做什么**：拼出"发给模型的完整消息"。

**两个细节，都是踩坑换来的**：

1. **内部提醒拼进 system，不拼进 user**
   早期是把提醒当 user 消息注入 → 模型把它当"用户要求"，回复"明白，已内化规则 ✅"，还进了历史。改成拼 system：模型只**遵守**，不会**回应**，而且不进历史（用户不可见）。

2. **保留标准 `tool_calls` / `ToolMessage` 结构**
   注释里写着黑历史：早期误诊 400 错误，把工具结果伪装成 user 消息，结果多工具时语义混乱、模型还学会模仿"（已调用工具…）"。后来实测 DashScope 完全支持标准格式，撤回了。

> **面试价值**：这段注释本身就是"我怎么排查问题"的证据——**误诊 → hack → 发现 hack 引出新问题 → 验证标准做法 → 撤回**。

---

## 5. `agent`（第 90-107 行）—— 决策节点

```python
async def agent(state):
    resp = await _invoke_llm_with_fallback(_to_api_messages(state))
    # 记录日志：耗时、token、调了哪些工具
    return {"messages": [resp], "needs_retry": False, "total_tokens": ...}
```

**做什么**：调一次模型，把回复加进历史。

**它只做三件事**：
1. 拼消息 → 调模型 → 拿回复
2. 打日志（耗时 / token / 决定调哪些工具）
3. **重置 `needs_retry`**（提醒已消费，下次再由 guard 决定要不要加）

**为什么这个节点这么"薄"？**
因为**判断逻辑都在别处**：要不要调工具由 `should_continue` 决定，该不该拦由 `answer_guard` 决定。节点只负责"问模型一次"——职责单一，好调试。

---

## 6. `should_continue` + `_over_budget`（第 110-119 行）—— 路由

```python
def should_continue(state):
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None):
        return "answer_guard"          # 不调工具了 → 说明要给答案，先去校验
    return "stop" if _over_budget(state) else "tools"
```

**三条分支，对应图上的三条边**：

| 条件 | 去哪 | 为什么 |
|---|---|---|
| 回复里**没有** `tool_calls` | `answer_guard` | 模型决定直接回答，但**不能直接给用户**，先校验 |
| 有 `tool_calls`，且**没超预算** | `tools` | 正常：去执行工具 |
| 有 `tool_calls`，但**超预算** | `stop` | 防止无限调工具烧钱 |

**面试点**："为什么回答前一定要过 guard？" → 因为模型可能没查库就编，**这是最后一道闸**。

---

## 7. 日志摘要：`_brief_args` / `_brief_result`（第 122-144 行）

```python
def _brief_result(result):
    data = json.loads(text)
    if "total" in data:
        return {"total": ..., "shown": ..., "hint": ...}   # 只记摘要
    return text[:80]
```

**做什么**：把工具参数和结果"压缩"进日志。

**为什么不能全打？**
- 参数里可能有超长文本，日志会爆；
- 结果可能是 20 条完整记录，没必要。

**判别标准**（面试可以说）：**看这一行日志能不能判断"这一步对不对"**。排查 0 条时，看 `total` 和 `hint` 就够了。

---

## 8. 分数校验：`_verify_scores_in_text`（第 147-164 行）

```python
normalized = re.sub(r"[^0-9*]", "", user_text)   # "我4/3/4想去" → "434"
joined = "".join(scores)                          # ["4","3","4"] → "434"
return joined in normalized
```

**做什么**：校验"模型给的分数，能不能在用户原话里找到"。

**为什么需要它？**
模型会把 434 抄成 454（真实发生过）。格式校验（必须是 3 个元素）拦不住这种错——`["4","5","4"]` 格式完全合法。

**配套的 `_latest_human_text_with_digits`**：找**最近一条含数字**的用户消息。为什么？因为第二轮"那北京呢"不含数字，拿它去比对必然失败——基准要选对。

**局限（被问要承认）**：原文有多个数字、或用户按"英中数"顺序说时，可能**误判**（模型对，但字符串对不上）。后果是多重试一次，不会给错数据。

---

## 9. `call_tools`（第 167-257 行）—— 最复杂的节点

这个函数做了 **6 件事**，按顺序看：

```python
async def call_tools(state):
    for tc in last.tool_calls:          # 模型可能一次要调多个工具
        name = tc["name"]
        args = dict(tc["args"])
        if name == query_schools_tool.name:
            ...
```

### ① 参数补全（第 186-190 行）—— 多轮记忆的核心

```python
for k in ("province", "city", "tiers", "school_names", "major_keywords", "scores"):
    if not args.get(k) and last_slots.get(k):
        args[k] = last_slots[k]
```

**模型这轮没给的，从 `memory.last_slots` 补。**

**为什么不让模型自己从历史里补？** 因为它会抄错（434→454）。**模型只输出增量（"北京市"），其余由代码补**。

### ② 清空矛盾条件（第 192-194 行）

```python
if args.get("school_names"):
    args.pop("province", None)
    args.pop("city", None)
```

**提到具体学校时，清掉省份/城市**——否则查"上海市的中南大学"，0 条。

**追问**："这是模型做的还是代码做的？" → **代码**。因为"学校在哪个省"是事实，不该靠模型判断。

### ③ 省份归一（第 196-203 行）

```python
norm, err = normalize_province(args["province"])
if err: param_error = err
else: args["province"] = norm
```

**为什么需要**？模型可能输出"华东省""广州"这种非法省份 → `WHERE province='华东省'` → 0 条。

**做法**：省份是枚举（23 个），可以硬校验。和"专业词无法枚举、只能靠模型"形成对比——**这就是"分层校验"**。

### ④ 城市与省份冲突（第 205-211 行）

```python
city_prov = province_of_city(args["city"])
if city_prov and city_prov != args["province"]:
    args.pop("province", None)      # 程序清空
```

**同样由代码判断**：广州属于广东，这是事实。

### ⑤ 分数原文溯源（第 213-226 行）

只校验**模型本轮新给的**分数（`model_args` 是补全前的快照）。继承来的分数上一轮已经验过了，不重复验。

失败时返回什么？
- 第 1 次：告诉模型"你提取的分数在原文里找不到，用户原话是 XXX，请重新提取"
- 第 2 次：让模型**直接问用户**三科成绩（不要继续猜）

### ⑥ 真正执行工具（第 231-237 行）

```python
result = await asyncio.to_thread(query_schools_tool.invoke, args)
memory["last_slots"] = {k: v for k, v in args.items() if v not in (None, "", [])}
```

**两个点**：

- **`asyncio.to_thread`**：工具内部是同步阻塞的（psycopg2、httpx），丢线程池里跑，别卡事件循环。
- **只在成功时更新 memory**：参数错了就不该污染记忆。

**异常处理**：schema 校验失败时，**把错误当 ToolMessage 返回给模型**，让它自己修正参数重试——这就是 ReAct 的"观察错误"。

---

## 10. `answer_guard`（第 260-297 行）—— 护栏，必背

```python
# 四个信号
asks_score        = 用户问题里有"分数线/多少分/能上…"
asks_policy       = 用户问题里有"志愿/报名/几科…"
has_concrete_score = 回答里有 `\d+分`
called_schools    = 全程调过 query_schools_tool 吗
called_policy     = 全程调过 search_policy_tool 吗
tool_empty        = 有没有工具返回 "total": 0

# 三个触发条件
1. 问分数 + 没查库 + 有分数      → 重查
2. 工具返回 0 条 + 有分数        → 重查（查空了还编）
3. 问政策 + 没查政策库           → 重查
```

**关键：这是启发式，不是事实核查。**

代码**无法直接知道**"回答里这个数字是不是工具给的"，只能看"有没有调过工具"。

**所以它拦不住什么？**（必被问）
> **"查了库但夹带私货"拦不住**——模型调了工具拿到 6 条真实数据，回答里又自己编了个"最高 19 分"，代码看不出来（因为确实调过工具）。

要彻底解决需要**逐字比对**：把回答里的每个数字回查工具结果，找不到就判定编造。**没做**，因为复杂且成本高。

**为什么会被误伤，怎么修的？**
政策回答里"校长推荐计划要求 8 分"会被当成"编造分数" → 强制去查分数线，答非所问。
修法：**先判断"用户是不是在问分数"**（`asks_score`），政策问题不触发第一条。

**`MAX_GUARD_RETRY = 2`**：打回两次还不行就放行，防止"模型就是不查库"导致死循环。

---

## 11. `route_after_guard` / `stop`（第 300-306 行）

```python
def route_after_guard(state):
    return "agent" if state.get("needs_retry") else END
```

护栏打完分：要重试就回 `agent`（模型重新决策），否则结束。

```python
def stop(state):
    return {"messages": [AIMessage(content="（本次查询的步数或 token 已达上限…）")]}
```

**优雅停止**：不是抛异常，而是返回一句人话。用户体验上"有反馈"比"报错"好。

---

## 12. `build_graph`（第 309-323 行）

```python
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
```

**面试可以画这个图**（第 0 节那张）。画出来比说十句都强。

**`AgentState` 是什么？** 一个 `TypedDict`（在 `app/state.py`），定义节点间传递的字段：`messages`、`memory`、`tool_count`、`guard_count`、`needs_retry`、`total_tokens`。

---

## 13. 读完之后，自测 10 题

不看代码回答，答不上来就回去看对应小节：

1. 图里有几个节点？`agent` 有几种去处？
2. 为什么截断要按 human 边界，不能按条数？
3. 内部提醒为什么拼进 system 而不是 user？
4. 多轮对话里，"计算机"和"434"是谁补的？为什么不靠模型？
5. 提到具体学校时为什么要清空省份？
6. 分数校验的基准为什么是"最近一条含数字的用户消息"？
7. `answer_guard` 看哪几个信号？三个触发条件分别是什么？
8. 护栏**拦不住**什么情况？
9. 工具执行为什么用 `asyncio.to_thread`？
10. `MAX_STEPS` 和 `MAX_GUARD_RETRY` 分别防什么？

<details>
<summary>答案要点（先自己答，再点开）</summary>

1. 4 个；`tools`（调工具）/ `answer_guard`（要回答）/ `stop`（超预算）
2. 按条数会把 `AI(tool_calls)` 和 `ToolMessage` 拆开 → API 报错
3. 拼 system 模型只遵守不回应；拼 user 会被当"用户要求"回应，还会进历史
4. 代码从 `memory.last_slots` 补；模型只给增量，防止抄错数字
5. 否则查"上海市的中南大学"→ 0 条（学校归属是事实，不该被旧省份限制）
6. 第二轮"那北京呢"不含数字，拿它比对必然失败
7. 信号：问分数、问政策、回答有分数、调过库/政策、工具返回 0；条件：①问分数+没查库+有分数 ②工具空+有分数 ③问政策+没查政策
8. "查了库但夹带私货"——代码只知道调过工具，不知道每个数字来源
9. 工具内部是同步阻塞的（psycopg2/httpx），不挪走会卡住事件循环，并发退化成串行
10. `MAX_STEPS` 防"无限调工具"；`MAX_GUARD_RETRY` 防"护栏反复打回"形成死循环

</details>

---

## 附：这个文件里最值得讲的 3 个故事

| 故事 | 在哪 | 怎么讲 |
|---|---|---|
| **误诊 400 → hack → 撤回** | 第 77-87 行注释 | "我以为 DashScope 不支持多轮 tool 历史，做了个把工具结果伪装成用户消息的 hack，结果引出更多 bug；后来实测标准格式完全可用" |
| **护栏误伤政策问题** | 第 274 行附近 | "政策回答里的'8分要求'被当成编造分数，强制去查分数线，答非所问；修法是先判断用户问的是不是分数" |
| **同步改异步** | 第 35 行 + 第 232 行 | "两条并发 3.00s → 1.51s；但改 async 后同步 `invoke` 驱动不了节点，评测全挂" |
