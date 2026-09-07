"""semantica 检索适配层：检索链守卫探测 + 可注入检索后端契约。

真实语义检索依赖 ``semantica.vector_store / semantica.context / semantica.embeddings``，
当前被 venv 内 pinecone 改名桩（抛裸 Exception）阻塞。本模块负责：
- 守卫式导入探测（捕获 Exception，视为「检索链不可用」而非崩溃）；
- 检索后端契约（backend 可注入，按 graphId 作命名空间隔离）；真实 semantica
  VectorStore 后端留待依赖修复后的后续任务接入，本模块不触发真实索引/迁移；
- 检索链不可用 / 后端未配置 / 执行异常时返回确定的降级结果（semantica:false、hits:[]）。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("graph_engine.retrieval")

#: 进程内探测结果缓存：semantica 检索链导入状态进程内不变，且失败导入开销大
#: （vector_store 会先拉起 faiss/onnxruntime 等重依赖才在 pinecone 桩处失败），只真实探测一次。
_chain_available: bool | None = None

_RETRIEVAL_MODULES = ("semantica.vector_store", "semantica.context", "semantica.embeddings")


def retrieval_available() -> bool:
    """semantica 检索链（vector_store/context/embeddings）是否可导入。

    守卫捕获一切 Exception：venv 内 pinecone 改名桩抛的是裸 Exception，连
    semantica 自身的 ``except (ImportError, OSError)`` 都守不住，这里一律按
    「检索链不可用」处理，不抛错、不崩溃。
    """
    global _chain_available
    if _chain_available is not None:
        return _chain_available
    ok = True
    for module_name in _RETRIEVAL_MODULES:
        try:
            __import__(module_name)
        except Exception as exc:  # 桩包抛裸 Exception，只能全量捕获
            logger.warning("semantica 检索链不可用（%s）：%s", module_name, exc)
            ok = False
            break
    _chain_available = ok
    return ok


class RetrievalBackend(Protocol):
    """检索后端契约（按 graphId 作命名空间维度隔离，可注入）。

    ``upsert`` 的向量条目结构见 :func:`record_to_doc`；``query`` 返回的原始命中
    需含 ``id``/``score`` 与 ``metadata``（type/docId/confidence/evidence 等）。
    删除/废弃与合并后的索引同步由 service 层调用方显式控制，后端自身不做迁移。
    """

    def upsert(self, graph_id_value: str, records: list[dict[str, Any]]) -> int:
        """写入向量条目（records 元素为 record_to_doc 输出），返回成功条数。"""
        ...

    def query(self, graph_id_value: str, text: str, top_k: int) -> list[dict[str, Any]]:
        """语义查询（text 为自然语言查询文本），返回原始命中列表。"""
        ...

    def delete(self, graph_id_value: str, record_ids: list[str]) -> int:
        """删除命名空间下记录（按稳定 ID），返回删除条数。"""
        ...


def record_to_doc(record: Any) -> dict[str, Any]:
    """EntityRecord/RelationRecord → 待索引向量条目。

    ``id`` 锚定稳定 ID（entity_id/relation_id，见 domain/ids.py）；metadata 携带
    稳定 ID、kind、type、docId、confidence、evidence 等字段，对齐 open-ikc 语义
    （供后端 metadata 过滤与命中展示）。docId 废弃/记录合并的向量同步由调用方控制。
    """
    if hasattr(record, "entity_id"):
        stable_id = str(record.entity_id)
        kind = "entity"
        rec_type = str(record.entity_type)
        text = str(record.name or record.normalized_name)
    else:
        stable_id = str(record.relation_id)
        kind = "relation"
        rec_type = str(record.relation_type)
        text = f"{record.source_entity_id} {rec_type} {record.target_entity_id}"
    return {
        "id": stable_id,
        "kind": kind,
        "text": text,
        "metadata": {
            "id": stable_id,
            "kind": kind,
            "type": rec_type,
            "graphId": str(record.graph_id),
            "docId": str(record.doc_id or ""),
            "name": text,
            "confidence": float(record.confidence),
            "evidence": [dict(item) for item in record.evidence],
        },
    }


def _degraded(reason: str) -> dict[str, Any]:
    """确定的降级检索结果（semantica:false + hits:[]）。"""
    return {"semantica": False, "reason": reason, "total": 0, "hits": []}


def _hit(raw: dict[str, Any]) -> dict[str, Any]:
    """原始命中 → 对外命中：稳定 ID + type/docId/score/evidence 等。"""
    metadata = dict(raw.get("metadata") or {})
    return {
        "id": str(raw.get("id") or metadata.get("id") or ""),
        "kind": str(metadata.get("kind") or "record"),
        "type": str(metadata.get("type") or ""),
        "name": str(metadata.get("name") or ""),
        "docId": str(metadata.get("docId") or ""),
        "score": float(raw.get("score") or 0.0),
        "confidence": float(metadata.get("confidence") or 0.0),
        "evidence": [dict(item) for item in (metadata.get("evidence") or []) if isinstance(item, dict)],
    }


class RetrievalAdapter:
    """语义检索适配器：可用状态 + 可注入后端。

    - backend 为 None 时，可用性来自 retrieval_available() 对真实 semantica 检索链的探测；
    - 显式注入 backend 视为检索链已启用（供单测 fake 与后续真实 VectorStore 后端接入）。
    检索不可用 / 后端未配置 / 后端执行异常时统一返回确定降级结果，不抛错。
    """

    def __init__(self, backend: RetrievalBackend | None = None) -> None:
        self.backend = backend
        self.available = backend is not None or retrieval_available()

    def search(self, graph_id_value: str, query: str, top_k: int) -> dict[str, Any]:
        """语义检索：可用且已配置后端时返回命中列表，否则确定降级。"""
        if not self.available:
            return _degraded("semantica 检索链不可用")
        if self.backend is None:
            return _degraded("检索后端未配置")
        try:
            raw_hits = self.backend.query(graph_id_value, query, top_k)
            hits = [_hit(item) for item in raw_hits]
        except Exception as exc:
            logger.warning("语义检索执行失败：%s", exc)
            return _degraded(f"检索执行失败：{exc}")
        return {"semantica": True, "total": len(hits), "hits": hits}

    def status(self) -> dict[str, Any]:
        """语义检索/索引可用状态（供运维与调试，与 search 同规则降级）。"""
        if not self.available:
            return {"semantica": False, "reason": "semantica 检索链不可用"}
        if self.backend is None:
            return {"semantica": False, "reason": "检索后端未配置"}
        return {"semantica": True, "backend": type(self.backend).__name__}
