"""gRPC 协议面：GraphService（graph.v1）。

环境无 grpc-tools，采用运行时动态 descriptor（FileDescriptorProto + message_factory +
grpc generic handler）实现服务端与客户端；proto/graph/v1/graph.proto 为权威契约，
接入 CI 后可无缝切换 grpc_tools 生成代码。
"""

from __future__ import annotations

import json
from concurrent import futures
from typing import Any, Callable

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from ..errors import GraphEngineError
from ..protocol import error, new_trace_id, ok
from ..runtime import get_service

SERVICE_NAME = "graph.v1.GraphService"
MESSAGE_NAME = "graph.v1.Envelope"


def _build_pool() -> descriptor_pool.DescriptorPool:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "graph/v1/graph.proto"
    file_proto.package = "graph.v1"
    file_proto.syntax = "proto3"
    envelope = file_proto.message_type.add()
    envelope.name = "Envelope"
    for name, number in (
        ("trace_id", 1),
        ("err_code", 2),
        ("err_msg", 3),
        ("data_json", 4),
    ):
        field = envelope.field.add()
        field.name = name
        field.number = number
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_proto)
    return pool


_pool = _build_pool()
Envelope = message_factory.GetMessageClass(_pool.FindMessageTypeByName(MESSAGE_NAME))


def _ser(msg: Any) -> bytes:
    return msg.SerializeToString()


def _deser(data: bytes) -> Any:
    msg = Envelope()
    msg.ParseFromString(data)
    return msg


# ---------- 方法分发 ----------

METHOD_HANDLERS: dict[str, Callable[[Any, dict[str, Any]], dict[str, Any]]] = {}


