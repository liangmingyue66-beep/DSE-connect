"""轻量可观测：trace_id + 结构化日志 + 关键指标。

不引入 OpenTelemetry/LangSmith 等重型框架，用一行 JSON 结构化日志，
足够本地定位问题；生产可再升级。
"""
import contextvars
import json
import uuid

# 当前请求的 trace_id（contextvar：同一线程内透传，不污染全局）
_current_trace = contextvars.ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    return str(uuid.uuid4())


def set_trace_id(trace_id: str) -> None:
    _current_trace.set(trace_id)


def get_trace_id() -> str:
    return _current_trace.get()


def log_event(span: str, **fields) -> None:
    """输出一行结构化 JSON 日志（便于 grep / 后续接日志系统）。"""
    record = {"trace_id": get_trace_id()[:8], "span": span, **fields}
    print(json.dumps(record, ensure_ascii=False, default=str), flush=True)
