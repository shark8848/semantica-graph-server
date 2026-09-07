# Semantica Graph Engine — 原生 MCP/检索能力接入（调研与方案）

> 状态：研究/方案草稿 v1（2026-09-07），**仅只读调研产出，未实施、未改任何代码/依赖**。
> 落地更新（2026-09-07 同日）：**阶段 2-3 的代码与单测骨架已先行落地**——新增 `graph_engine/adapters/retrieval.py`
> （检索链守卫式探测 `retrieval_available()` + 可注入 backend 契约 + `record_to_doc` 向量条目结构），
> `service.py` 新增 `semantic_search`/`index_status`，MCP 工具 **15→17**（+`graph_search`/`graph_index_status`，
> 已注册 TOOLS 与 handler），新增 `tests/test_retrieval.py`（6 用例，全量回归 20 通过）。
> **真实语义链（vector_store/context/embeddings）仍待阶段 1 pinecone 依赖修复后激活**：当前对外表现为
> 确定性降级（`semantica:false`/`hits:[]`），不声称真实语义检索已可用；索引/迁移动作（index_graph/drop_doc）
> 与 HTTP/gRPC/CLI 端点均未实施。看板条目状态不受本骨架影响。
> 范围：把 semantica 0.6.5 的「原生 MCP + 检索（context/vector_store/embeddings）」能力接入本引擎的方案设计；
> 前提：看板条目「semantica 原生 MCP/检索能力接入（pinecone 依赖修复后）」当前为 **P2 未开始**，本方案结论是该条目应**保持未开始**，直至有网环境完成 pinecone 依赖修复。
> 调研方式：只读浏览 `.venv` 内 semantica 0.6.5 源码 + `.venv/bin/python -c/import` 实测复现（不触发网络、不安装/卸载包、不落盘修改 venv）。

## 1. 现状与调研方法

### 1.1 引擎侧现状（MCP 面最小实现）
- `graph_engine/interfaces/mcp_server.py` docstring 明确：「与 semantica 自带 mcp_server 同思路，但只依赖引擎 application 层，**避开当前 venv 中损坏的 semantica.context/vector_store 导入链**」。
- 对外 stdio JSON-RPC，15 个 `graph_*` 工具（**2026-09-07 骨架落地后新增 `graph_search`/`graph_index_status`，共 17 个**，见 §5 阶段 3）：`graph_create / graph_list / graph_get / graph_delete / graph_stat / graph_build / graph_merge / graph_deprecate_doc / graph_nodes / graph_edges / graph_neighbors / graph_paths / graph_export / graph_job_run / graph_job_get`。
- 能力特征：CRUD/建图/合并/废弃/结构化查询/任务，全部落在 `graph_engine/application/service.py`；查询是结构化过滤（entityType/name）与图遍历，**无向量召回、无语义相似检索**。
- 文本建图 `build_from_text` 是规则占位（标题 + 引号候选词），SQLite 只存 `graphs/entities/relations/jobs` 四类表，**不保留文档原文/chunk**。
- 适配层 `graph_engine/adapters/semantica.py` 只守卫式 import `semantica` 与 `semantica.kg`（GraphBuilder/Analyzer），刻意不触碰 `semantica.context/vector_store`。

### 1.2 调研动作（本任务实际执行，均只读）
1. `rg --files .venv/lib/python3.12/site-packages/semantica*`（403 个文件）盘点模块拓扑。
2. 读 `semantica-0.6.5.dist-info/METADATA`、`entry_points.txt`、`semantica/mcp_server/__init__.py`、`context/__init__.py`、`vector_store/*.py`、`embeddings/text_embedder.py`、`ingest/mcp_*.py` 等。
3. `.venv/bin/python -c "import semantica.vector_store"` 与 `import semantica.context` 实测复现（关键 traceback 见 §3.1）。
4. 用 sys.meta_path 影子实验（进程内、不落盘）验证「pinecone 表现为缺失而非抛裸 Exception」时链路可恢复（见 §3.2）。
5. 读 `docs/解决方案.md`、`docs/project-board.tsv`、`graph_engine/` 源码确认现状与语义约束。