def _flag(value: Any) -> bool | None:
    """宽松布尔解析：None 保持 None（走服务端 env 缺省）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def _register(method: str) -> Callable:
    def decorator(fn: Callable) -> Callable:
        METHOD_HANDLERS[method] = fn
        return fn

    return decorator


@_register("CreateGraph")
def _create_graph(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.create_graph(
        graph_id_value=str(p.get("graphId") or ""),
        name=str(p.get("name") or ""),
        kb_id=str(p.get("kbId") or ""),
        tenant_id=str(p.get("tenantId") or ""),
        owner_id=str(p.get("ownerId") or ""),
        schema=p.get("graphSchema"),
    )


@_register("GetGraph")
def _get_graph(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.get_graph(str(p.get("graphId") or ""))


@_register("DeleteGraph")
def _delete_graph(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.delete_graph(str(p.get("graphId") or ""))


@_register("ListGraphs")
def _list_graphs(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.list_graphs(tenant_id=str(p.get("tenantId") or ""), owner_id=str(p.get("ownerId") or ""))


@_register("BuildGraph")
def _build_graph(service, p: dict[str, Any]) -> dict[str, Any]:
    if p.get("text"):
        return service.build_from_text(
            str(p.get("graphId") or ""),
            text=str(p.get("text") or ""),
            doc_id=str(p.get("docId") or ""),
            title=str(p.get("title") or ""),
            llm=_flag(p.get("llm")),
        )
    return service.build_from_records(
        str(p.get("graphId") or ""),
        entities=p.get("entities") or [],
        relations=p.get("relations") or [],
        doc_id=str(p.get("docId") or ""),
    )


@_register("MergeRecords")
def _merge_records(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.merge_records(
        str(p.get("graphId") or ""),
        entities=p.get("entities") or [],
        relations=p.get("relations") or [],
        doc_id=str(p.get("docId") or ""),
    )


@_register("DeprecateDoc")
def _deprecate_doc(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.deprecate_doc(str(p.get("graphId") or ""), doc_id=str(p.get("docId") or ""))


@_register("Stat")
def _stat(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.stat(str(p.get("graphId") or ""))


@_register("ListNodes")
def _list_nodes(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.list_nodes(
        str(p.get("graphId") or ""),
        entity_type=str(p.get("entityType") or ""),
        name=str(p.get("name") or ""),
        page=int(p.get("page") or 1),
        page_size=int(p.get("pageSize") or 20),
    )


@_register("ListEdges")
def _list_edges(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.list_edges(
        str(p.get("graphId") or ""),
        relation_type=str(p.get("relationType") or ""),
        page=int(p.get("page") or 1),
        page_size=int(p.get("pageSize") or 20),
    )


@_register("Neighbors")
def _neighbors(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.neighbors(
        str(p.get("graphId") or ""), entity_id_value=str(p.get("entityId") or ""), depth=int(p.get("depth") or 1)
    )


@_register("Paths")
def _paths(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.paths(
        str(p.get("graphId") or ""),
        source_entity_id=str(p.get("sourceEntityId") or ""),
        target_entity_id=str(p.get("targetEntityId") or ""),
        max_depth=int(p.get("maxDepth") or 5),
    )


@_register("ExportGraph")
def _export_graph(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.export(str(p.get("graphId") or ""), format=str(p.get("format") or "jsonl"))


@_register("Sparql")
def _sparql(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.sparql(
        str(p.get("graphId") or ""),
        query=str(p.get("query") or ""),
        limit=int(p.get("limit") or 0),
    )


@_register("RunJob")
def _run_job(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.run_job(str(p.get("jobId") or ""))


@_register("GetJob")
def _get_job(service, p: dict[str, Any]) -> dict[str, Any]:
    return service.get_job(str(p.get("jobId") or ""))


# ---------- server ----------


def _make_handler(method: str, service: Any | None = None):
    def _call(request: Any, context: Any) -> Any:
        trace_id = request.trace_id or new_trace_id()
        try:
            data = json.loads(request.data_json or "{}")
            params = dict(data.get("params") or data)
            result = METHOD_HANDLERS[method](service or get_service(), params)
            payload = ok(trace_id, result)
        except GraphEngineError as exc:
            payload = error(trace_id, exc)
        except Exception as exc:  # pragma: no cover
            payload = error(trace_id, exc)
        resp = Envelope()
        resp.trace_id = payload["traceId"]
        resp.err_code = payload["errCode"]
        resp.err_msg = payload["errMsg"]
        resp.data_json = json.dumps(payload.get("data"), ensure_ascii=False, default=str)
        return resp

    return _call


def build_grpc_server(service: Any | None = None) -> grpc.Server:
    """构造 gRPC server（不启动），供 serve_grpc 与测试复用。"""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    handlers = {
        method: grpc.unary_unary_rpc_method_handler(
            _make_handler(method, service), request_deserializer=_deser, response_serializer=_ser
        )
        for method in METHOD_HANDLERS
    }
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(SERVICE_NAME, handlers),))
    return server


def serve_grpc(host: str = "0.0.0.0", port: int = 50051, service: Any | None = None) -> int:
    """启动 gRPC 服务（阻塞），返回实际监听端口。"""
    server = build_grpc_server(service)
    bound_port = server.add_insecure_port(f"{host}:{port}")
    server.start()
    print(f"[graph-engine] gRPC listening on {host}:{bound_port} ({SERVICE_NAME})")
    server.wait_for_termination()
    return bound_port


# ---------- client ----------


def grpc_client(host: str = "localhost", port: int = 50051) -> Callable[..., dict[str, Any]]:
    """返回一个通用调用函数：client(method, params, trace_id="") → envelope dict。"""
    channel = grpc.insecure_channel(f"{host}:{port}")

    def call(method: str, params: dict[str, Any] | None = None, trace_id: str = "") -> dict[str, Any]:
        req = Envelope()
        req.trace_id = trace_id or new_trace_id()
        req.data_json = json.dumps({"params": params or {}}, ensure_ascii=False)
        stub = channel.unary_unary(
            f"/{SERVICE_NAME}/{method}", request_serializer=_ser, response_deserializer=_deser
        )
        resp = stub(req)
        return {
            "traceId": resp.trace_id,
            "errCode": resp.err_code,
            "errMsg": resp.err_msg,
            "data": json.loads(resp.data_json) if resp.data_json else None,
        }

    return call
