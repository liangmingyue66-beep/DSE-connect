"""FastAPI 后端：把 ReAct Agent 包装成 SSE 流式接口，并托管前端静态页。

数据模型（从"一个凭证 = 一个会话"升级为"用户 → 多会话"）：
    token(用户凭证) --HMAC 校验--> user_id
    user_id --1:N--> session_id --1:N--> messages

接口：
    POST   /auth/token              签发用户凭证
    GET    /sessions                会话列表（按最近活跃排序）
    POST   /sessions                新建会话
    GET    /sessions/{sid}/messages 历史消息
    DELETE /sessions/{sid}          删除会话
    POST   /chat                    SSE 流式对话
    GET    /health                  健康检查

用法：
    .venv/Scripts/python.exe -m uvicorn app.server:app --port 8000
"""
import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app import auth, observability
from app.graph import graph

ROOT = Path(__file__).resolve().parent.parent
# 前端构建产物（npm run build 生成，不入库）。未构建时服务仍可启动，只提供 API。
FRONTEND_DIST = ROOT / "frontend" / "dist"
WEB_DIR = FRONTEND_DIST if FRONTEND_DIST.exists() else None

RECURSION_LIMIT = 30
SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
SWEEP_INTERVAL = int(os.getenv("SESSION_SWEEP_INTERVAL", "300"))
SESSION_LOCK_TIMEOUT = int(os.getenv("SESSION_LOCK_TIMEOUT", "90"))
MAX_SESSIONS_PER_USER = int(os.getenv("MAX_SESSIONS_PER_USER", "50"))
TITLE_MAX_LEN = 20


async def _session_janitor():
    """后台清理：定期回收长期不活跃的会话，防内存泄漏。"""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL)
        removed = sweep_sessions()
        if removed:
            observability.log_event("session.sweep", removed=removed, alive=len(SESSIONS))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_session_janitor())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="DSE 志愿填报助手", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def no_cache_html(request, call_next):
    """index.html 不缓存：否则重新构建后浏览器还拿旧页面，改了看不见效果。"""
    response = await call_next(request)
    if request.url.path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

# {session_id: {owner, title, messages, memory, created_at, last_active}}
SESSIONS: dict[str, dict] = {}
# {user_id: [session_id, ...]}  保持插入顺序，展示时按 last_active 排序
USER_SESSIONS: dict[str, list[str]] = {}
# 每会话一把锁：同一会话的请求串行，避免两个请求同时改 messages 互相踩
SESSION_LOCKS: dict[str, asyncio.Lock] = {}


# ---------- 会话存取 ----------

def _new_session(user_id: str, title: str = "") -> dict:
    sid = uuid.uuid4().hex[:16]
    now = time.time()
    SESSIONS[sid] = {
        "owner": user_id,
        "title": title,
        "messages": [],
        "memory": {},
        "created_at": now,
        "last_active": now,
    }
    USER_SESSIONS.setdefault(user_id, []).append(sid)
    return SESSIONS[sid] | {"id": sid}


def _get_owned_session(sid: str, user_id: str) -> dict | None:
    """取会话并校验归属：不是自己的会话一律当作不存在（防越权访问）。"""
    session = SESSIONS.get(sid)
    if session is None or session.get("owner") != user_id:
        return None
    return session


def _touch(session: dict) -> None:
    session["last_active"] = time.time()


def _get_lock(session_id: str) -> asyncio.Lock:
    lock = SESSION_LOCKS.get(session_id)
    if lock is None:
        lock = SESSION_LOCKS[session_id] = asyncio.Lock()
    return lock


def _drop_session(sid: str) -> None:
    session = SESSIONS.pop(sid, None)
    if session:
        owner = session.get("owner")
        if owner in USER_SESSIONS:
            USER_SESSIONS[owner] = [x for x in USER_SESSIONS[owner] if x != sid]
            if not USER_SESSIONS[owner]:
                USER_SESSIONS.pop(owner, None)
    lock = SESSION_LOCKS.get(sid)
    if lock is not None and not lock.locked():
        SESSION_LOCKS.pop(sid, None)


def sweep_sessions(now: float | None = None) -> int:
    """回收超过 TTL 未活动的会话，返回回收数量（纯函数，便于单测）。"""
    now = now or time.time()
    expired = [sid for sid, s in SESSIONS.items() if now - s.get("last_active", 0) > SESSION_TTL]
    for sid in expired:
        _drop_session(sid)
    return len(expired)


def _require_user(token: str | None) -> str:
    """校验凭证，返回 user_id；失败抛 401。"""
    user_id = auth.verify(token)
    if user_id is None:
        observability.set_trace_id(observability.new_trace_id())
        observability.log_event("auth.rejected", reason="invalid_token")
        raise HTTPException(status_code=401, detail="凭证无效，请重新建立会话")
    return user_id


def _serialize(messages: list) -> list[dict]:
    """把 LangChain 消息转成前端能渲染的结构（工具调用与结果不展示）。"""
    out = []
    for m in messages:
        if m.type == "human":
            out.append({"role": "user", "content": m.content or ""})
        elif m.type == "ai" and (m.content or "").strip() and not getattr(m, "tool_calls", None):
            out.append({"role": "assistant", "content": m.content})
    return out


class ChatRequest(BaseModel):
    session_id: str
    message: str


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------- 接口 ----------