### 1.3 关键结论速览
| 结论 | 说明 |
| --- | --- |
| 损坏点唯一 | 仅 `.venv` 中 `pinecone-client==6.0.0` 改名桩 `import pinecone` 抛**裸 Exception**，逃过 semantica 模块级 `except (ImportError, OSError)` 守卫 |
| 影响面 | `semantica.vector_store`、`semantica.context` 整棵子树 import 失败 → semantica 原生 MCP 的 `ContextGraph` 运行时导入即失败 |
| 最小修复 | 有网环境卸载桩包并安装新发布名 `pinecone`（§3.3），或临时移除桩文件使其表现为「缺失」以恢复导入（Pinecone 后端不可用） |
| 接入价值 | semantica 原生「检索」能力主体在库 API（context/vector_store/embeddings），其自带 mcp_server 只是 12 个决策/建图向工具，仍需引擎 adapter 层做数据模型映射后才能为 open-ikc 语义服务 |
| 看板处置 | 条目保持 P2 未开始；本文是前置调研/方案，不做任何「已接入」声明 |

## 2. semantica 0.6.5 原生能力盘点

### 2.1 模块拓扑与可用面（site-packages/semantica）
| 模块 | 能力 | 当前 venv 可 import？ | 备注 |
| --- | --- | --- | --- |
| `semantica`（顶层） | `__version__=0.6.5`，轻量 | ✅ | 引擎 `semantica_available()` 已用 |
| `kg` | GraphBuilder/KnowledgeGraph/EntityResolver/GraphAnalyzer/PathFinder/CentralityCalculator/CommunityDetector/NodeEmbedder（node2vec）/ProvenanceTracker | ✅ | 引擎已守卫接入建图/分析/导出 |
| `mcp_server`（console `semantica-mcp`） | stdio MCP，12 工具（§2.2） | ⚠️ 模块可 import，运行时 `_get_graph()`→`import semantica.context` 失败 | 被 §3 损坏点阻塞 |
| `context` | AgentContext/ContextGraph/ContextRetriever/AgentMemory/EntityLinker/Decision 追踪（record_decision/find_precedents/causal/policy） | ❌ 失败 | 被 §3 损坏点阻塞 |
| `vector_store` | VectorStore 多后端（faiss/qdrant/weaviate/milvus/pinecone/pgvector/sqlite-vec/inmemory）、metadata 过滤、namespace、hybrid_search/hybrid_similarity、决策向量流水线 | ❌ 失败 | 被 §3 损坏点阻塞 |
| `embeddings` | TextEmbedder（默认 fastembed + `BAAI/bge-small-en-v1.5`，可 sentence_transformers/哈希降级）、EmbeddingGenerator、provider_stores（openai/bge/fastembed）、GraphEmbeddingManager | ✅（本地推理可用；模型首载需下载） | 不依赖 pinecone |
| `ingest`（含 `mcp_client.py` / `mcp_ingestor.py`） | 以「MCP 即数据源」方式接入外部 MCP server 摄取内容 | 视第三方驱动而定 | 与本文检索侧关联度低，可后置 |
| `export/vector_exporter.py` | 向量导出 | — | 次要 |
| `semantic_extract` | NER/RelationExtractor/LLM 抽取 | ✅（NER/规则侧） | LLM 抽取依赖 `llm-*` extra（未装） |

### 2.2 semantica 原生 MCP（`semantica/mcp_server`，12 工具）
`main()` 入口（`python -m semantica.mcp_server` 或 console `semantica-mcp`），环境变量 `SEMANTICA_KG_PATH`（启动加载持久图，可选）/`SEMANTICA_LOG_LEVEL`。工具清单：

| 工具 | 说明 | 备注 |
| --- | --- | --- |
| `extract_entities` / `extract_relations` | 文本 NER / 三元组抽取 | 不依赖 ContextGraph |
| `record_decision` | 记录决策到 ContextGraph（含因果/元数据） | 运行时需 `import semantica.context` |
| `query_decisions` | 决策检索：自然语言/分类/最近 | 内部走 `find_similar_decisions` |
| `find_precedents` | 按场景找相似先例 | 同上 |
| `get_causal_chain` | 决策因果链 | 同上 |
| `add_entity` / `add_relationship` | 直接加节点/边 | 同上 |
| `run_reasoning` | 前向链规则推理 | 同上 |
| `get_graph_analytics` / `get_graph_summary` / `export_graph` | PageRank/社区、统计摘要、RDF/JSON 导出 | 同上 |

