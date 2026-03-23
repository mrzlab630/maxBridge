"""MCP-compatible schema endpoint for AI agent discovery.

Returns a complete machine-readable description of all available
JSON-RPC methods, their parameters, return types, and usage examples.
"""

from typing import Any

from maxbridge import __version__

# Schema version follows semver independently of the app version
_SCHEMA_VERSION = "1.0.0"


def get_mcp_schema() -> dict[str, Any]:
    """Return the full MCP tool schema for maxBridge."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "server": {
            "name": "maxBridge",
            "version": __version__,
            "description": (
                "MAX Messenger bridge daemon. Connects to MAX (Russian messenger by VK) "
                "as a user account via WebSocket, receives all messages in real-time, and "
                "exposes a JSON-RPC 2.0 IPC interface for external applications. "
                "Supports multiple simultaneous accounts with encrypted session storage."
            ),
            "protocol": "jsonrpc-2.0-ndjson",
            "transport": ["unix_socket", "tcp"],
        },
        "capabilities": [
            "multi_account",
            "realtime_messages",
            "message_history",
            "send_messages",
            "user_resolution",
            "chat_resolution",
            "encrypted_sessions",
            "push_notifications",
        ],
        "methods": _build_methods_schema(),
        "notifications": _build_notifications_schema(),
        "errors": _build_errors_schema(),
        "examples": _build_examples(),
    }


def _build_methods_schema() -> list[dict[str, Any]]:
    return [
        {
            "name": "ping",
            "description": "Health check. Returns pong if the daemon is alive.",
            "params": [],
            "returns": {"type": "object", "properties": {"pong": {"type": "boolean"}}},
        },
        {
            "name": "health",
            "description": (
                "Detailed health check. Returns system status (healthy/degraded/unhealthy), "
                "connected account count, uptime, and error rate."
            ),
            "params": [],
            "returns": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["healthy", "degraded", "unhealthy", "no_accounts"],
                    },
                    "accounts_connected": {"type": "integer"},
                    "accounts_total": {"type": "integer"},
                    "uptime_seconds": {"type": "number"},
                    "error_rate": {"type": "number"},
                    "recent_errors": {"type": "integer"},
                },
            },
        },
        {
            "name": "status",
            "description": "Returns status of all accounts and subscriber count.",
            "params": [],
            "returns": {
                "type": "object",
                "properties": {
                    "accounts": {
                        "type": "object",
                        "description": "Map of account_id -> {connected, phone, has_session}",
                    },
                    "subscribers": {"type": "integer"},
                },
            },
        },
        {
            "name": "accounts",
            "description": "Returns detailed status of all registered MAX accounts.",
            "params": [],
            "returns": {
                "type": "object",
                "description": (
                    "Map of account_id -> {connected: bool, phone: str, has_session: bool}"
                ),
            },
        },
        {
            "name": "stats",
            "description": (
                "Returns runtime statistics: message counts (received/delivered/dropped), "
                "RPC call counts, error counts, connection reconnects, and uptime."
            ),
            "params": [],
            "returns": {
                "type": "object",
                "properties": {
                    "uptime_seconds": {"type": "number"},
                    "started_at": {"type": "number", "description": "Unix epoch"},
                    "messages": {
                        "type": "object",
                        "properties": {
                            "received": {"type": "object"},
                            "delivered": {"type": "object"},
                            "dropped": {"type": "object"},
                            "total_received": {"type": "integer"},
                            "total_delivered": {"type": "integer"},
                            "total_dropped": {"type": "integer"},
                        },
                    },
                    "rpc": {
                        "type": "object",
                        "properties": {
                            "calls": {"type": "object"},
                            "errors": {"type": "object"},
                            "total_calls": {"type": "integer"},
                            "total_errors": {"type": "integer"},
                        },
                    },
                    "connections": {
                        "type": "object",
                        "properties": {
                            "reconnects": {"type": "object"},
                            "ipc_total": {"type": "integer"},
                            "ipc_rejected": {"type": "integer"},
                        },
                    },
                },
            },
        },
        {
            "name": "errors",
            "description": "Returns recent error log entries, newest first.",
            "params": [
                {
                    "name": "limit",
                    "type": "integer",
                    "description": "Maximum number of errors to return (default: 50, max: 100)",
                    "required": False,
                    "default": 50,
                },
            ],
            "returns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "number"},
                        "account_id": {"type": "string"},
                        "method": {"type": "string"},
                        "error": {"type": "string"},
                    },
                },
            },
        },
        {
            "name": "send_message",
            "description": (
                "Send a text message to a chat via the specified MAX account. "
                "If account_id is omitted, the first registered account is used."
            ),
            "params": [
                {
                    "name": "account_id",
                    "type": "string",
                    "description": "Account to send from. Omit to use the default account.",
                    "required": False,
                },
                {
                    "name": "chat_id",
                    "type": "integer",
                    "description": "Target chat/channel/group numeric ID",
                    "required": True,
                },
                {
                    "name": "text",
                    "type": "string",
                    "description": "Message text (max 4096 characters)",
                    "required": True,
                },
            ],
            "returns": {
                "type": "object",
                "properties": {
                    "sent": {"type": "boolean"},
                    "account_id": {"type": "string"},
                    "result": {"type": "string"},
                },
            },
        },
        {
            "name": "get_history",
            "description": "Fetch message history for a chat.",
            "params": [
                {
                    "name": "account_id",
                    "type": "string",
                    "required": False,
                    "description": "Account to use. Omit for default.",
                },
                {
                    "name": "chat_id",
                    "type": "integer",
                    "required": True,
                    "description": "Chat ID to fetch history from",
                },
                {
                    "name": "count",
                    "type": "integer",
                    "required": False,
                    "default": 30,
                    "description": "Number of messages to fetch (1-200)",
                },
            ],
            "returns": {
                "type": "object",
                "description": "Raw MAX API response payload with message list",
            },
        },
        {
            "name": "get_chat_info",
            "description": "Get information about a chat, channel, or group by its numeric ID.",
            "params": [
                {
                    "name": "account_id",
                    "type": "string",
                    "required": False,
                    "description": "Account to use. Omit for default.",
                },
                {
                    "name": "chat_id",
                    "type": "integer",
                    "required": True,
                    "description": "Chat/channel/group numeric ID",
                },
            ],
            "returns": {
                "type": "object",
                "description": "Chat metadata (name, type, members count, etc.)",
            },
        },
        {
            "name": "get_user_info",
            "description": "Resolve user profiles by their numeric IDs.",
            "params": [
                {
                    "name": "account_id",
                    "type": "string",
                    "required": False,
                    "description": "Account to use. Omit for default.",
                },
                {
                    "name": "user_ids",
                    "type": "array",
                    "items": {"type": "integer"},
                    "required": True,
                    "description": "List of user IDs to resolve (max 100)",
                },
            ],
            "returns": {
                "type": "object",
                "description": "User profiles (name, avatar, status, etc.)",
            },
        },
        {
            "name": "subscribe",
            "description": (
                "Subscribe to real-time message push notifications. After calling this, "
                "the server will push JSON-RPC notifications with method='message' for "
                "every incoming/edited/deleted message across all connected accounts."
            ),
            "params": [],
            "returns": {"type": "object", "properties": {"subscribed": {"type": "boolean"}}},
        },
        {
            "name": "unsubscribe",
            "description": "Stop receiving push notifications.",
            "params": [],
            "returns": {"type": "object", "properties": {"unsubscribed": {"type": "boolean"}}},
        },
        {
            "name": "list_methods",
            "description": "List all available RPC method names.",
            "params": [],
            "returns": {
                "type": "object",
                "properties": {"methods": {"type": "array", "items": {"type": "string"}}},
            },
        },
        {
            "name": "schema",
            "description": (
                "Returns the full MCP-compatible schema describing all methods, "
                "parameters, return types, notifications, and usage examples. "
                "Designed for AI agent tool discovery."
            ),
            "params": [],
            "returns": {"type": "object", "description": "Complete MCP schema"},
        },
    ]


def _build_notifications_schema() -> list[dict[str, Any]]:
    return [
        {
            "method": "message",
            "description": (
                "Pushed to subscribed clients when a message is received, edited, or deleted "
                "on any connected MAX account. Contains the full unified message object."
            ),
            "params": {
                "type": "object",
                "properties": {
                    "account_id": {
                        "type": "string",
                        "description": "Which MAX account received this message",
                    },
                    "chat_id": {"type": "integer"},
                    "message_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["message_new", "message_edited", "message_deleted"],
                    },
                    "text": {"type": "string"},
                    "sender_id": {"type": "integer", "nullable": True},
                    "sender_name": {"type": "string", "nullable": True},
                    "chat_name": {"type": "string", "nullable": True},
                    "chat_type": {"type": "string", "nullable": True},
                    "timestamp": {
                        "type": "integer",
                        "nullable": True,
                        "description": "Message CID (millisecond timestamp)",
                    },
                    "attachments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "data": {"type": "object"},
                            },
                        },
                    },
                },
            },
        },
    ]


def _build_errors_schema() -> list[dict[str, Any]]:
    return [
        {
            "code": -32700, "name": "PARSE_ERROR",
            "description": "Invalid JSON or request too large",
        },
        {
            "code": -32600, "name": "INVALID_REQUEST",
            "description": "Missing method field",
        },
        {
            "code": -32601, "name": "METHOD_NOT_FOUND",
            "description": "Unknown method name",
        },
        {
            "code": -32602, "name": "INVALID_PARAMS",
            "description": "Wrong parameter types or values",
        },
        {
            "code": -32603, "name": "INTERNAL_ERROR",
            "description": "Server-side error or unauthorized",
        },
    ]


def _build_examples() -> list[dict[str, Any]]:
    return [
        {
            "title": "Connect and subscribe to all messages",
            "steps": [
                {
                    "description": "Connect via Unix socket",
                    "code": "socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/maxbridge.sock",
                },
                {
                    "description": "Subscribe to push notifications",
                    "request": '{"jsonrpc":"2.0","method":"subscribe","id":1}',
                    "response": '{"jsonrpc":"2.0","result":{"subscribed":true},"id":1}',
                },
                {
                    "description": "Incoming message notification (server push)",
                    "notification": (
                        '{"jsonrpc":"2.0","method":"message","params":'
                        '{"account_id":"default","chat_id":12345,"message_id":"abc",'
                        '"status":"message_new","text":"Hello!","sender_id":67890}}'
                    ),
                },
            ],
        },
        {
            "title": "Send a message from a specific account",
            "request": (
                '{"jsonrpc":"2.0","method":"send_message",'
                '"params":{"account_id":"work","chat_id":12345,"text":"Hello from bridge!"},'
                '"id":2}'
            ),
            "response": (
                '{"jsonrpc":"2.0","result":{"sent":true,"account_id":"work",'
                '"result":"..."},"id":2}'
            ),
        },
        {
            "title": "Check system health",
            "request": '{"jsonrpc":"2.0","method":"health","id":3}',
            "response": (
                '{"jsonrpc":"2.0","result":{"status":"healthy",'
                '"accounts_connected":2,"accounts_total":2,'
                '"uptime_seconds":3600.0,"error_rate":0.0,"recent_errors":0},"id":3}'
            ),
        },
        {
            "title": "Get runtime statistics",
            "request": '{"jsonrpc":"2.0","method":"stats","id":4}',
            "response": (
                '{"jsonrpc":"2.0","result":{"uptime_seconds":3600.0,'
                '"messages":{"total_received":150,"total_delivered":148,"total_dropped":2},'
                '"rpc":{"total_calls":42,"total_errors":1}},"id":4}'
            ),
        },
        {
            "title": "Get recent errors",
            "request": '{"jsonrpc":"2.0","method":"errors","params":{"limit":5},"id":5}',
            "response": (
                '{"jsonrpc":"2.0","result":[{"timestamp":1711234567.89,'
                '"account_id":"default","method":"send_message",'
                '"error":"chat_id and text are required"}],"id":5}'
            ),
        },
    ]
