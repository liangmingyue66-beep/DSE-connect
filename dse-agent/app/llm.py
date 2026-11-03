"""LLM 封装：Qwen 主 / DeepSeek 备选，可切换、可降级。

why: 模型依赖隔离在系统边界（项目不变量 5）。业务代码只依赖 chat()，
主模型失败自动降级备选，换供应商只改本文件。
"""
import os

from langchain_openai import ChatOpenAI

QWEN_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_BASE = "https://api.deepseek.com"

# 统一超时与重试，避免依赖 SDK 默认值（默认往往=无限等待）
_TIMEOUT = 30
_MAX_RETRIES = 2


def _mk(model: str, base_url: str, api_key: str | None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0,
        timeout=_TIMEOUT,
        max_retries=_MAX_RETRIES,
    )


def get_llm() -> ChatOpenAI:
    """主模型：Qwen qwen-plus（tool calling 稳、便宜）。"""
    return _mk("qwen-plus", QWEN_BASE, os.getenv("DASHSCOPE_API_KEY"))


def get_llm_fast() -> ChatOpenAI:
    """降级档：Qwen qwen-flash（更省钱）。"""
    return _mk("qwen-flash", QWEN_BASE, os.getenv("DASHSCOPE_API_KEY"))


def get_llm_backup() -> ChatOpenAI:
    """备选供应商：DeepSeek deepseek-chat。"""
    return _mk("deepseek-chat", DEEPSEEK_BASE, os.getenv("DEEPSEEK_API_KEY"))


def chat(prompt: str) -> str:
    """调主模型，失败自动降级备选供应商，返回文本。"""
    last_err: Exception | None = None
    for model in (get_llm(), get_llm_backup()):
        try:
            return model.invoke(prompt).content
        except Exception as e:  # 超时 / HTTP / 鉴权错误统一收敛，降级到下一个模型
            last_err = e
    raise RuntimeError(f"主备模型均调用失败: {last_err}")