要点：
- 原生 MCP **没有直接的「向量检索 / 文档 QA」工具**，`query_decisions/find_precedents` 的语义相似是在 `ContextGraph` 内存决策集上做的（内容相似 0.7 + 结构相似 0.3，见 `context_graph.py:find_precedents_by_scenario`），**不读引擎 SQLite 图谱、不挂外部向量库**。
- 因此「原生 MCP/检索能力接入」的正确姿势不是透传其 mcp_server，而是修复依赖后用其 **context/vector_store/embeddings 库能力**在引擎 adapter 层实现检索，再按引擎工具风格暴露。

### 2.3 检索 / 建图能力主体（修复后的可用点）
- `semantica.context`：
  - `ContextGraph(advanced_analytics=True)`：内存上下文图；`record_decision`、`find_similar_decisions`、`find_precedents_by_scenario`、因果/策略分析。**实例化本身不依赖向量库**（影子实验已验证）。
  - `AgentContext(vector_store=vs, ...)`：高层「记忆 + 图 + 向量」入口，`store(text, conversation_id=...)` / `retrieve(query)` / `find_precedents(...)`；构造要求显式传 vector_store。
  - `ContextRetriever.retrieve(...)`：多源混合召回（图节点/关系/记忆/向量，支持时间过滤与 `TemporalGraphRetriever`）。
  - `AgentMemory`：带 RAG 的持久记忆。`EntityLinker`：跨源实体链接/URI。
- `semantica.vector_store`：`VectorStore(backend=...)` 统一接口（upsert/query/delete/namespace/metadata 过滤/混合检索）；`HybridSearch`（RRF/加权融合）、`DecisionEmbeddingPipeline` 等。后端中 faiss（已装 1.15.0）适合本地/离线，pinecone/qdrant/milvus/weaviate/pgvector 适合外置。
- `semantica.embeddings`：`TextEmbedder(model_name="BAAI/bge-small-en-v1.5", method="fastembed", device="cpu")`；模型加载失败自动降级哈希嵌入。venv 已装 fastembed 0.8.0 / onnxruntime（import vector_store 时出现 onnxruntime telemetry 警告即为该链被拉起）。
- `semantica.kg`（引擎已用）：`GraphBuilder.build()` 负责「抽取记录→图」的去重/合并/证据，是引擎语义建图的主体，检索阶段保持复用。

### 2.4 第三方组件在 import 链中的位置与版本特征
- **pinecone 是唯一「包级导入即炸」点**：全包仅 `vector_store/pinecone_store.py:46` 一处 `from pinecone import Pinecone as PineconeClientLib, ServerlessSpec, PodSpec`；而 `vector_store/__init__.py:181` 在包顶层**无条件** `from .pinecone_store import PineconeStore, ...`，使所有 import 拉爆。
- 其余后端均按「可选导入」设计：weaviate_client 未装、psycopg 未确认等都在各自 store 模块内 `try: import ... except (ImportError, OSError)`，缺失只置 `*_AVAILABLE=False`，不阻塞包导入（影子实验中 `weaviate_client` 缺失未影响导入，已证实）。
- 版本/API 特征（semantica 期望的 pinecone SDK）：
  - 顶层符号：`Pinecone`（客户端类）、`ServerlessSpec`、`PodSpec`（spec 构造）。
  - 调用形态：`Pinecone(api_key=...)`；`client.create_index(name, dimension, metric, spec=...)`、`delete_index`、`list_indexes()`（元素带 `.name`）、`client.Index(name)`；index 侧 `upsert(vectors=[(id, vector, metadata)], namespace=...)`、`query(vector, top_k, namespace, include_metadata=True, filter=...)`、`delete(ids)`、`fetch(ids)`、`describe_index_stats()`。
  - semantica METADATA extra `vectorstore-pinecone` 写的是 **旧发布名 `pinecone-client>=3.0.0`**——旧发布名解析到的最新版恰是改名桩（见 §3），这是容易反复踩坑的点，需显式安装新发布名。
