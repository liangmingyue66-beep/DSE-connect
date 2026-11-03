"""会话凭证：服务端签发 + HMAC 签名。

解决什么问题：原来 session_id 由前端自己生成（`s<时间戳><4位随机>`），
别人只要猜到/看到就能读写那个会话（会话劫持）。

做法（不引入登录系统的前提下，最小可行）：
- 服务端签发 `<sid>.<signature>`：sid 是 128 bit 随机（不可猜），signature = HMAC-SHA256(secret, sid)
- 客户端只当作一个不透明字符串保存和回传
- 服务端用 `hmac.compare_digest` 校验（防时序攻击），不合法直接 401
- 无状态校验：不用在服务端存 token 表

边界（诚实说清）：
- 防「伪造/猜测 session_id」✔
- 不防「凭证被完整窃取」（那需要登录 + 短期 token + 绑设备）
"""
import hashlib
import hmac
import os
import secrets

# 生产环境应从环境变量注入；未配置时随机生成（重启后旧凭证失效，
# 与"会话存内存、重启即丢"的行为一致，不会引入新的不一致）
_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
_SIG_LEN = 32
_SEP = "."


def _sign(sid: str) -> str:
    return hmac.new(_SECRET.encode(), sid.encode(), hashlib.sha256).hexdigest()[:_SIG_LEN]


def issue() -> str:
    """签发新凭证：<32位十六进制随机>.<32位签名>。"""
    sid = secrets.token_hex(16)   # 128 bit
    return f"{sid}{_SEP}{_sign(sid)}"


def verify(raw: str | None) -> str | None:
    """校验凭证，合法返回纯净 session_id；否则返回 None。"""
    if not raw or _SEP not in raw:
        return None
    sid, sig = raw.rsplit(_SEP, 1)
    if not sid or not sig:
        return None
    # compare_digest：常量时间比较，避免通过响应时间逐字节猜签名
    return sid if hmac.compare_digest(sig, _sign(sid)) else None
