"""SQLite 主存储：graphs / entities / relations / jobs 四表，WAL + 线程安全。

实体与关系均以稳定 ID 为主键，属性/别名/证据以 JSON 列存储，
保持与 open-ikc 现有 DTO 一致（evidence/confidence/status/时间戳）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from ..domain.models import EntityRecord, GraphMeta, RelationRecord
from ..errors import ConflictError, NotFoundError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS graphs (
  graph_id    TEXT PRIMARY KEY,
  name        TEXT NOT NULL DEFAULT '',
  kb_id       TEXT NOT NULL DEFAULT '',
  tenant_id   TEXT NOT NULL DEFAULT '',
  owner_id    TEXT NOT NULL DEFAULT '',
  schema_json TEXT NOT NULL DEFAULT '{}',
  status      TEXT NOT NULL DEFAULT 'active',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entities (
  entity_id       TEXT PRIMARY KEY,
  graph_id        TEXT NOT NULL,
  doc_id          TEXT NOT NULL DEFAULT '',
  entity_type     TEXT NOT NULL,
  name            TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  aliases_json    TEXT NOT NULL DEFAULT '[]',
  evidence_json   TEXT NOT NULL DEFAULT '[]',
  confidence      REAL NOT NULL DEFAULT 1.0,
  status          TEXT NOT NULL DEFAULT 'active',
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_graph ON entities(graph_id, status, entity_type);
CREATE TABLE IF NOT EXISTS relations (
  relation_id       TEXT PRIMARY KEY,
  graph_id          TEXT NOT NULL,
  doc_id            TEXT NOT NULL DEFAULT '',
  relation_type     TEXT NOT NULL,
  source_entity_id  TEXT NOT NULL,
  target_entity_id  TEXT NOT NULL,
  properties_json   TEXT NOT NULL DEFAULT '{}',
  evidence_json     TEXT NOT NULL DEFAULT '[]',
  confidence        REAL NOT NULL DEFAULT 1.0,
  status            TEXT NOT NULL DEFAULT 'active',
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relations_graph ON relations(graph_id, status, relation_type);
CREATE TABLE IF NOT EXISTS jobs (
  job_id       TEXT PRIMARY KEY,
  graph_id     TEXT NOT NULL DEFAULT '',
  task         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  payload_json TEXT NOT NULL DEFAULT '{}',
  result_json  TEXT,
  error        TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
"""