- 本地嵌入链（无 pinecone 依赖）：fastembed（已装 0.8.0）→ onnxruntime → 默认 bge-small 模型（首载需下载）；sentence-transformers/torch（已装）可作备选；纯本地且模型不可得时 TextEmbedder 可降级哈希嵌入。

## 3. 当前 venv 导入链损坏：精确定位

### 3.1 复现（`.venv/bin/python`，Python 3.12.3；venv 内为 semantica 0.6.5 + `pinecone_client-6.0.0.dist-info`）
命令一：`import semantica.vector_store`
```
Traceback (most recent call last):
  File ".../semantica/vector_store/__init__.py", line 181, in <module>
    from .pinecone_store import PineconeStore, PineconeClient, PineconeIndex, PineconeSearch
  File ".../semantica/vector_store/pinecone_store.py", line 46, in <module>
    from pinecone import Pinecone as PineconeClientLib, ServerlessSpec, PodSpec
  File ".../pinecone/__init__.py", line 5, in <module>
    raise Exception(
Exception: The official Pinecone python package has been renamed from `pinecone-client` to `pinecone`. Please remove `pinecone-client` from your project dependencies and add `pinecone` instead. See the README at https://github.com/pinecone-io/pinecone-python-client ...
```
命令二：`import semantica.context`（`context/__init__.py:108 → agent_context.py:80 → context_retriever.py:85 → vector_store/__init__.py:181`，尾部同上）
```
  File ".../semantica/context/__init__.py", line 108, in <module>
    from .agent_context import AgentContext
  File ".../semantica/context/agent_context.py", line 80, in <module>
    from .context_retriever import ContextRetriever, RetrievedContext
  File ".../semantica/context/context_retriever.py", line 85, in <module>
    from ..vector_store.hybrid_similarity import HybridSimilarityCalculator
  File ".../semantica/vector_store/__init__.py", line 181, in <module>
    ...
Exception: The official Pinecone python package has been renamed ...
```
性质：损坏的不是「缺包」，而是 **venv 里躺着旧发布名 `pinecone-client` 6.0.0 的改名桩**：其 `pinecone/__init__.py` 仅一句 `raise Exception("...renamed...install `pinecone` instead...")`（同目录 `__version__` 为 `6.0.0`）。该桩**抛裸 `Exception`**，而 semantica 的模块守卫是 `except (ImportError, OSError)`（`pinecone_store.py:45-52`），因此守不住。

### 3.2 根因验证（进程内影子实验，只读）
在 `sys.meta_path` 挂一个让 `import pinecone` 表现为 **ModuleNotFoundError（=未安装）** 的拦截器后：
```
IMPORT_OK semantica.vector_store          # 且 PINECONE_AVAILABLE = False
IMPORT_OK semantica.context
ContextGraph(advanced_analytics=True) OK  # 可实例化
```
结论：只要「import pinecone」要么成功（装真 SDK）要么是标准的 ModuleNotFoundError（被守卫吞掉），`semantica.context/vector_store` 即恢复可导入。**当前唯一的破坏源就是桩包抛裸 Exception。**

### 3.3 最小修复动作（需在有网环境执行；本任务沙箱禁止安装，未执行）
首选（恢复 pinecone 后端能力）：
1. `pip uninstall -y pinecone-client`（卸载改名桩 `pinecone_client-6.0.0`）；需网络 + venv 写权限。
2. `pip install "pinecone>=6,<7"`（新发布名 `pinecone`；若实际仓库版本号不同，以 `pip index versions pinecone` 实测为准，需网络）。
3. 验证：`.venv/bin/python -c "import semantica.context, semantica.vector_store; from semantica.vector_store import VectorStore; print(VectorStore, __import__('pinecone').__version__)"`，并确认 `import semantica.kg` 与引擎 `pytest tests -q` 不回归。
4. 提醒：**不要再**用 `pip install 'semantica[vectorstore-pinecone]'` 或 `pip install pinecone-client` 拉依赖——semantica METADATA 的旧名约束会再次解析到改名桩；如需走 extra，须先显式固定新名并卸载桩包。

