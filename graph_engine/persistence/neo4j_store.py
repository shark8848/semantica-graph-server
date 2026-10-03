"""Neo4j 图存储实现：引擎图数据的**外部图库**后端（缺省后端）。

与 ``SqliteGraphStore`` **同接口、同语义**（稳定 ID 主键、``*_json`` 列、合并规则、
增量废弃、作业台账），只把四类记录落到 Neo4j（Bolt）：

- ``GraphEngineGraph`` / ``GraphEngineEntity`` / ``GraphEngineRelation`` / ``GraphEngineJob``
  四类标签，属性名与 SQLite 列一一对应（``*_json`` 在两边都是 JSON 文本）；
- 记录以**镜像**方式落库（关系也是节点，属性带 ``source_entity_id`` / ``target_entity_id``），
  与 SQLite 行为逐条对齐：不因端点缺失造「幽灵节点」，读面（G-02/G-03）也不会多出记录；
- 派生查询（``neighbors`` / ``paths`` / ``stat`` / ``export_lines``）按图取回活动记录后
  在进程内计算，与 SQLite 实现同口径，避免两套语义漂移。

``neo4j`` 驱动是**守卫式依赖**：驱动缺失 / 连接失败由 :func:`create_store` 降级为 SQLite
（图库不可用不拦引擎，与仓库既有的守卫式降级口径一致）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from ..domain.models import EntityRecord, GraphMeta, RelationRecord
from ..errors import ConflictError, NotFoundError

try:  # 守卫式：驱动缺失时不让 import 失败，由 neo4j_available() 判定
    from neo4j import GraphDatabase

    NEO4J_AVAILABLE = True
except Exception:  # pragma: no cover - 环境相关
    GraphDatabase = None  # type: ignore[assignment]
    NEO4J_AVAILABLE = False

logger = logging.getLogger("graph_engine.persistence.neo4j")

GRAPH_LABEL = "GraphEngineGraph"
ENTITY_LABEL = "GraphEngineEntity"
RELATION_LABEL = "GraphEngineRelation"
JOB_LABEL = "GraphEngineJob"
CONSTRAINT_LABEL = "GraphEngineGraphConstraint"
ONTOLOGY_LABEL = "GraphEngineGraphOntology"

_SCHEMA_STATEMENTS = (
    f"CREATE CONSTRAINT graph_engine_graph_id IF NOT EXISTS FOR (n:{GRAPH_LABEL}) REQUIRE n.graph_id IS UNIQUE",
    f"CREATE CONSTRAINT graph_engine_entity_id IF NOT EXISTS FOR (n:{ENTITY_LABEL}) REQUIRE n.entity_id IS UNIQUE",
    f"CREATE CONSTRAINT graph_engine_relation_id IF NOT EXISTS FOR (n:{RELATION_LABEL}) REQUIRE n.relation_id IS UNIQUE",
    f"CREATE CONSTRAINT graph_engine_job_id IF NOT EXISTS FOR (n:{JOB_LABEL}) REQUIRE n.job_id IS UNIQUE",
    f"CREATE INDEX graph_engine_entity_graph IF NOT EXISTS FOR (n:{ENTITY_LABEL}) ON (n.graph_id, n.status)",
    f"CREATE INDEX graph_engine_relation_graph IF NOT EXISTS FOR (n:{RELATION_LABEL}) ON (n.graph_id, n.status)",
    f"CREATE INDEX graph_engine_job_graph IF NOT EXISTS FOR (n:{JOB_LABEL}) ON (n.graph_id, n.created_at)",
    f"CREATE CONSTRAINT graph_engine_constraint_graph IF NOT EXISTS FOR (n:{CONSTRAINT_LABEL}) REQUIRE n.graph_id IS UNIQUE",
    f"CREATE CONSTRAINT graph_engine_ontology_key IF NOT EXISTS FOR (n:{ONTOLOGY_LABEL}) REQUIRE (n.graph_id, n.ontology_version) IS UNIQUE",
)

_T = TypeVar("_T")


def neo4j_available() -> bool:
    """neo4j 驱动是否可导入（只判依赖，不建连接）。"""
    return NEO4J_AVAILABLE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Neo4jGraphStore:
    """Neo4j 图存储实现：每次调用开一个 session，驱动自身线程安全。"""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "",
        database: str = "neo4j",
        *,
        connection_timeout: float = 5.0,
    ) -> None:
        if not NEO4J_AVAILABLE:  # pragma: no cover - 由 create_store 提前判定
            raise RuntimeError("neo4j 驱动不可用：pip install 'neo4j>=5'（或 semantica[graph-neo4j]）")
        self.uri = uri
        self.database = database or "neo4j"
        self._driver = GraphDatabase.driver(
            uri, auth=(user, password), connection_timeout=connection_timeout
        )
        # 连接失败在此抛出，由工厂降级 SQLite（不静默留下半个实例）
        self._driver.verify_connectivity()
        self._ensure_schema()

    # ---------- 基础设施 ----------

    def _ensure_schema(self) -> None:
        with self._driver.session(database=self.database) as session:
            for statement in _SCHEMA_STATEMENTS:
                session.run(statement).consume()

    def _rows(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session(database=self.database) as session:
            return [dict(record) for record in session.run(cypher, **params)]

    def _one(self, cypher: str, **params: Any) -> dict[str, Any] | None:
        rows = self._rows(cypher, **params)
        return rows[0] if rows else None

    def _write(self, work: Callable[[Any], _T]) -> _T:
        with self._driver.session(database=self.database) as session:
            return session.execute_write(work)

    def close(self) -> None:
        self._driver.close()

    # ---------- graphs ----------

    def create_graph(self, meta: GraphMeta) -> GraphMeta:
        def work(tx: Any) -> None:
            exists = tx.run(
                f"MATCH (n:{GRAPH_LABEL} {{graph_id: $graph_id}}) RETURN n LIMIT 1",
                graph_id=meta.graph_id,
            ).single()
            if exists is not None:
                raise ConflictError("图谱已存在", field="graphId", reason=f"graphId 冲突：{meta.graph_id}")
            tx.run(
                f"CREATE (n:{GRAPH_LABEL} {{graph_id: $graph_id, name: $name, kb_id: $kb_id,"
                " tenant_id: $tenant_id, owner_id: $owner_id, schema_json: $schema_json,"
                " status: $status, created_at: $created_at, updated_at: $updated_at})",
                **self._graph_params(meta),
            )

        self._write(work)
        return meta

    def get_graph(self, graph_id: str) -> GraphMeta | None:
        row = self._one(
            f"MATCH (n:{GRAPH_LABEL} {{graph_id: $graph_id}}) RETURN properties(n) AS props LIMIT 1",
            graph_id=graph_id,
        )
        return self._to_graph(row["props"]) if row else None

    def get_graph_or_raise(self, graph_id: str) -> GraphMeta:
        meta = self.get_graph(graph_id)
        if meta is None:
            raise NotFoundError("图谱不存在", field="graphId", reason=f"graphId：{graph_id}")
        return meta

    def list_graphs(self, *, tenant_id: str = "", owner_id: str = "") -> list[GraphMeta]:
        rows = self._rows(
            f"MATCH (n:{GRAPH_LABEL})"
            " WHERE ($tenant_id = '' OR n.tenant_id = $tenant_id)"
            " AND ($owner_id = '' OR n.owner_id = $owner_id)"
            " RETURN properties(n) AS props ORDER BY n.created_at DESC",
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        return [self._to_graph(row["props"]) for row in rows]

    def delete_graph(self, graph_id: str) -> None:
        def work(tx: Any) -> None:
            exists = tx.run(
                f"MATCH (n:{GRAPH_LABEL} {{graph_id: $graph_id}}) RETURN n LIMIT 1", graph_id=graph_id
            ).single()
            if exists is None:
                raise NotFoundError("图谱不存在", field="graphId", reason=f"graphId：{graph_id}")
            for label in (GRAPH_LABEL, ENTITY_LABEL, RELATION_LABEL, CONSTRAINT_LABEL, ONTOLOGY_LABEL):
                tx.run(f"MATCH (n:{label} {{graph_id: $graph_id}}) DETACH DELETE n", graph_id=graph_id)

        self._write(work)

    # ---------- 抽取约束画像（P4：人工修正沉淀，按图持久化） ----------

    def get_constraints(self, graph_id: str) -> dict[str, Any]:
        row = self._one(
            f"MATCH (n:{CONSTRAINT_LABEL} {{graph_id: $graph_id}})"
            " RETURN properties(n) AS props LIMIT 1",
            graph_id=graph_id,
        )
        if not row:
            return {}
        raw = (row.get("props") or {}).get("constraints_json") or "{}"
        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001 - 坏 JSON 视作无画像
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def put_constraints(self, graph_id: str, constraints: dict[str, Any]) -> dict[str, Any]:
        payload = dict(constraints or {})
        self.get_graph_or_raise(graph_id)

        def work(tx: Any) -> None:
            tx.run(
                f"MERGE (n:{CONSTRAINT_LABEL} {{graph_id: $graph_id}})"
                " SET n.constraints_json = $constraints_json, n.updated_at = $updated_at",
                graph_id=graph_id,
                constraints_json=json.dumps(payload, ensure_ascii=False),
                updated_at=_now_iso(),
            )

        self._write(work)
        return payload

    # ---------- 编译产物快照（本体面；D1：只缓存产物，不存定义） ----------

    def put_ontology(
        self, graph_id: str, *, ontology_id: str, ontology_version: int, product: dict[str, Any]
    ) -> dict[str, Any]:
        version = int(ontology_version)
        payload = dict(product or {})
        now = _now_iso()
        self.get_graph_or_raise(graph_id)

        def work(tx: Any) -> None:
            tx.run(
                f"MERGE (n:{ONTOLOGY_LABEL} {{graph_id: $graph_id, ontology_version: $ontology_version}})"
                " SET n.ontology_id = $ontology_id, n.product_json = $product_json, n.created_at = $created_at",
                graph_id=graph_id,
                ontology_version=version,
                ontology_id=str(ontology_id or ""),
                product_json=json.dumps(payload, ensure_ascii=False),
                created_at=now,
            )

        self._write(work)
        return {
            "graphId": graph_id,
            "ontologyId": str(ontology_id or ""),
            "ontologyVersion": version,
            "graphSchema": payload,
            "createdAt": now,
        }

    def get_ontology(self, graph_id: str, ontology_version: int) -> dict[str, Any] | None:
        row = self._one(
            f"MATCH (n:{ONTOLOGY_LABEL} {{graph_id: $graph_id, ontology_version: $ontology_version}})"
            " RETURN properties(n) AS props LIMIT 1",
            graph_id=graph_id,
            ontology_version=int(ontology_version),
        )
        if not row:
            return None
        return self._to_ontology(row.get("props") or {})

    def list_ontologies(self, graph_id: str) -> list[dict[str, Any]]:
        rows = self._rows(
            f"MATCH (n:{ONTOLOGY_LABEL} {{graph_id: $graph_id}})"
            " RETURN properties(n) AS props ORDER BY n.ontology_version DESC",
            graph_id=graph_id,
        )
        return [self._to_ontology(row.get("props") or {}) for row in rows]

    @staticmethod
    def _to_ontology(props: dict[str, Any]) -> dict[str, Any]:
        try:
            product = json.loads(props.get("product_json") or "{}")
        except Exception:  # noqa: BLE001 - 坏 JSON 视作空产物
            product = {}
        return {
            "graphId": str(props.get("graph_id") or ""),
            "ontologyId": str(props.get("ontology_id") or ""),
            "ontologyVersion": int(props.get("ontology_version") or 0),
            "graphSchema": dict(product) if isinstance(product, dict) else {},
            "createdAt": str(props.get("created_at") or ""),
        }

    # ---------- entities ----------

    def upsert_entity(self, record: EntityRecord) -> EntityRecord:
        from ..domain.merge import merge_entity

        def work(tx: Any) -> EntityRecord:
            existing = self._entity_props(tx, record.entity_id)
            merged = merge_entity(existing, record)
            tx.run(
                f"MERGE (n:{ENTITY_LABEL} {{entity_id: $entity_id}})"
                " SET n.graph_id = $graph_id, n.doc_id = $doc_id, n.entity_type = $entity_type,"
                " n.name = $name, n.normalized_name = $normalized_name,"
                " n.properties_json = $properties_json, n.aliases_json = $aliases_json,"
                " n.evidence_json = $evidence_json, n.confidence = $confidence,"
                " n.status = $status, n.created_at = $created_at, n.updated_at = $updated_at",
                **self._entity_params(merged),
            )
            return merged

        return self._write(work)

    def get_entity(self, entity_id: str) -> EntityRecord | None:
        row = self._one(
            f"MATCH (n:{ENTITY_LABEL} {{entity_id: $entity_id}}) RETURN properties(n) AS props LIMIT 1",
            entity_id=entity_id,
        )
        return self._to_entity(row["props"]) if row else None

    def list_entities(
        self,
        graph_id: str,
        *,
        entity_type: str | None = None,
        name: str | None = None,
        include_deprecated: bool = False,
    ) -> list[EntityRecord]:
        rows = self._rows(
            f"MATCH (n:{ENTITY_LABEL} {{graph_id: $graph_id}})"
            " WHERE ($include_deprecated OR n.status = 'active')"
            " AND ($entity_type IS NULL OR n.entity_type = $entity_type)"
            " AND ($name IS NULL OR n.name CONTAINS $name)"
            " RETURN properties(n) AS props ORDER BY n.entity_type, n.name",
            graph_id=graph_id,
            include_deprecated=bool(include_deprecated),
            entity_type=entity_type,
            name=name,
        )
        return [self._to_entity(row["props"]) for row in rows]

    # ---------- relations ----------

    def upsert_relation(self, record: RelationRecord) -> RelationRecord:
        from ..domain.merge import merge_relation

        def work(tx: Any) -> RelationRecord:
            existing = self._relation_props(tx, record.relation_id)
            merged = merge_relation(existing, record)
            tx.run(
                f"MERGE (n:{RELATION_LABEL} {{relation_id: $relation_id}})"
                " SET n.graph_id = $graph_id, n.doc_id = $doc_id, n.relation_type = $relation_type,"
                " n.source_entity_id = $source_entity_id, n.target_entity_id = $target_entity_id,"
                " n.properties_json = $properties_json, n.evidence_json = $evidence_json,"
                " n.confidence = $confidence, n.status = $status,"
                " n.created_at = $created_at, n.updated_at = $updated_at",
                **self._relation_params(merged),
            )
            return merged

        return self._write(work)

    def get_relation(self, relation_id: str) -> RelationRecord | None:
        row = self._one(
            f"MATCH (n:{RELATION_LABEL} {{relation_id: $relation_id}}) RETURN properties(n) AS props LIMIT 1",
            relation_id=relation_id,
        )
        return self._to_relation(row["props"]) if row else None

    def list_relations(
        self,
        graph_id: str,
        *,
        relation_type: str | None = None,
        include_deprecated: bool = False,
    ) -> list[RelationRecord]:
        rows = self._rows(
            f"MATCH (n:{RELATION_LABEL} {{graph_id: $graph_id}})"
            " WHERE ($include_deprecated OR n.status = 'active')"
            " AND ($relation_type IS NULL OR n.relation_type = $relation_type)"
            " RETURN properties(n) AS props ORDER BY n.relation_type, n.relation_id",
            graph_id=graph_id,
            include_deprecated=bool(include_deprecated),
            relation_type=relation_type,
        )
        return [self._to_relation(row["props"]) for row in rows]

    # ---------- 派生查询（与 SqliteGraphStore 同口径） ----------

    def neighbors(
        self, graph_id: str, entity_id: str, depth: int = 1
    ) -> tuple[EntityRecord | None, list[EntityRecord], list[RelationRecord]]:
        """BFS 邻域：返回中心节点、可达节点、覆盖边。"""
        center = self.get_entity(entity_id)
        if center is None or center.graph_id != graph_id:
            return None, [], []
        nodes = {item.entity_id: item for item in self.list_entities(graph_id)}
        edges = self.list_relations(graph_id)
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
        """BFS 最短路径（与 SQLite 实现同口径：只回长度最短的前 10 条）。"""
        nodes = {item.entity_id: item for item in self.list_entities(graph_id)}
        edges = self.list_relations(graph_id)
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
        entity_counts: dict[str, int] = {}
        for item in self.list_entities(graph_id):
            entity_counts[item.entity_type] = entity_counts.get(item.entity_type, 0) + 1
        relation_counts: dict[str, int] = {}
        for item in self.list_relations(graph_id):
            relation_counts[item.relation_type] = relation_counts.get(item.relation_type, 0) + 1
        return {
            "nodeCount": sum(entity_counts.values()),
            "edgeCount": sum(relation_counts.values()),
            "entityTypes": [
                {"type": key, "count": entity_counts[key]} for key in sorted(entity_counts)
            ],
            "relationTypes": [
                {"type": key, "count": relation_counts[key]} for key in sorted(relation_counts)
            ],
        }

    def export_lines(self, graph_id: str, *, include_deprecated: bool = True) -> list[str]:
        lines: list[str] = []
        for record in self.list_entities(graph_id, include_deprecated=include_deprecated):
            lines.append(json.dumps({"kind": "entity", **record.to_dict()}, ensure_ascii=False))
        for record in self.list_relations(graph_id, include_deprecated=include_deprecated):
            lines.append(json.dumps({"kind": "relation", **record.to_dict()}, ensure_ascii=False))
        return lines

    def deprecate_doc_assets(self, graph_id: str, doc_id: str, active_entity_keys: set[str]) -> int:
        """增量废弃：仅由该文档贡献、且本次构建未再出现的节点标记 deprecated（不物理删除）。"""
        now = _now_iso()
        pending: list[str] = []
        for record in self.list_entities(graph_id):
            doc_ids = {str(item.get("docId") or "") for item in record.evidence}
            if doc_id not in doc_ids:
                continue
            key = f"{record.entity_type}:{record.normalized_name}"
            if key in active_entity_keys:
                continue
            if doc_ids != {doc_id}:
                continue
            pending.append(record.entity_id)
        if not pending:
            return 0

        def work(tx: Any) -> int:
            result = tx.run(
                f"UNWIND $entity_ids AS entity_id MATCH (n:{ENTITY_LABEL} {{entity_id: entity_id}}) "
                "SET n.status = 'deprecated', n.updated_at = $now RETURN count(n) AS affected",
                entity_ids=pending,
                now=now,
            ).single()
            return int(result["affected"]) if result else 0

        return self._write(work)

    # ---------- jobs ----------

    def create_job(self, job_id: str, graph_id: str, task: str, payload: dict[str, Any]) -> None:
        now = _now_iso()

        def work(tx: Any) -> None:
            tx.run(
                f"CREATE (n:{JOB_LABEL} {{job_id: $job_id, graph_id: $graph_id, task: $task,"
                " status: 'pending', payload_json: $payload_json, result_json: null,"
                " error: null, created_at: $created_at, updated_at: $updated_at})",
                job_id=job_id,
                graph_id=graph_id,
                task=task,
                payload_json=json.dumps(payload, ensure_ascii=False),
                created_at=now,
                updated_at=now,
            )

        self._write(work)

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        self._write(
            lambda tx: tx.run(
                f"MATCH (n:{JOB_LABEL} {{job_id: $job_id}}) SET n.status = $status,"
                " n.result_json = $result_json, n.error = $error, n.updated_at = $updated_at",
                job_id=job_id,
                status=status,
                result_json=json.dumps(result, ensure_ascii=False) if result is not None else None,
                error=error,
                updated_at=_now_iso(),
            ).consume()
        )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._one(
            f"MATCH (n:{JOB_LABEL} {{job_id: $job_id}}) RETURN properties(n) AS props LIMIT 1",
            job_id=job_id,
        )
        return self._to_job(row["props"]) if row else None

    def list_jobs(self, *, graph_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        rows = self._rows(
            f"MATCH (n:{JOB_LABEL}) WHERE ($graph_id = '' OR n.graph_id = $graph_id)"
            " RETURN properties(n) AS props ORDER BY n.created_at DESC LIMIT $limit",
            graph_id=graph_id,
            limit=int(limit),
        )
        return [self._to_job(row["props"]) for row in rows]

    # ---------- 事务内读（upsert 合并用） ----------

    def _entity_props(self, tx: Any, entity_id: str) -> EntityRecord | None:
        row = tx.run(
            f"MATCH (n:{ENTITY_LABEL} {{entity_id: $entity_id}}) RETURN properties(n) AS props",
            entity_id=entity_id,
        ).single()
        return self._to_entity(row["props"]) if row else None

    def _relation_props(self, tx: Any, relation_id: str) -> RelationRecord | None:
        row = tx.run(
            f"MATCH (n:{RELATION_LABEL} {{relation_id: $relation_id}}) RETURN properties(n) AS props",
            relation_id=relation_id,
        ).single()
        return self._to_relation(row["props"]) if row else None

    # ---------- 参数与映射 ----------

    @staticmethod
    def _graph_params(meta: GraphMeta) -> dict[str, Any]:
        return {
            "graph_id": meta.graph_id,
            "name": meta.name,
            "kb_id": meta.kb_id,
            "tenant_id": meta.tenant_id,
            "owner_id": meta.owner_id,
            "schema_json": json.dumps(meta.schema, ensure_ascii=False),
            "status": meta.status,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
        }

    @staticmethod
    def _entity_params(record: EntityRecord) -> dict[str, Any]:
        return {
            "entity_id": record.entity_id,
            "graph_id": record.graph_id,
            "doc_id": record.doc_id,
            "entity_type": record.entity_type,
            "name": record.name,
            "normalized_name": record.normalized_name,
            "properties_json": json.dumps(record.properties, ensure_ascii=False),
            "aliases_json": json.dumps(record.aliases, ensure_ascii=False),
            "evidence_json": json.dumps(record.evidence, ensure_ascii=False),
            "confidence": float(record.confidence),
            "status": record.status,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @staticmethod
    def _relation_params(record: RelationRecord) -> dict[str, Any]:
        return {
            "relation_id": record.relation_id,
            "graph_id": record.graph_id,
            "doc_id": record.doc_id,
            "relation_type": record.relation_type,
            "source_entity_id": record.source_entity_id,
            "target_entity_id": record.target_entity_id,
            "properties_json": json.dumps(record.properties, ensure_ascii=False),
            "evidence_json": json.dumps(record.evidence, ensure_ascii=False),
            "confidence": float(record.confidence),
            "status": record.status,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @staticmethod
    def _to_graph(props: dict[str, Any]) -> GraphMeta:
        return GraphMeta(
            graph_id=str(props.get("graph_id") or ""),
            name=str(props.get("name") or ""),
            kb_id=str(props.get("kb_id") or ""),
            tenant_id=str(props.get("tenant_id") or ""),
            owner_id=str(props.get("owner_id") or ""),
            schema=json.loads(props.get("schema_json") or "{}"),
            status=str(props.get("status") or "active"),
            created_at=str(props.get("created_at") or ""),
            updated_at=str(props.get("updated_at") or ""),
        )

    @staticmethod
    def _to_entity(props: dict[str, Any]) -> EntityRecord:
        return EntityRecord(
            entity_id=str(props.get("entity_id") or ""),
            graph_id=str(props.get("graph_id") or ""),
            doc_id=str(props.get("doc_id") or ""),
            entity_type=str(props.get("entity_type") or ""),
            name=str(props.get("name") or ""),
            normalized_name=str(props.get("normalized_name") or ""),
            properties=json.loads(props.get("properties_json") or "{}"),
            aliases=json.loads(props.get("aliases_json") or "[]"),
            evidence=json.loads(props.get("evidence_json") or "[]"),
            confidence=float(props.get("confidence") if props.get("confidence") is not None else 1.0),
            status=str(props.get("status") or "active"),
            created_at=str(props.get("created_at") or ""),
            updated_at=str(props.get("updated_at") or ""),
        )

    @staticmethod
    def _to_relation(props: dict[str, Any]) -> RelationRecord:
        return RelationRecord(
            relation_id=str(props.get("relation_id") or ""),
            graph_id=str(props.get("graph_id") or ""),
            doc_id=str(props.get("doc_id") or ""),
            relation_type=str(props.get("relation_type") or ""),
            source_entity_id=str(props.get("source_entity_id") or ""),
            target_entity_id=str(props.get("target_entity_id") or ""),
            properties=json.loads(props.get("properties_json") or "{}"),
            evidence=json.loads(props.get("evidence_json") or "[]"),
            confidence=float(props.get("confidence") if props.get("confidence") is not None else 1.0),
            status=str(props.get("status") or "active"),
            created_at=str(props.get("created_at") or ""),
            updated_at=str(props.get("updated_at") or ""),
        )

    @staticmethod
    def _to_job(props: dict[str, Any]) -> dict[str, Any]:
        return {
            "jobId": props.get("job_id"),
            "graphId": props.get("graph_id"),
            "task": props.get("task"),
            "status": props.get("status"),
            "payload": json.loads(props.get("payload_json") or "{}"),
            "result": json.loads(props["result_json"]) if props.get("result_json") else None,
            "error": props.get("error"),
            "createdAt": props.get("created_at"),
            "updatedAt": props.get("updated_at"),
        }
