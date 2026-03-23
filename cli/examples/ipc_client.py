#!/usr/bin/env python3
"""Example IPC client — connect to maxBridge daemon and interact via JSON-RPC.

Usage:
    python examples/ipc_client.py                  # Unix socket (default)
    python examples/ipc_client.py --tcp             # TCP mode
    python examples/ipc_client.py --subscribe       # Subscribe to live messages
"""

import asyncio
import json
import os
import sys


async def send_request(reader, writer, method, params=None, req_id=1):
    """Send a JSON-RPC request and return the response."""
    request = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": req_id,
    }
    line = json.dumps(request) + "\n"
    writer.write(line.encode())
    await writer.drain()

    response_line = await reader.readline()
    return json.loads(response_line.decode())


async def subscribe_loop(reader, writer):
    """Subscribe and print all incoming messages."""
    # Send subscribe request
    resp = await send_request(reader, writer, "subscribe")
    print(f"Subscribe response: {resp}")

    print("Listening for messages... (Ctrl+C to stop)")
    while True:
        line = await reader.readline()
        if not line:
            print("Connection closed by server")
            break
        data = json.loads(line.decode())
        if data.get("method") == "message":
            msg = data["params"]
            print(f"\n{'='*60}")
            print(f"  Account:   {msg.get('account_id', '?')}")
            print(f"  Status:    {msg['status']}")
            print(f"  Chat:      {msg.get('chat_name') or msg['chat_id']}"
                  f" ({msg.get('chat_type', '?')})")
            print(f"  Sender:    {msg.get('sender_name') or msg.get('sender_id', '?')}"
                  f" [id:{msg.get('sender_id', '?')}]")
            print(f"  Text:      {msg.get('text', '')[:200]}")
            if msg.get("attachments"):
                print(f"  Attach:    {len(msg['attachments'])} item(s)")
            if msg.get("timestamp"):
                print(f"  Time:      {msg['timestamp']}")
            print(f"{'='*60}")


async def interactive_mode(reader, writer):
    """Interactive command mode."""
    # Get available methods
    resp = await send_request(reader, writer, "list_methods")
    methods = resp.get("result", {}).get("methods", [])
    print(f"Available methods: {', '.join(methods)}")

    # Ping
    resp = await send_request(reader, writer, "ping")
    print(f"Ping: {resp.get('result')}")

    # Status
    resp = await send_request(reader, writer, "status")
    print(f"Status: {resp.get('result')}")


async def main():
    use_tcp = "--tcp" in sys.argv
    subscribe = "--subscribe" in sys.argv

    if use_tcp:
        reader, writer = await asyncio.open_connection("127.0.0.1", 9100)
        print("Connected via TCP 127.0.0.1:9100")
    else:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        socket_path = os.path.join(runtime_dir, "maxbridge.sock")
        reader, writer = await asyncio.open_unix_connection(socket_path)
        print(f"Connected via Unix socket {socket_path}")

    try:
        if subscribe:
            await subscribe_loop(reader, writer)
        else:
            await interactive_mode(reader, writer)
    finally:
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
