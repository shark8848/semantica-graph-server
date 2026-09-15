# 方案：OxigraphStore RDF/SPARQL 视图 + LLM 建图增强

> 对应 `docs/解决方案.md` §7「后续演进」条目落地（2026-09-07，MVP 阶段）。
> 状态：核心能力 + 五面接口 + SPARQL 护栏（白名单/超时/分页）+ 单测已落地
> （`pytest tests` 41 passed）；`pyoxigraph` 依赖已在 requirements/pyproject
> 经 `semantica[tripletstore-oxigraph]` 声明；真实 LLM provider 出网验收、
> 镜像构建复验列为待人工/后续项。

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

SPARQL 护栏（只读安全，2026-09-08 增补）：
- **白名单**：仅允许 `SELECT/ASK/CONSTRUCT/DESCRIBE` 只读查询表单；写类表单
  （`INSERT/DELETE/LOAD/CLEAR/...`）一律拒绝为 `200001`（field `query`）；
  识别时跳过注释与 `BASE/PREFIX` 声明，字符串字面量内的关键字不干扰判定。
- **超时**：查询执行期按行做墙钟检查（默认 `10s`，`GRAPH_ENGINE_SPARQL_TIMEOUT`
  可调，`0` 关闭），超时即时中止并收敛 `200001`；实时视图构建的一次性映射
  不设中断点，构建完成后下一行检查即生效。
- **分页**：结果行/三元组按 `min(limit, GRAPH_ENGINE_SPARQL_MAX_ROWS)`（默认
  上限 `5000`）流式截断，`limit<=0` 走默认上限；响应附 `rowLimit`/`truncated`
  供调用方感知（ASK 布尔结果无行数概念，不附加）。

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
  `pip install "semantica[tripletstore-oxigraph]"`）；该依赖已写入
  `requirements.txt`/`pyproject.toml`（`semantica[tripletstore-oxigraph]==0.6.5`），
  Docker 构建会自动拉入，镜像内复验列为待执行项。
- LLM provider 包（openai/google-genai/anthropic/groq/ollama/transformers）本 venv
  已具备；镜像 core-only 变体不含 semantica 时两能力自动降级，不阻塞核心服务。
- 守卫探测结果进程内缓存；导入失败只记 warning。

## 6. 测试

- `tests/test_rdf_llm.py`：RDF 导出格式/缺失图、SPARQL SELECT/ASK/CONSTRUCT/
  limit/错误码/护栏（白名单、分页截断、超时）、LLM 门控与合并语义，
  以及 HTTP/gRPC/MCP/CLI 四面对 `sparql` 的接线。
- 回归：`.venv/bin/python -m pytest tests -q`（41 passed）。

## 7. 待人工/后续项

- 出网真实 LLM 增强验证（需要 provider API key）。
- 镜像构建复验（`pyoxigraph` 依赖声明已落地，待 `build_docker.sh` + 冒烟确认）。
- 持久化 RDF 视图（增量索引）暂不做。
