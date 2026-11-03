"""会话凭证（HMAC 签名）的单元测试。"""
from app import auth


def test_issue_format_and_verify():
    token = auth.issue()
    assert token.count(".") == 1
    sid = auth.verify(token)
    assert sid and len(sid) == 32


def test_two_sessions_differ():
    assert auth.verify(auth.issue()) != auth.verify(auth.issue())


def test_tampered_signature_rejected():
    token = auth.issue()
    sid, sig = token.rsplit(".", 1)
    fake = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    assert auth.verify(f"{sid}.{fake}") is None


def test_tampered_sid_rejected():
    """改 sid 但不改签名 → 必须拒绝（防"换成别人的会话"）。"""
    token = auth.issue()
    _, sig = token.rsplit(".", 1)
    assert auth.verify(f"{'a' * 32}.{sig}") is None


def test_legacy_plain_id_rejected():
    """旧的"前端自己生成 id"格式必须被拒。"""
    assert auth.verify("s1abc2def") is None


def test_empty_and_none_rejected():
    assert auth.verify("") is None
    assert auth.verify(None) is None
    assert auth.verify(".") is None
