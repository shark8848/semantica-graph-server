# 方案：OxigraphStore RDF/SPARQL 视图 + LLM 建图增强

> 对应 `docs/解决方案.md` §7「后续演进」条目落地（2026-09-07，MVP 阶段）。
> 状态：核心能力 + 五面接口 + 单测已落地（`pytest tests` 36 passed）；
> 真实 LLM provider 出网增强、镜像内 `pyoxigraph` 依赖确认列为待人工/后续项。

## 1. 目标

在现有 SQLite 稳定 ID 记录之上提供两个**可选增强**，均不影响无 semantica/无
外网的核心功能（守卫式导入 + 确定降级，与 `adapters/retrieval.py` 同风格）：

1. **RDF/SPARQL 视图**：以 `semantica.triplet_store.oxigraph_store.OxigraphStore`
   为后端，把当前图的活动记录（EntityRecord/RelationRecord）实时映射为 RDF 三元组，
   提供 SPARQL 查询与 RDF 序列化导出（只读补强，SQLite 仍是事实源）。
2. **LLM 建图增强**：文本建图候选实体（规则抽取）可选地经 semantica
   `semantic_extract.LLMExtraction`（实际类名，支持 openai/gemini/groq/anthropic/
   ollama/huggingface_llm）做实体级增强（去噪/纠名/置信度），默认关闭。

## 2. RDF 词汇表（稳定 ID → 稳定 IRI）

- 实体 IRI：`urn:ge:entity:{graph_id}:{entity_id}`（分段经 IRI 百分号编码）
- 关系 IRI：`urn:ge:relation:{graph_id}:{relation_id}`
- 谓词命名空间：`urn:ge:kg#`
  - `type` 用 `rdf:type`；实体类型 `kg:Entity` / 关系类型 `kg:Relation`；
    名称 `rdfs:label`（外加 `kg:name` / `kg:entityType` / `kg:relationType`）；
  - 语义字段：`kg:docId`、`kg:confidence`（xsd:double）、`kg:alias`、
    `kg:source` / `kg:target`（指向实体 IRI）、`kg:property/{key}`、
    `kg:evidence`（JSON 字面量）；
- 属性值类型映射：bool/int/float → xsd 类型字面量，其余（dict/list）→ JSON 字符串字面量。

IRI 由稳定 ID 派生、跨重建稳定，与 `docs/解决方案.md` 稳定 ID 语义一致。

## 3. 五面接口

- **HTTP**：`GET /api/v1/graph/graphs/{id}/export?format=turtle|nt|nq|rdfxml`
  （原有 jsonl/json 不变）；`POST /api/v1/graph/graphs/{id}/sparql`
  body `{"query": "...", "limit": 0}`（SELECT/ASK/CONSTRUCT/DESCRIBE）。
- **gRPC**：`Sparql` RPC（Envelope，`proto/graph/v1/graph.proto` 已同步）；
  `ExportGraph` 的 `format` 支持上述 RDF 格式。
- **MCP**：工具 `graph_export`（format 扩展）+ 新增 `graph_sparql`。
- **CLI**：`graph-engine export --format turtle` + `graph-engine sparql <graph_id> --query "..."`。
- **Celery**：`export` job 的 `format` 直通 RDF 格式；SPARQL 属同步查询不投 broker。

错误语义：RDF/SPARQL 依赖缺失、SPARQL 空查询/语法错误均收敛为 `200001`（field
`format`/`query`）；图不存在仍 `200404`。

## 4. LLM 增强配置与降级

- 入口：五面 build 接口透传 `llm`（HTTP `payload.llm`、gRPC/MCP `llm`、CLI `--llm`、
  Celery `build_text` job `payload.llm`）；缺省读环境变量 `GRAPH_ENGINE_LLM_ENHANCE`
  （默认关闭）。
- provider：`GRAPH_ENGINE_LLM_PROVIDER`（如 `openai`），可选
  `GRAPH_ENGINE_LLM_MODEL` / `GRAPH_ENGINE_LLM_API_KEY`（缺省走 provider 原生
  环境变量如 `OPENAI_API_KEY`）。
- 语义：仅对能在原文定位 span 的候选做增强（标题等不回证的候选原样保留）；
  结果按原候选合并（保留 docId 等字段，采用 LLM 返回的名称/类型/置信度）。
- 降级（不抛错、不触发网络）：provider 未配置 / semantica 不可导入 /
  provider 初始化失败或无凭据 → 返回原候选 + `meta.llm` 说明。

## 5. 依赖与可用性

- RDF/SPARQL 需要 `pyoxigraph`（本开发 venv 已具备；部署安装方式：
  `pip install "semantica[tripletstore-oxigraph]"`）。
- LLM provider 包（openai/google-genai/anthropic/groq/ollama/transformers）本 venv
  已具备；镜像 core-only 变体不含 semantica 时两能力自动降级，不阻塞核心服务。
- 守卫探测结果进程内缓存；导入失败只记 warning。

## 6. 测试

- `tests/test_rdf_llm.py`：RDF 导出格式/缺失图、SPARQL SELECT/ASK/CONSTRUCT/
  limit/错误码、LLM 门控与合并语义，以及 HTTP/gRPC/MCP/CLI 四面对 `sparql` 的接线。
- 回归：`.venv/bin/python -m pytest tests -q`（36 passed）。

## 7. 待人工/后续项

- 出网真实 LLM 增强验证（需要 provider API key）。
- 镜像构建时确认 `pyoxigraph` 进入 semantica 变体（Dockerfile/requirements 增补）。
- SPARQL 白名单/超时/分页护栏（当前仅 SELECT `limit` 截断）；持久化 RDF 视图（增量索引）暂不做。
