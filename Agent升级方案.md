# Agent 升级方案 · 香港 DSE 志愿填报助手

> 基于 `E:\deepseek test` 的现有资产，升级成**对话式 Agent 助手**。  
> **路线裁决（以最新需求为准）**：直接用 Agent（LangGraph/LangChain），旧方案作废——
>
> - ❌ 作废 1：`docs/DSE志愿填报助手-项目方案.md`（Next.js + SQLite 全栈，AI 是二期）
> - ❌ 作废 2：`PROJECT_ROADMAP.md` §4「框架放最后、小步手写」的技术顺序
> - ✅ 保留：真项目的不变量、已完成的阶段 0 资产（数据/验证/前端）

---

## 1. 一句话本质

现有的是一个**数据工具**（3794 条数据 + 静态筛选网页，已上线）。  
升级 = 在它上面加一层 **Agent**：**LLM 负责"聊天 + 意图翻译"，确定性查询由代码做**，把"筛选网页"变成"对话式助手"。

---

## 2. 现有资产（复用，别重造）

| 资产        | 位置                                                                                                   | 复用于             |
| --------- | ---------------------------------------------------------------------------------------------------- | --------------- |
| **数据母表**  | `data_all/programmes_all.csv`（**3794 条**，22 字段）+ registry_all(165校) + universities_all + 额外要求清单(300) | 入库 → SQL 查询     |
| 数据管线      | `run_provinces.py` + `data_gd_batch/extract_guangdong.py` + `verify*.py`                             | 数据已就绪；只有改数据时才重跑 |
| 静态前端      | `webv0/`（index.html/app.js/data.js，**已上线 GitHub Pages**）                                             | 改造成对话浮窗         |
| 后端起步      | `backend/models.py`（`School` 模型，MySQL 库 `test`）                                                      | 扩展 `Program` 模型 |
| **认知/文档** | `HANDOFF.md` / `PROJECT_ROADMAP.md` / `README.md` / `INTERVIEW_NOTES.md` / `项目复盘日志.md`               | 项目不变量 + 面试素材    |

> 数据字段（`programmes_all.csv`）：页码 / 院校 / 省份 / 城市 / 院系 / 专业名称 / 授课语言 / 体艺类 / 录取人数 / 最高·最低(中英数) / 志愿序号 / 总分 / 额外科目要求 / 专业要求 / 有收生数据。

---

## 3. 技术栈（以新需求为准）

| 层          | 选型                               | 说明                                         |
| ---------- | -------------------------------- | ------------------------------------------ |
| **编排**     | **LangGraph**                    | agent 多步循环（替代已弃用的 LangChain AgentExecutor） |
| **组件**     | **LangChain**                    | ChatModel / Tool / Retriever / Prompt      |
| **LLM**    | **DeepSeek**（项目已用）或通义            | 只做聊天 + 意图翻译                                |
| **数据库**    | **PostgreSQL**（弃真项目原用的 MySQL）    | 一个库 + 多张表（书数据 + 用户数据）                      |
| **结构化查询**  | **SQL**（Text-to-SQL / SQL Agent） | 分数线查询，**模型不算数**                            |
| **政策 RAG** | LangChain Retriever + 向量库        | 政策 FAQ（332A/顺序志愿/报名流程）                     |
| **用户数据**   | 收藏/对比/会话历史 → MySQL 表             | 才是有意义的 CRUD                                |
| **后端**     | **FastAPI**（+ SSE 流式）            | agent 多步耗时，必须流式                            |
| **前端**     | 对话浮窗（改 `webv0/`）                 | 复用现有静态前端                                   |
| **可观测**    | LangSmith                        | trace，工程化必备                                |

---

## 4. 架构（双轨 —— 呼应项目不变量）

```
用户自然语言："我 4/5/4，想去广东读计算机"
        │
        ▼
   Agent（LLM：只做聊天 + 意图翻译 + 组织回答）
        ├── query_schools(省份, 成绩, 专业词)  → 确定性 SQL 查询（模型不计算）
        └── search_policy(问题)                → 政策 RAG（FAQ 类）
        │
        ▼
   回答（院校专业清单 + 可回查书页页码）
```

**沿用真项目不变量**（来自 `PROJECT_ROADMAP` §1）：

1. **书数据只读**（PDF→管线→母表，不手输）；
2. **LLM 不计算**（筛选/排序/页码回溯全由代码做）；
3. **两层数据分开**：书数据（只读）+ 用户数据（收藏/会话，可写）；
4. 数据改动走"全库回归"。

---

## 5. 目录结构建议（新文件夹）

```
<新文件夹>/
├── data/                  # 从 deepseek test 复制 data_all/*.csv
├── app/
│   ├── agent.py           # LangGraph agent（编排）
│   ├── tools.py           # query_schools / search_policy
│   ├── db.py              # MySQL 连接 + 模型（schools / programs）
│   └── main.py            # FastAPI 入口
├── web/                   # 对话浮窗（改自 webv0/）
├── docs/                  # 本方案 + 现有文档
└── .env                   # 数据库/LLM 凭据（不进 Git）
```

---

## 6. 实施步骤（三步走，每步可运行）

1. **数据入库**：`data_all/*.csv` → MySQL（`schools` 165 行 + `programs` 3794 行）；
2. **工具 + Agent**：写 `query_schools` 纯函数 → LangGraph agent（1 个工具）→ 命令行对话跑通；
3. **接 API + 前端**：FastAPI 接口（SSE）→ 前端对话浮窗 → 端到端联调；
4. （可选）**政策 RAG**：补 `search_policy` 工具。

---

## 7. 原则与待确认

**三条原则**：

1. **以"做对"为准，不因"现成"而将就**——现有资产（数据管线/静态前端）能用则复用，有更好的做法就改，不为"照搬"而将就；
2. **数据库：一个 PostgreSQL 库，多张表，不物理分库**。"两层数据分开"指**逻辑分开**（书数据表只读、用户数据表可写），不是分两个 database——几千条数据规模下分库是过度设计，JOIN 也不方便；
3. **AI 直接写代码**（不再走"用户亲手写"的学习模式）。

**待确认**：项目名 / 新文件夹位置。
