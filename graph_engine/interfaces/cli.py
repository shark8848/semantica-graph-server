"""CLI 协议面：typer 命令 graph-engine，与 open-ikc `ikc` CLI 风格一致。"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

import typer

from ..application.service import GraphEngineService
from ..errors import GraphEngineError
from ..runtime import get_service

app = typer.Typer(
    name="graph-engine",
    help="Semantica Graph Engine 命令行（HTTP/gRPC/Celery/MCP/CLI 五面接口）",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

_service: GraphEngineService | None = None
_global_json = False


def _svc() -> GraphEngineService:
    if _service is None:
        return get_service()
    return _service


def _emit(data: Any) -> None:
    text = json.dumps(data, ensure_ascii=False, default=str)
    typer.echo(text)


def _exit_for(err: BaseException) -> None:
    if isinstance(err, GraphEngineError):
        raise typer.Exit(str(err.message), code=1)
    raise typer.Exit(str(err), code=6)


@app.callback()
def _main(
    db_path: Optional[str] = typer.Option(None, "--db-path", help="SQLite 存储路径（覆盖环境变量）"),
    json_output: bool = typer.Option(False, "--json", help="输出原始 JSON"),
) -> None:
    global _service, _global_json
    from ..runtime import get_store, get_settings

    _global_json = json_output
    if db_path:
        _service = GraphEngineService(get_store(db_path))


# ---------- serve ----------

@app.command()
def serve(
    kind: str = typer.Argument(..., help="http | grpc | mcp | worker"),
    host: str = typer.Option("", help="监听地址（默认取配置）"),
    port: int = typer.Option(0, help="监听端口（默认取配置）"),
) -> None:
    """启动协议面服务。"""
    from ..config import Settings

    settings = Settings()
    if kind == "http":
        import uvicorn

        uvicorn.run("graph_engine.interfaces.http_app:create_app", host=host or settings.http_host, port=port or settings.http_port, factory=True)
    elif kind == "grpc":
        from .grpc_server import serve_grpc

        serve_grpc(host=host or settings.grpc_host, port=port or settings.grpc_port)
    elif kind == "mcp":
        from .mcp_server import serve_mcp

        serve_mcp()
    elif kind == "worker":
        from .celery_app import worker_main

        worker_main(argv=["worker", "-l", "info"])
    else:
        raise typer.BadParameter(f"未知服务类型：{kind}（支持 http|grpc|mcp|worker）")


# ---------- graph ----------

@app.command()
def create(
    name: str = typer.Option("", "--name"),
    kb_id: str = typer.Option("", "--kb-id"),
    graph_id: str = typer.Option("", "--graph-id"),
    tenant_id: str = typer.Option("", "--tenant-id"),
    owner_id: str = typer.Option("", "--owner-id"),
    schema_json: str = typer.Option("", "--schema", help="graphSchema JSON 字符串"),
) -> None:
    """创建图谱。"""
    try:
        schema = json.loads(schema_json) if schema_json else {}
        _emit(_svc().create_graph(
            graph_id_value=graph_id, name=name, kb_id=kb_id,
            tenant_id=tenant_id, owner_id=owner_id, schema=schema,
        ))
    except Exception as exc:
        _exit_for(exc)


@app.command("list")
def list_graphs(
    tenant_id: str = typer.Option("", "--tenant-id"),
    owner_id: str = typer.Option("", "--owner-id"),
) -> None:
    """列出图谱。"""
    try:
        _emit(_svc().list_graphs(tenant_id=tenant_id, owner_id=owner_id))
    except Exception as exc:
        _exit_for(exc)


@app.command("get")
def get_graph(graph_id: str = typer.Argument(...)) -> None:
    """查询图谱元信息。"""
    try:
        _emit(_svc().get_graph(graph_id))
    except Exception as exc:
        _exit_for(exc)


@app.command("delete")
def delete_graph(graph_id: str = typer.Argument(...)) -> None:
    """删除图谱。"""
    try:
        _emit(_svc().delete_graph(graph_id))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def stat(graph_id: str = typer.Argument(...)) -> None:
    """图谱统计（含 schema 覆盖率）。"""
    try:
        _emit(_svc().stat(graph_id))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def build(
    graph_id: str = typer.Argument(...),
    text: str = typer.Option("", "--text"),
    doc_id: str = typer.Option("", "--doc-id"),
    title: str = typer.Option("", "--title"),
    llm: bool = typer.Option(False, "--llm", help="文本建图时开启 LLM 实体增强（需配置 GRAPH_ENGINE_LLM_PROVIDER）"),
    records_json: str = typer.Option("", "--records", help='{"entities":[...],"relations":[...]} JSON'),
) -> None:
    """建图：--text 走规则抽取；--records 走显式记录。"""
    try:
        if records_json:
            payload = json.loads(records_json)
            result = _svc().build_from_records(
                graph_id,
                entities=payload.get("entities") or [],
                relations=payload.get("relations") or [],
                doc_id=doc_id,
            )
        else:
            result = _svc().build_from_text(graph_id, text=text, doc_id=doc_id, title=title, llm=llm)
        _emit(result)
    except Exception as exc:
        _exit_for(exc)


@app.command()
def merge(
    graph_id: str = typer.Argument(...),
    records_json: str = typer.Option(..., "--records", help='{"entities":[...],"relations":[...]} JSON'),
    doc_id: str = typer.Option("", "--doc-id"),
) -> None:
    """增量合并实体/关系。"""
    try:
        payload = json.loads(records_json)
        _emit(_svc().merge_records(
            graph_id,
            entities=payload.get("entities") or [],
            relations=payload.get("relations") or [],
            doc_id=doc_id,
        ))
    except Exception as exc:
        _exit_for(exc)


@app.command("deprecate-doc")
def deprecate_doc(graph_id: str = typer.Argument(...), doc_id: str = typer.Argument(...)) -> None:
    """按 docId 增量废弃。"""
    try:
        _emit(_svc().deprecate_doc(graph_id, doc_id=doc_id))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def nodes(
    graph_id: str = typer.Argument(...),
    entity_type: str = typer.Option("", "--entity-type"),
    name: str = typer.Option("", "--name"),
    page: int = typer.Option(1, "--page"),
    page_size: int = typer.Option(20, "--page-size"),
) -> None:
    """分页查询实体节点。"""
    try:
        _emit(_svc().list_nodes(graph_id, entity_type=entity_type, name=name, page=page, page_size=page_size))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def edges(
    graph_id: str = typer.Argument(...),
    relation_type: str = typer.Option("", "--relation-type"),
    page: int = typer.Option(1, "--page"),
    page_size: int = typer.Option(20, "--page-size"),
) -> None:
    """分页查询关系边。"""
    try:
        _emit(_svc().list_edges(graph_id, relation_type=relation_type, page=page, page_size=page_size))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def neighbors(
    graph_id: str = typer.Argument(...),
    entity_id: str = typer.Argument(...),
    depth: int = typer.Option(1, "--depth"),
) -> None:
    """实体邻域查询。"""
    try:
        _emit(_svc().neighbors(graph_id, entity_id_value=entity_id, depth=depth))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def paths(
    graph_id: str = typer.Argument(...),
    source_entity_id: str = typer.Argument(...),
    target_entity_id: str = typer.Argument(...),
    max_depth: int = typer.Option(5, "--max-depth"),
) -> None:
    """最短路径查询。"""
    try:
        _emit(_svc().paths(graph_id, source_entity_id=source_entity_id, target_entity_id=target_entity_id, max_depth=max_depth))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def export(graph_id: str = typer.Argument(...), format: str = typer.Option("jsonl", "--format")) -> None:
    """导出图谱（jsonl/json；RDF：turtle/nt/nq/rdfxml）。"""
    try:
        _emit(_svc().export(graph_id, format=format))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def sparql(
    graph_id: str = typer.Argument(...),
    query: str = typer.Option("", "--query", help="SPARQL 查询语句（SELECT/ASK/CONSTRUCT/DESCRIBE）"),
    limit: int = typer.Option(0, "--limit", help="SELECT 结果截断行数（0 不截断）"),
) -> None:
    """SPARQL 查询当前图（RDF 视图，需 pyoxigraph）。"""
    try:
        _emit(_svc().sparql(graph_id, query=query, limit=limit))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def analytics(graph_id: str = typer.Argument(...)) -> None:
    """semantica 图结构分析。"""
    try:
        _emit(_svc().analytics(graph_id))
    except Exception as exc:
        _exit_for(exc)


# ---------- job ----------

@app.command()
def job_run(job_id: str = typer.Argument(...)) -> None:
    """同步执行任务。"""
    try:
        _emit(_svc().run_job(job_id))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def job_get(job_id: str = typer.Argument(...)) -> None:
    """查询任务状态。"""
    try:
        _emit(_svc().get_job(job_id))
    except Exception as exc:
        _exit_for(exc)


@app.command()
def job_list(graph_id: str = typer.Option("", "--graph-id"), limit: int = typer.Option(20, "--limit")) -> None:
    """列出任务。"""
    try:
        _emit(_svc().list_jobs(graph_id_value=graph_id, limit=limit))
    except Exception as exc:
        _exit_for(exc)


if __name__ == "__main__":
    app()
