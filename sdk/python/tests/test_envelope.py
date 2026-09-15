from __future__ import annotations

import pytest

from semantica_graph_sdk import Envelope
from semantica_graph_sdk.envelope import parse_envelope
from semantica_graph_sdk.errors import GraphEngineProtocolError


def test_parse_envelope_success():
    envelope = parse_envelope('{"traceId": "t1", "errCode": "0", "errMsg": "", "data": {"graphId": "g1"}}')
    assert envelope.ok
    assert envelope.err_code == "0"
    assert envelope.data == {"graphId": "g1"}
    assert envelope.trace_id == "t1"


def test_parse_envelope_business_error():
    envelope = parse_envelope('{"traceId": "t2", "errCode": "200404", "errMsg": "图谱不存在", "data": null}')
    assert not envelope.ok
    assert envelope.err_code == "200404"
    assert envelope.err_msg == "图谱不存在"
    assert envelope.data is None


def test_parse_envelope_invalid_json():
    with pytest.raises(GraphEngineProtocolError):
        parse_envelope("<html>502</html>")


def test_parse_envelope_missing_err_code():
    with pytest.raises(GraphEngineProtocolError):
        parse_envelope('{"data": {}}')


def test_envelope_dataclass_ok_defaults():
    assert Envelope(err_code="0").ok
    assert not Envelope(err_code="200500").ok