class SqliteGraphStore:
    """SQLite 图存储实现。进程内单连接 + 互斥锁，保证读写原子性。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # ---------- graphs ----------

    def create_graph(self, meta: GraphMeta) -> GraphMeta:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM graphs WHERE graph_id=?", (meta.graph_id,)
            ).fetchone()
            if row:
                raise ConflictError("图谱已存在", field="graphId", reason=f"graphId 冲突：{meta.graph_id}")
            self._conn.execute(
                "INSERT INTO graphs(graph_id, name, kb_id, tenant_id, owner_id, schema_json, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    meta.graph_id,
                    meta.name,
                    meta.kb_id,
                    meta.tenant_id,
                    meta.owner_id,
                    json.dumps(meta.schema, ensure_ascii=False),
                    meta.status,
                    meta.created_at,
                    meta.updated_at,
                ),
            )
            self._conn.commit()
            return meta

    def get_graph(self, graph_id: str) -> GraphMeta | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM graphs WHERE graph_id=?", (graph_id,)).fetchone()
        return self._row_to_graph(row) if row else None

    def get_graph_or_raise(self, graph_id: str) -> GraphMeta:
        meta = self.get_graph(graph_id)
        if meta is None:
            raise NotFoundError("图谱不存在", field="graphId", reason=f"graphId：{graph_id}")
        return meta

    def list_graphs(self, *, tenant_id: str = "", owner_id: str = "") -> list[GraphMeta]:
        sql = "SELECT * FROM graphs"
        conds: list[str] = []
        params: list[str] = []
        if tenant_id:
            conds.append("tenant_id=?")
            params.append(tenant_id)
        if owner_id:
            conds.append("owner_id=?")
            params.append(owner_id)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_graph(row) for row in rows]

    def delete_graph(self, graph_id: str) -> None:
        with self._lock:
            self.get_graph_or_raise(graph_id)
            self._conn.execute("DELETE FROM graphs WHERE graph_id=?", (graph_id,))
            self._conn.execute("DELETE FROM entities WHERE graph_id=?", (graph_id,))
            self._conn.execute("DELETE FROM relations WHERE graph_id=?", (graph_id,))
            self._conn.commit()

    # ---------- entities ----------

    def upsert_entity(self, record: EntityRecord) -> EntityRecord:
        from ..domain.merge import merge_entity

        with self._lock:
            existing = self.get_entity_locked(record.entity_id)
            merged = merge_entity(existing, record)
            self._conn.execute(
                "INSERT INTO entities(entity_id, graph_id, doc_id, entity_type, name, normalized_name,"
                " properties_json, aliases_json, evidence_json, confidence, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(entity_id) DO UPDATE SET"
                " name=excluded.name, properties_json=excluded.properties_json,"
                " aliases_json=excluded.aliases_json, evidence_json=excluded.evidence_json,"
                " confidence=excluded.confidence, status=excluded.status, updated_at=excluded.updated_at",
                (
                    merged.entity_id,
                    merged.graph_id,
                    merged.doc_id,
                    merged.entity_type,
                    merged.name,
                    merged.normalized_name,
                    json.dumps(merged.properties, ensure_ascii=False),
                    json.dumps(merged.aliases, ensure_ascii=False),
                    json.dumps(merged.evidence, ensure_ascii=False),
                    merged.confidence,
                    merged.status,
                    merged.created_at,
                    merged.updated_at,
                ),
            )
            self._conn.commit()
            return merged

    def get_entity_locked(self, entity_id: str) -> EntityRecord | None:
        row = self._conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
        return self._row_to_entity(row) if row else None

    def get_entity(self, entity_id: str) -> EntityRecord | None:
        with self._lock:
            return self.get_entity_locked(entity_id)

    def list_entities(
        self,
        graph_id: str,
        *,
        entity_type: str | None = None,
        name: str | None = None,
        include_deprecated: bool = False,
    ) -> list[EntityRecord]:
        sql = "SELECT * FROM entities WHERE graph_id=?"
        params: list[Any] = [graph_id]
        if not include_deprecated:
            sql += " AND status='active'"
        if entity_type:
            sql += " AND entity_type=?"
            params.append(entity_type)
        if name:
            sql += " AND name LIKE ?"
            params.append(f"%{name}%")
        sql += " ORDER BY entity_type, name"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_entity(row) for row in rows]

    # ---------- relations ----------

    def upsert_relation(self, record: RelationRecord) -> RelationRecord:
        from ..domain.merge import merge_relation

        with self._lock:
            existing = self.get_relation_locked(record.relation_id)
            merged = merge_relation(existing, record)
            self._conn.execute(
                "INSERT INTO relations(relation_id, graph_id, doc_id, relation_type, source_entity_id,"
                " target_entity_id, properties_json, evidence_json, confidence, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(relation_id) DO UPDATE SET"
                " properties_json=excluded.properties_json, evidence_json=excluded.evidence_json,"
                " confidence=excluded.confidence, status=excluded.status, updated_at=excluded.updated_at",
                (
                    merged.relation_id,
                    merged.graph_id,
                    merged.doc_id,
                    merged.relation_type,
                    merged.source_entity_id,
                    merged.target_entity_id,
                    json.dumps(merged.properties, ensure_ascii=False),
                    json.dumps(merged.evidence, ensure_ascii=False),
                    merged.confidence,
                    merged.status,
                    merged.created_at,
                    merged.updated_at,
                ),
            )
            self._conn.commit()
            return merged

    def get_relation_locked(self, relation_id: str) -> RelationRecord | None:
        row = self._conn.execute("SELECT * FROM relations WHERE relation_id=?", (relation_id,)).fetchone()
        return self._row_to_relation(row) if row else None

    def get_relation(self, relation_id: str) -> RelationRecord | None:
        with self._lock:
            return self.get_relation_locked(relation_id)

    def list_relations(
        self,
        graph_id: str,
        *,
        relation_type: str | None = None,
        include_deprecated: bool = False,
    ) -> list[RelationRecord]:
        sql = "SELECT * FROM relations WHERE graph_id=?"
        params: list[Any] = [graph_id]
        if not include_deprecated:
            sql += " AND status='active'"
        if relation_type:
            sql += " AND relation_type=?"
            params.append(relation_type)
        sql += " ORDER BY relation_type, relation_id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_relation(row) for row in rows]

    # ---------- 派生查询 ----------

    def neighbors(
        self, graph_id: str, entity_id: str, depth: int = 1
    ) -> tuple[EntityRecord | None, list[EntityRecord], list[RelationRecord]]:
        """BFS 邻域：返回中心节点、可达节点、覆盖边。"""
        with self._lock:
            center = self.get_entity_locked(entity_id)
            if center is None or center.graph_id != graph_id:
                return None, [], []
            nodes = {
                row["entity_id"]: self._row_to_entity(row)
                for row in self._conn.execute(
                    "SELECT * FROM entities WHERE graph_id=? AND status='active'", (graph_id,)
                ).fetchall()
            }
            edges = [
                self._row_to_relation(row)
                for row in self._conn.execute(
                    "SELECT * FROM relations WHERE graph_id=? AND status='active'", (graph_id,)
                ).fetchall()
            ]
        reachable: dict[str, EntityRecord] = {center.entity_id: center}
        covered: dict[str, RelationRecord] = {}
        frontier = {center.entity_id}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in edges:
                if edge.source_entity_id in frontier:
                    target = nodes.get(edge.target_entity_id)
                    if target is not None:
                        covered[edge.relation_id] = edge
                        next_frontier.add(target.entity_id)
                if edge.target_entity_id in frontier:
                    source = nodes.get(edge.source_entity_id)
                    if source is not None:
                        covered[edge.relation_id] = edge
                        next_frontier.add(source.entity_id)
            for node_id in next_frontier:
                reachable.setdefault(node_id, nodes[node_id])
            frontier = next_frontier
        return center, list(reachable.values()), list(covered.values())

    def paths(
        self, graph_id: str, source_entity_id: str, target_entity_id: str, max_depth: int = 5
    ) -> list[dict[str, Any]]:
        """BFS 最短路径（允许重复出现不同路径，MVP 返回长度最短的路径列表）。"""
        with self._lock:
            nodes = {
                row["entity_id"]: self._row_to_entity(row)
                for row in self._conn.execute(
                    "SELECT * FROM entities WHERE graph_id=? AND status='active'", (graph_id,)
                ).fetchall()
            }
            edges = [
                self._row_to_relation(row)
                for row in self._conn.execute(
                    "SELECT * FROM relations WHERE graph_id=? AND status='active'", (graph_id,)
                ).fetchall()
            ]
        if source_entity_id not in nodes or target_entity_id not in nodes:
            return []
        adjacency: dict[str, list[str]] = {}
        for edge in edges:
            adjacency.setdefault(edge.source_entity_id, []).append(edge.target_entity_id)
            adjacency.setdefault(edge.target_entity_id, []).append(edge.source_entity_id)
        results: list[dict[str, Any]] = []
        best: int | None = None
        stack: list[tuple[str, list[str]]] = [(source_entity_id, [source_entity_id])]
        while stack:
            current, path = stack.pop()
            if current == target_entity_id:
                if best is None or len(path) <= best:
                    best = len(path)
                    results.append(
                        {
                            "entityIds": list(path),
                            "nodeNames": [nodes[n].name for n in path],
                            "length": len(path) - 1,
                        }
                    )
                continue
            if best is not None and len(path) >= best:
                continue
            if len(path) > max_depth:
                continue
            for nxt in reversed(adjacency.get(current, [])):
                if nxt not in path:
                    stack.append((nxt, path + [nxt]))
        results.sort(key=lambda item: item["length"])
        return results[:10]

    def stat(self, graph_id: str) -> dict[str, Any]:
        with self._lock:
            entity_rows = self._conn.execute(
                "SELECT entity_type, COUNT(*) AS cnt FROM entities WHERE graph_id=? AND status='active'"
                " GROUP BY entity_type ORDER BY entity_type",
                (graph_id,),
            ).fetchall()
            relation_rows = self._conn.execute(
                "SELECT relation_type, COUNT(*) AS cnt FROM relations WHERE graph_id=? AND status='active'"
                " GROUP BY relation_type ORDER BY relation_type",
                (graph_id,),
            ).fetchall()
            node_count = sum(int(r["cnt"]) for r in entity_rows)
            edge_count = sum(int(r["cnt"]) for r in relation_rows)
        return {
            "nodeCount": node_count,
            "edgeCount": edge_count,
            "entityTypes": [{"type": r["entity_type"], "count": int(r["cnt"])} for r in entity_rows],
            "relationTypes": [{"type": r["relation_type"], "count": int(r["cnt"])} for r in relation_rows],
        }

    def export_lines(self, graph_id: str, *, include_deprecated: bool = True) -> list[str]:
        lines: list[str] = []
        with self._lock:
            entity_rows = self._conn.execute(
                "SELECT * FROM entities WHERE graph_id=? ORDER BY entity_id", (graph_id,)
            ).fetchall()
            relation_rows = self._conn.execute(
                "SELECT * FROM relations WHERE graph_id=? ORDER BY relation_id", (graph_id,)
            ).fetchall()
        for row in entity_rows:
            rec = self._row_to_entity(row)
            if not include_deprecated and rec.status != "active":
                continue
            lines.append(json.dumps({"kind": "entity", **rec.to_dict()}, ensure_ascii=False))
        for row in relation_rows:
            rec = self._row_to_relation(row)
            if not include_deprecated and rec.status != "active":
                continue
            lines.append(json.dumps({"kind": "relation", **rec.to_dict()}, ensure_ascii=False))
        return lines

    def deprecate_doc_assets(self, graph_id: str, doc_id: str, active_entity_keys: set[str]) -> int:
        """增量废弃：仅由该文档贡献、且本次构建未再出现的节点标记 deprecated（不物理删除）。"""
        deprecated = 0
        now = _now_iso()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM entities WHERE graph_id=? AND status='active'", (graph_id,)
            ).fetchall()
            for row in rows:
                rec = self._row_to_entity(row)
                doc_ids = {str(item.get("docId") or "") for item in rec.evidence}
                if doc_id not in doc_ids:
                    continue
                key = f"{rec.entity_type}:{rec.normalized_name}"
                if key in active_entity_keys:
                    continue
                if doc_ids != {doc_id}:
                    continue
                self._conn.execute(
                    "UPDATE entities SET status='deprecated', updated_at=? WHERE entity_id=?",
                    (now, rec.entity_id),
                )
                deprecated += 1
            self._conn.commit()
        return deprecated

    # ---------- jobs ----------

    def create_job(self, job_id: str, graph_id: str, task: str, payload: dict[str, Any]) -> None:
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs(job_id, graph_id, task, status, payload_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (job_id, graph_id, task, "pending", json.dumps(payload, ensure_ascii=False), now, now),
            )
            self._conn.commit()

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status=?, result_json=?, error=?, updated_at=? WHERE job_id=?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    now,
                    job_id,
                ),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_jobs(self, *, graph_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs"
        params: list[Any] = []
        if graph_id:
            sql += " WHERE graph_id=?"
            params.append(graph_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_job(row) for row in rows]

    # ---------- row mapping ----------

    @staticmethod
    def _row_to_graph(row: sqlite3.Row) -> GraphMeta:
        return GraphMeta(
            graph_id=row["graph_id"],
            name=row["name"],
            kb_id=row["kb_id"],
            tenant_id=row["tenant_id"],
            owner_id=row["owner_id"],
            schema=json.loads(row["schema_json"] or "{}"),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> EntityRecord:
        return EntityRecord(
            entity_id=row["entity_id"],
            graph_id=row["graph_id"],
            doc_id=row["doc_id"],
            entity_type=row["entity_type"],
            name=row["name"],
            normalized_name=row["normalized_name"],
            properties=json.loads(row["properties_json"] or "{}"),
            aliases=json.loads(row["aliases_json"] or "[]"),
            evidence=json.loads(row["evidence_json"] or "[]"),
            confidence=row["confidence"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_relation(row: sqlite3.Row) -> RelationRecord:
        return RelationRecord(
            relation_id=row["relation_id"],
            graph_id=row["graph_id"],
            doc_id=row["doc_id"],
            relation_type=row["relation_type"],
            source_entity_id=row["source_entity_id"],
            target_entity_id=row["target_entity_id"],
            properties=json.loads(row["properties_json"] or "{}"),
            evidence=json.loads(row["evidence_json"] or "[]"),
            confidence=row["confidence"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "jobId": row["job_id"],
            "graphId": row["graph_id"],
            "task": row["task"],
            "status": row["status"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


def create_store(db_path: str | None = None) -> SqliteGraphStore:
    from ..config import Settings

    path = db_path or Settings().resolved_db_path
    return SqliteGraphStore(path)
