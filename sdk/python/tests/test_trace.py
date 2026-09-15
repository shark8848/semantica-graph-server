from __future__ import annotations

from semantica_graph_sdk.trace import ensure_trace_id, generate_trace_id


def test_generate_trace_id_is_23_digits():
    trace_id = generate_trace_id()
    assert len(trace_id) == 23
    assert trace_id.isdigit()


def test_generate_trace_id_is_unique():
    assert len({generate_trace_id() for _ in range(50)}) == 50


def test_ensure_trace_id_reuses_explicit_value():
    assert ensure_trace_id("explicit-trace") == "explicit-trace"
    assert len(ensure_trace_id(None)) == 23
    assert len(ensure_trace_id("")) == 23