备选（离线、只求恢复导入；Pinecone 远端后端不可用）：
- 由维护者在有 venv 写权限的环境删除/改名桩文件 `site-packages/pinecone/`（`__init__.py`、`__version__`）与 `pinecone_client-6.0.0.dist-info/`，使 import 变成 ModuleNotFoundError 被守卫吞掉 → context/vector_store 可导入、`PINECONE_AVAILABLE=False`。该操作是环境运维动作，不可进 git/镜像层；引擎代码侧不依赖它。
- 环境变量：本地/离线模式建议显式 `VECTOR_STORE_DEFAULT_BACKEND=faiss` 或 `sqlite`（semantica config 支持 env/yaml 覆盖；命名精确值以有网实测 semantica `vector_store/config.py` 的 env_mappings 为准，模块 docstring 口径为 `SEMANTICA_VECTOR_STORE_*`）；pinecone API key 走代码 `PineconeStore(api_key=...)` 或 SDK 标准环境变量（`PINECONE_API_KEY`，需有网验证）。

## 4. 差距分析：现有 graph_engine MCP vs 目标检索能力

| 能力维度 | 现有引擎（15 个 graph_* 工具） | semantica 原生可用点（依赖修复后） | 差距 | 建议补齐方式 |
| --- | --- | --- | --- | --- |
| 结构化查询 | graph_nodes/graph_edges/graph_neighbors/graph_paths（SQLite+图遍历） | ContextGraph/vector_store metadata 过滤 | 名称/类型为字面匹配，无打分 | 保留现有；检索侧另起 |
| 向量召回 | ❌ 无 embedding、无向量索引 | `vector_store.VectorStore`（faiss 本地优先）+ `embeddings.TextEmbedder` | 全文缺失 | adapter 建「实体/关系向量索引」，记录向量 id 锚定稳定 ID |
| 语义检索 | ❌ 无 | `VectorStore.query` / `ContextRetriever.retrieve` / `AgentContext.retrieve` | 无自然语言→记录召回 | 新增 `graph_search` 工具（自然语言→实体/关系/决策记录） |
| 文档/片段检索 QA | ❌ SQLite 不存原文/chunk | ContextRetriever 可召回记忆/上下文；无开箱 QA | 引擎侧无原文留存，无片段切分 | 建议先做「记录级检索」，文档级 QA 列为后续演进（需文本留存方案） |
| 决策先例/相似 | ❌ 引擎不存决策语义 | `ContextGraph.find_similar_decisions`、`decision` 子模块 | 数据模型不同（open-ikc 实体关系 vs semantica Decision） | adapter 映射或仅对 decision 语义扩展时启用 |
| 建图（抽取→图） | ✅ graph_build/graph_merge（rules 占位 + kg.GraphBuilder records） | `semantic_extract`（NER/关系）、kg | text 抽取为占位规则 | 可后置接 NER/LLM 抽取（LLM 需 extra/网络） |
| 语义与持久化兼容 | ✅ 稳定 ID/证据/置信度/docId 废弃 | ContextGraph 为内存模型 | 原生模型无 engine 的 docId 废弃/证据语义 | adapter 层保证「向量内容随 merge/deprecate_doc 同步」 |
| 工具暴露风格 | graph_* + envelope/traceId | 原生英文工具名、decision 语义 | 不一致 | 以现有 graph_* 命名 + envelope 为准扩展 |

核心差距一句话：**引擎目前只有「图结构检索」，缺「语义/向量检索」；而 semantica 检索能力被 pinecone 桩包卡死，且其数据模型（决策/记忆）与 open-ikc（实体关系 + docId/证据）不同，不能透传，必须经 adapter 映射。**

## 5. 分阶段接入方案

> 文件路径均为**建议**，实际以落地实现为准；阶段 2-4 全部依赖阶段 1 的依赖修复（有网环境执行）。

### 阶段 1：pinecone 依赖修复（有网环境；前置阻塞解除）
- 动作：按 §3.3 卸载桩包、安装新发布名 `pinecone`，跑验证命令；不改仓库代码。
- 建议文件（可选，仅注释/约束）：`requirements.txt` 增注释说明「检索能力不随引擎基础依赖安装，pinecone 属可选运维依赖」；或新增 `requirements-retrieval.txt`（建议，含 `pinecone>=6,<7`、`fastembed` 等）供构建/部署按需装。
- 验收：`import semantica.context, semantica.vector_store` 通过；`PINECONE_AVAILABLE` 为 True（装真 SDK）或 False（仅离线模式，本地 faiss 可用）；`.venv/bin/python -m pytest tests -q` 12 用例不回归。
- 风险：无网环境做不了；semantica 旧名 extra 会再次引入桩包（§3.3 提醒）。

