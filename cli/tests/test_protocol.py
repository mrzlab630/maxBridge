"""Tests for JSON-RPC 2.0 protocol."""

import json

from maxbridge.ipc.protocol import (
    JsonRpcError,
    make_error,
    make_notification,
    make_response,
    parse_request,
)


class TestParseRequest:
    def test_valid_request(self):
        raw = json.dumps({"jsonrpc": "2.0", "method": "ping", "id": 1})
        method, err, params, req_id = parse_request(raw)
        assert method == "ping"
        assert err is None
        assert params == {}
        assert req_id == 1

    def test_with_params(self):
        raw = json.dumps({"jsonrpc": "2.0", "method": "send", "params": {"a": 1}, "id": 2})
        method, err, params, req_id = parse_request(raw)
        assert method == "send"
        assert params == {"a": 1}

    def test_invalid_json(self):
        method, err, params, req_id = parse_request("{bad json")
        assert method is None
        assert "Parse error" in err

    def test_missing_method(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 1})
        method, err, params, req_id = parse_request(raw)
        assert method is None
        assert "missing method" in err

    def test_not_object(self):
        method, err, params, req_id = parse_request('"string"')
        assert method is None
        assert "not an object" in err

    def test_non_dict_params_ignored(self):
        raw = json.dumps({"jsonrpc": "2.0", "method": "x", "params": [1, 2], "id": 1})
        method, err, params, req_id = parse_request(raw)
        assert params == {}


class TestMakeResponse:
    def test_success(self):
        resp = make_response({"pong": True}, 1)
        data = json.loads(resp)
        assert data["jsonrpc"] == "2.0"
        assert data["result"]["pong"] is True
        assert data["id"] == 1


class TestMakeError:
    def test_error(self):
        resp = make_error(JsonRpcError.METHOD_NOT_FOUND, "Not found", 5)
        data = json.loads(resp)
        assert data["error"]["code"] == -32601
        assert data["error"]["message"] == "Not found"
        assert data["id"] == 5

    def test_error_with_data(self):
        resp = make_error(-1, "err", 1, data={"detail": "x"})
        data = json.loads(resp)
        assert data["error"]["data"]["detail"] == "x"


class TestMakeNotification:
    def test_notification(self):
        note = make_notification("message", {"chat_id": 1})
        data = json.loads(note)
        assert "id" not in data
        assert data["method"] == "message"
        assert data["params"]["chat_id"] == 1
