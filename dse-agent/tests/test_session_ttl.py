"""会话管理（多用户 / 多会话 / TTL / 并发锁）的单元测试，不启动服务、不调模型。"""
import asyncio
import time

from app import server


def setup_function():
    server.SESSIONS.clear()
    server.USER_SESSIONS.clear()
    server.SESSION_LOCKS.clear()


def test_new_session_registers_owner_and_index():
    created = server._new_session("user-a", "第一个会话")
    sid = created["id"]

    assert sid in server.SESSIONS
    assert server.SESSIONS[sid]["owner"] == "user-a"
    assert server.USER_SESSIONS["user-a"] == [sid]


def test_owned_session_rejects_other_user():
    sid = server._new_session("user-a")["id"]

    assert server._get_owned_session(sid, "user-a") is not None
    # 别人的会话一律当作不存在（防越权访问）
    assert server._get_owned_session(sid, "user-b") is None
    assert server._get_owned_session("not-exist", "user-a") is None


def test_sweep_removes_expired_and_cleans_index(monkeypatch):
    monkeypatch.setattr(server, "SESSION_TTL", 50)
    now = time.time()
    old = server._new_session("user-a")["id"]
    fresh = server._new_session("user-a")["id"]
    server.SESSIONS[old]["last_active"] = now - 100

    removed = server.sweep_sessions(now=now)

    assert removed == 1
    assert old not in server.SESSIONS
    assert old not in server.USER_SESSIONS["user-a"]     # 索引同步清理
    assert fresh in server.SESSIONS


def test_sweep_keeps_everything_when_all_fresh(monkeypatch):
    monkeypatch.setattr(server, "SESSION_TTL", 3600)
    now = time.time()
    for _ in range(3):
        server._new_session("user-a")

    assert server.sweep_sessions(now=now) == 0
    assert len(server.SESSIONS) == 3


def test_drop_session_cleans_everything():
    sid = server._new_session("user-a")["id"]
    server._get_lock(sid)

    server._drop_session(sid)

    assert sid not in server.SESSIONS
    assert server.USER_SESSIONS.get("user-a", []) == []
    assert sid not in server.SESSION_LOCKS


def test_same_session_lock_is_shared():
    assert server._get_lock("x") is server._get_lock("x")


def test_lock_blocks_concurrent_runner():
    """同一会话的请求必须串行，不能交错。"""
    order = []

    async def worker(tag, delay):
        async with server._get_lock("s"):
            order.append(f"{tag}-start")
            await asyncio.sleep(delay)
            order.append(f"{tag}-end")

    async def main():
        await asyncio.gather(worker("A", 0.05), worker("B", 0.01))

    asyncio.run(main())
    assert order == ["A-start", "A-end", "B-start", "B-end"]