### 阶段 2：adapter 层暴露原生检索能力（离线可开发，冒烟需网络/模型）

> **落地（2026-09-07，仅骨架）**：`graph_engine/adapters/retrieval.py` 已交付——守卫式探测
> `retrieval_available()`（捕获一切 Exception，进程内缓存；真实导入失败路径开销大，仅探测一次）、
> 可注入 `RetrievalBackend` 契约（upsert/query/delete，按 graphId 命名空间隔离）与向量条目结构
> `record_to_doc`（id 锚定稳定 ID，metadata 携带 kind/type/docId/confidence/evidence 对齐 open-ikc 语义）。
> **未实施**：`index_graph`/`drop_doc` 等真实索引/废弃同步动作、faiss/embeddings 接线，留待阶段 1 依赖修复后启用。
- 目标：把 semantica 检索能力收口到引擎 adapter，保持「损坏/缺失则降级」的既有风格。
- 建议改动：
  - 新建 `graph_engine/adapters/retrieval.py`（建议）：守卫式 import `semantica.embeddings.TextEmbedder` / `semantica.vector_store.VectorStore`；提供 `index_graph(graph_id)`（遍历 SQLite records→embed→upsert）、`semantic_search(graph_id, query, top_k)`、`drop_doc(graph_id, doc_id)`（随 `deprecate_doc` 同步删向量/标记失效）；向量 id 锚定现有稳定 ID（`entity_id/relation_id`，见 `graph_engine/domain/ids.py`），metadata 携带 `docId/type/confidence` 以对齐 open-ikc 语义。
  - 可选：`graph_engine/config.py` 增 retrieval 配置（backend 默认 `faiss`、embedding provider/model、开关），不引新必装依赖。
  - 建议先本地后端：venv 已装 faiss 1.15.0 与 fastembed；默认模型 `BAAI/bge-small-en-v1.5` 首载需下载（需网络一次），不可得时依赖 TextEmbedder 哈希降级（语义弱，仅保证链路通）。
- 验收：新增单测（建议 `tests/test_retrieval_adapter.py`）：mock embedder 或已缓存模型下 index→search 召回稳定 ID；deprecate 后不召回；无 semantica 检索链时返回 `{"semantica": false}` 降级不抛错。
- 风险/开放问题：embedding 模型与维度固定策略；图内无原文，若要做文本片段级检索需先解决「原文/chunk 留存」（引擎当前不存原文——见 §6）。

### 阶段 3：MCP tools 扩展（检索能力对外）

> **落地（2026-09-07，仅骨架）**：`graph_search`/`graph_index_status` 已按现有 lambda 风格注册到
> MCP `TOOLS`/`_tool_handlers`（15→17），接到 `service.semantic_search`/`index_status`；
> 检索链不可用或后端未配置时确定性降级（`semantica:false`/`hits:[]`），不抛错。
> **未实施**：真实向量召回（依赖阶段 1 后注入 backend）；HTTP/gRPC/CLI 检索端点（后续单独排期）。
- 目标：在现有 15 个工具基础上扩展语义检索，命名/信封沿用 `graph_*`。
- 建议改动：
  - `graph_engine/interfaces/mcp_server.py`：新增工具（建议名与语义）——
    - `graph_search`：自然语言/查询文本 → 召回相关实体/关系记录（返回 topK、score、稳定 ID、证据 docId）。
    - `graph_index_status`：返回图谱向量索引状态/统计（可选，便于运维与调试）。
    - 后续演进（不在本阶段承诺）：`graph_retrieve_docs`（文档片段级，需阶段 2 文本留存配套）。
  - 同一 handler 表按现有 lambda 风格接到 application 层新方法（建议在 `service.py` 增加 `semantic_search(...)`，内部调 `adapters.retrieval`）。
  - 可选并行面：HTTP/gRPC/CLI 是否同步暴露同一检索能力，建议与 MCP 共用 service 方法，面层各加一两个端点/命令即可（P2 范围可只做 MCP+CLI）。