@app.post("/auth/token")
@app.post("/session")   # 兼容旧路径
async def auth_token():
    """签发用户凭证。前端把它存 localStorage，之后每个请求放在 X-Session-Token 头。"""
    return {"token": auth.issue()}


@app.get("/sessions")
async def list_sessions(x_session_token: str | None = Header(default=None)):
    user_id = _require_user(x_session_token)
    items = []
    for sid in USER_SESSIONS.get(user_id, []):
        s = SESSIONS.get(sid)
        if not s:
            continue
        human_count = len([m for m in s["messages"] if m.type == "human"])
        # 空会话不展示：这类是"点了新建但还没说话"的，展示出来只会堆积一堆"新对话"
        if human_count == 0:
            continue
        items.append({
            "id": sid,
            "title": s.get("title") or "新对话",
            "message_count": human_count,
            "last_active": int(s.get("last_active", 0)),
        })
    items.sort(key=lambda x: -x["last_active"])
    return {"sessions": items}


@app.post("/sessions")
async def create_session(x_session_token: str | None = Header(default=None)):
    user_id = _require_user(x_session_token)
    # 幂等：已有空会话就复用，避免"连点几次新建就多出几个空对话"
    for sid in USER_SESSIONS.get(user_id, []):
        existing = SESSIONS.get(sid)
        if existing and not existing["messages"]:
            return {"id": sid, "title": existing.get("title") or "新对话", "reused": True}
    if len(USER_SESSIONS.get(user_id, [])) >= MAX_SESSIONS_PER_USER:
        raise HTTPException(status_code=429, detail="会话数量已达上限，请先删除一些")
    session = _new_session(user_id)
    return {"id": session["id"], "title": session["title"] or "新对话", "reused": False}


@app.get("/sessions/{sid}/messages")
async def get_messages(sid: str, x_session_token: str | None = Header(default=None)):
    user_id = _require_user(x_session_token)
    session = _get_owned_session(sid, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"id": sid, "title": session.get("title") or "新对话",
            "messages": _serialize(session["messages"])}


@app.delete("/sessions/{sid}")
async def delete_session(sid: str, x_session_token: str | None = Header(default=None)):
    user_id = _require_user(x_session_token)
    if _get_owned_session(sid, user_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    _drop_session(sid)
    return {"ok": True}


@app.post("/chat")
async def chat(req: ChatRequest, x_session_token: str | None = Header(default=None)):
    user_id = _require_user(x_session_token)
    sid = req.session_id
    session = _get_owned_session(sid, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    observability.set_trace_id(observability.new_trace_id())

    # 首条消息当作会话标题（会话列表里展示）
    if not session.get("title") and not session["messages"]:
        session["title"] = req.message.strip()[:TITLE_MAX_LEN] or "新对话"

    async def gen():
        lock = _get_lock(sid)
        try:
            # 同一会话串行：用户连点两次时，第二个请求排队，而不是两份历史交错写入
            await asyncio.wait_for(lock.acquire(), timeout=SESSION_LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            observability.log_event("session.busy", session_id=sid)
            yield _sse({"type": "error", "text": "上一条消息还在处理中，请稍候再发送。"})
            yield _sse({"type": "done"})
            return

        # 用户消息在拿到锁之后再入历史，避免"没被处理却留在历史里"的孤儿消息
        session["messages"].append(HumanMessage(content=req.message))
        try:
            async for mode, chunk in graph.astream(
                {"messages": list(session["messages"]), "memory": dict(session["memory"])},
                stream_mode=["updates", "messages"],
                config={"recursion_limit": RECURSION_LIMIT},
            ):
                if mode == "messages":
                    msg, meta = chunk
                    node = meta.get("langgraph_node")
                    # 只推 agent 的纯文本 token：不推工具调用参数、不推工具结果 JSON
                    if node == "agent" and not getattr(msg, "tool_call_chunks", None) and msg.content:
                        yield _sse({"type": "stream", "text": msg.content})
                else:  # updates
                    for node, update in chunk.items():
                        if "messages" in update and update["messages"]:
                            session["messages"].extend(update["messages"])
                            last = update["messages"][-1]
                            if node == "agent" and getattr(last, "tool_calls", None):
                                yield _sse({"type": "node", "node": "tool"})
                        if "memory" in update and update["memory"]:
                            session["memory"] = update["memory"]
        except asyncio.CancelledError:
            # 客户端断开（关页面/刷新）时，StreamingResponse 会取消这个生成器。
            # 不要自己调 request.is_disconnected()——会抢 receive 通道导致取消失效。
            observability.log_event("client.disconnected", session_id=sid)
            raise
        except Exception as e:
            observability.log_event("error", error_type=type(e).__name__, detail=str(e)[:300])
            yield _sse({"type": "error", "text": "抱歉，系统处理时出了点问题，请稍后再试。"})
        finally:
            _touch(session)
            lock.release()
        yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "sessions": len(SESSIONS),
        "users": len(USER_SESSIONS),
        "locks": len(SESSION_LOCKS),
        "session_ttl_seconds": SESSION_TTL,
        "web_dir": str(WEB_DIR) if WEB_DIR else None,
    }


# 托管前端静态页（放最后，避免覆盖 API 路由）
if WEB_DIR is not None:
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:
    @app.get("/")
    async def frontend_missing():
        """没构建前端时给一句明确的提示，而不是抛 500。"""
        return JSONResponse(
            {"detail": "前端尚未构建：cd frontend && npm install && npm run build"},
            status_code=503,
        )
