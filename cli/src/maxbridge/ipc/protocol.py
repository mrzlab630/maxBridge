"""JSON-RPC 2.0 protocol implementation."""

import json
import logging
from typing import Any

logger = logging.getLogger("maxbridge.ipc.protocol")

JSONRPC_VERSION = "2.0"


class JsonRpcError:
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603


def parse_request(raw: str) -> tuple[str | None, str | None, dict[str, Any], int | None]:
    """Parse a JSON-RPC 2.0 request string.

    Returns (method, error_message, params, request_id).
    On parse error: method=None, error_message set.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"Parse error: {e}", {}, None

    if not isinstance(data, dict):
        return None, "Invalid request: not an object", {}, None

    req_id = data.get("id")
    method = data.get("method")

    if not method or not isinstance(method, str):
        return None, "Invalid request: missing method", {}, req_id

    params = data.get("params", {})
    if not isinstance(params, dict):
        params = {}

    return method, None, params, req_id


def make_response(result: Any, req_id: int | None = None) -> str:
    """Build a JSON-RPC 2.0 success response."""
    return json.dumps({
        "jsonrpc": JSONRPC_VERSION,
        "result": result,
        "id": req_id,
    })


def make_error(code: int, message: str, req_id: int | None = None,
               data: Any = None) -> str:
    """Build a JSON-RPC 2.0 error response."""
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return json.dumps({
        "jsonrpc": JSONRPC_VERSION,
        "error": error,
        "id": req_id,
    })


def make_notification(method: str, params: dict[str, Any]) -> str:
    """Build a JSON-RPC 2.0 notification (no id, server -> client push)."""
    return json.dumps({
        "jsonrpc": JSONRPC_VERSION,
        "method": method,
        "params": params,
    })
