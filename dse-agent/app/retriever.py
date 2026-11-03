"""政策语料向量检索器。

设计取舍（面试可讲）：
- 语料仅 3 份 markdown（几十个 chunk），用内存向量 + 余弦相似度即可，
  不引入 FAISS/Chroma 等重型向量库（不过度设计，也避免 Windows 安装坑）。
- embedding 用 DashScope text-embedding-v3（OpenAI 兼容接口），复用已有 key。
- 切片按 '## ' 标题分节（政策条款结构清晰，标题即语义单元）。
"""
import math
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

# 自包含加载 .env（不依赖 import 顺序的副作用），保证能读到 embedding key
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

POLICY_DIR = Path(__file__).resolve().parent.parent / "data" / "policy"
EMBED_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
EMBED_MODEL = "text-embedding-v3"
EMBED_TIMEOUT = 30
EMBED_BATCH_SIZE = 10   # DashScope embedding 单次批量上限（报错实测：should not be larger than 10）
TOP_K = 3


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """批量向量化（OpenAI 兼容接口）。按 EMBED_BATCH_SIZE 分批，规避单次上限。"""
    key = os.getenv("DASHSCOPE_API_KEY")
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i:i + EMBED_BATCH_SIZE]
        resp = httpx.post(
            EMBED_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"model": EMBED_MODEL, "input": batch},
            timeout=EMBED_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        out.extend(d["embedding"] for d in data["data"])
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _split_markdown(text: str) -> list[tuple[str, str]]:
    """按 '## ' 标题切片，返回 [(标题, 正文)]。"""
    chunks, title, lines = [], "", []
    for line in text.splitlines():
        if line.startswith("## "):
            if lines:
                chunks.append((title, "\n".join(lines).strip()))
            title, lines = line[3:].strip(), []
        elif line.strip():
            lines.append(line.strip())
    if lines:
        chunks.append((title, "\n".join(lines).strip()))
    return chunks


class PolicyRetriever:
    """加载一次、缓存向量；search 按余弦相似度取 top-k。"""

    def __init__(self):
        self._items: list[tuple[str, str, str]] = []   # [(标题, 正文, 来源文件)]
        self._vectors: list[list[float]] = []
        self._load()

    def _load(self):
        raw = []   # [(标题, 正文, 来源, 全文)]
        for f in sorted(POLICY_DIR.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            for title, body in _split_markdown(text):
                full = f"{title}\n{body}".strip()
                if full:
                    raw.append((title, body, f.name, full))
        if not raw:
            return
        vectors = _embed_batch([r[3] for r in raw])
        for (title, body, source, _), vec in zip(raw, vectors):
            self._items.append((title, body, source))
            self._vectors.append(vec)

    def search(self, question: str, top_k: int = TOP_K) -> list[dict]:
        qv = _embed_batch([question])[0]
        scored = sorted(
            ((_cosine(qv, v), i) for i, v in enumerate(self._vectors)),
            key=lambda x: -x[0],
        )
        return [
            {"title": self._items[i][0], "text": self._items[i][1],
             "source": self._items[i][2], "score": round(s, 3)}
            for s, i in scored[:top_k]
        ]