- 验收：单测覆盖 `tools/list` 出现新工具、`call_tool(graph_search)` 命中 mock 向量；全量 `pytest tests -q` 通过；不引入未配置格式/静态检查。
- 风险：MCP 冒烟需要 embedder 可用（模型缓存或网络）；接口返回结构与 open-ikc envelope（traceId/errCode/errMsg）对齐由 application 层保证。

### 阶段 4：冒烟与回归（含 Docker/文档同步）
- 建议改动：`tests/` 检索用例并入全量回归；如涉及 Docker/镜像，按 AGENTS.md 跑 `bash scripts/docker_smoke.sh`（health/HTTP create+stat/gRPC/stats/回环隔离/非 root）；文档同步 `docs/解决方案.md` 的「能力映射/风险表」并把本方案定稿；按流程更新 `docs/project-board.tsv`（该条目由 P2 未开始→进行中/已完成需**完成阶段 3 验收后**才可改，本任务不改）。
- 验收：离线回归 `pytest tests -q` 全绿；Docker 改动后 docker_smoke 全绿；看板条目同步。
- 风险：镜像体积（检索链带 onnxruntime/fastembed/faiss/torch 需评估，对应已有「镜像体积精简评估 P2」条目）；embedding 模型在容器内首次运行的下载依赖与网络策略。

## 6. 风险与开放问题
| 风险/开放问题 | 说明与对策 |
| --- | --- |
| 无网环境无法修依赖 | 阶段 1 必须在有网环境执行；本任务已把「最小动作 + 验证命令」写清，未执行安装 |
| semantica 旧名 extra 再次引入改名桩 | 不装 `semantica[vectorstore-pinecone]`；显式固定新发布名并卸载桩包 |
| embedding 模型下载/GPU | 默认 fastembed+bge-small CPU 推理；首载需网络；模型不可得时哈希降级（语义弱），不建议生产用 |
| pinecone 服务可用性 | 远端 backend 需 API key/网络；本地/离线建议 faiss 或 sqlite 后端先行，pinecone 作为可选后端后置验证 |
| 与 open-ikc 语义兼容 | 向量 metadata 必须携带 docId/type/confidence/evidence；`deprecate_doc`/merge 后向量同步失效，避免「废弃文档仍被召回」 |
| 引擎不存原文/chunk | 记录级语义检索可直接做；「文档检索 QA」需先设计文本留存（新表或 sidecar），属 P2 后的演进 |
| 决策/记忆模型差异 | semantica 原生 decision/memory 语义与 open-ikc 图谱不同，不做透传；仅在 adapter 显式映射的场景启用 |
| 测试确定性 | 向量检索用固定 mock embedder 或缓存模型，避免 CI 依赖网络/随机性 |

## 7. 结论
- **被依赖修复阻塞（不能先动）**：阶段 1 pinecone 修复必须先行；在此之前 semantica.context/vector_store 无法 import，任何「接入」都无法落地，看板 P2 条目**保持未开始**。
- **可先行（无需依赖修复）**：本方案已交付的差距盘点与阶段规划；引擎现有测试/能力面保持不回归；检索 adapter 的接口设计、测试骨架、文档与 TSV 流程可先起草（本任务未做任何改动）。
- **已先行（2026-09-07）**：阶段 2-3 骨架落地——`adapters/retrieval.py`、`service.semantic_search/index_status`、
  MCP `graph_search`/`graph_index_status`（15→17）与 `tests/test_retrieval.py`（全量 20 用例通过）；
  真实向量索引与语义召回仍以阶段 1 依赖修复为前提，当前对外为确定性降级，不做「已接入」声明。
- **可并行（看板其他条目）**：「镜像体积精简评估（P2）」「MCP/CLI 镜像内冒烟脚本（P2）」与检索接入存在交集，可在阶段 1 后并行推进。
- **本任务边界**：调研任务仅产出本方案文档（未改代码/依赖、未执行 git 操作、未安装/卸载 python 包、未做「已接入」声明）；
  同日另以独立骨架任务先行落地阶段 2-3 代码与单测（见文首「落地更新」与 §5 各阶段标注，改动仅限 5 个文件，未触碰依赖/venv/看板）。
  下一步建议：在**有网环境**执行 §3.3 依赖修复并回填验证结果，再按阶段 2→4 排期。
