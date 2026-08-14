"""Focused tests for the merged TUI error-log screen."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Label, RichLog

from maxbridge.ipc.server import IpcServer
from maxbridge.tui.screens import logs as logs_screen


def _write_record(path: Path, **overrides) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "version": 1,
        "timestamp": "2026-08-14T10:00:00+03:00",
        "level": "ERROR",
        "logger": "maxbridge.test",
        "message": "failed",
        "traceback": "Traceback\nRuntimeError: failed",
    }
    record.update(overrides)
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def test_durable_reader_handles_missing_empty_and_symlink(tmp_path):
    path = tmp_path / "logs/maxbridge-errors.log"
    missing = logs_screen.read_durable_errors(path)
    assert missing.entries == ()
    assert "пока не создан" in missing.status

    path.parent.mkdir()
    path.write_text("", encoding="utf-8")
    empty = logs_screen.read_durable_errors(path)
    assert empty.entries == ()
    assert "ошибок нет" in empty.status

    path.unlink()
    target = tmp_path / "outside.log"
    target.write_text("unchanged\n", encoding="utf-8")
    path.symlink_to(target)
    linked = logs_screen.read_durable_errors(path)
    assert linked.entries == ()
    assert "симлинком" in linked.status
    assert target.read_text(encoding="utf-8") == "unchanged\n"


def test_durable_reader_redacts_canonical_and_malformed_lines(tmp_path):
    path = tmp_path / "logs/maxbridge-errors.log"
    secret = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    _write_record(
        path,
        message=f"Authorization: Bearer hidden-bearer {secret}",
        traceback=f"password=trace-password\nRuntimeError: {secret}",
    )
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"raw auth_secret=raw-secret {secret}\n")

    result = logs_screen.read_durable_errors(path)

    assert len(result.entries) == 2
    rendered = "\n".join(entry.text for entry in result.entries)
    assert secret not in rendered
    assert "hidden-bearer" not in rendered
    assert "trace-password" not in rendered
    assert "raw-secret" not in rendered
    assert "[REDACTED]" in rendered
    assert "]]" not in rendered
    assert "некорректных строк: 1" in result.status


def test_durable_reader_reports_unreadable_without_exception_text(tmp_path, monkeypatch):
    path = tmp_path / "logs/maxbridge-errors.log"
    _write_record(path)
    original_open = logs_screen.os.open

    def denied(candidate, flags, *args, **kwargs):
        if Path(candidate) == path:
            raise PermissionError("password=must-not-render")
        return original_open(candidate, flags, *args, **kwargs)

    monkeypatch.setattr(logs_screen.os, "open", denied)
    result = logs_screen.read_durable_errors(path)

    assert result.entries == ()
    assert result.status == "FILE: недоступен (PermissionError)"
    assert "must-not-render" not in result.status


def test_durable_reader_ignores_incomplete_trailing_record(tmp_path):
    path = tmp_path / "logs/maxbridge-errors.log"
    path.parent.mkdir()
    path.write_text('{"version": 1, "message": "still writing"', encoding="utf-8")

    result = logs_screen.read_durable_errors(path)

    assert result.entries == ()
    assert result.status == "FILE: запись обновляется"


def test_unix_default_socket_matches_daemon_without_xdg_runtime_dir(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    assert logs_screen._unix_socket_path({}, tmp_path) == Path(
        IpcServer._default_socket_path()
    )


@pytest.mark.asyncio
async def test_unix_ipc_authenticates_then_reads_and_redacts_errors(tmp_path):
    socket_path = tmp_path / "maxbridge.sock"
    auth_secret = "ipc-auth-secret"
    response_secret = "987654321:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    requests = []

    async def handle(reader, writer):
        auth = json.loads(await reader.readline())
        requests.append(auth)
        writer.write(
            json.dumps(
                {"jsonrpc": "2.0", "id": auth["id"], "result": {"authenticated": True}}
            ).encode()
            + b"\n"
        )
        await writer.drain()
        errors = json.loads(await reader.readline())
        requests.append(errors)
        writer.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": errors["id"],
                    "result": [
                        {
                            "timestamp": 1_723_625_200,
                            "kind": "rpc",
                            "method": "send_message",
                            "account_id": "personal",
                            "error": f"token={response_secret}",
                            "traceback": f"RuntimeError: {response_secret}",
                        }
                    ],
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path=socket_path)
    try:
        result = await logs_screen.fetch_runtime_errors(
            {
                "transport": "unix",
                "socket_path": str(socket_path),
                "auth_secret": auth_secret,
            },
            tmp_path,
        )
    finally:
        server.close()
        await server.wait_closed()

    assert [request["method"] for request in requests] == ["auth", "errors"]
    assert requests[0]["params"] == {"secret": auth_secret}
    assert requests[1]["params"] == {"limit": 100}
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.kind == "rpc"
    assert entry.method == "send_message"
    assert entry.account == "personal"
    assert response_secret not in entry.text
    assert "[REDACTED]" in entry.text


@pytest.mark.asyncio
async def test_loopback_tcp_reads_empty_error_ring():
    requests = []

    async def handle(reader, writer):
        request = json.loads(await reader.readline())
        requests.append(request)
        writer.write(
            json.dumps(
                {"jsonrpc": "2.0", "id": request["id"], "result": {"errors": []}}
            ).encode()
            + b"\n"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        result = await logs_screen.fetch_runtime_errors(
            {"transport": "tcp", "tcp_host": "127.0.0.1", "tcp_port": port},
            Path("/unused"),
        )
    finally:
        server.close()
        await server.wait_closed()

    assert requests[0]["method"] == "errors"
    assert result.entries == ()
    assert result.status == "RUNTIME: ошибок нет"


@pytest.mark.asyncio
async def test_ipc_malformed_unavailable_and_non_loopback_states(tmp_path):
    async def malformed(_reader, writer):
        writer.write(b"not-json\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(malformed, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        malformed_result = await logs_screen.fetch_runtime_errors(
            {"transport": "tcp", "tcp_host": "127.0.0.1", "tcp_port": port},
            tmp_path,
        )
    finally:
        server.close()
        await server.wait_closed()

    unavailable = await logs_screen.fetch_runtime_errors(
        {"transport": "unix", "socket_path": str(tmp_path / "missing.sock")},
        tmp_path,
    )
    rejected = await logs_screen.fetch_runtime_errors(
        {"transport": "tcp", "tcp_host": "example.invalid", "tcp_port": 9100},
        tmp_path,
    )

    assert "ValueError" in malformed_result.status
    assert "недоступен" in unavailable.status
    assert "ValueError" in rejected.status
    assert auth_secret_absent(malformed_result.status + unavailable.status + rejected.status)


def auth_secret_absent(text: str) -> bool:
    return "secret" not in text.lower() and "password" not in text.lower()


def test_merge_deduplicates_identical_source_records_and_sorts_newest_last():
    old = logs_screen.LogEntry("FILE", "old", 1.0, "old")
    duplicate = logs_screen.LogEntry("FILE", "old", 1.0, "old")
    new = logs_screen.LogEntry("RUNTIME", "new", 2.0, "new")

    merged = logs_screen.merge_entries((new,), (old, duplicate))

    assert merged == (old, new)


def test_format_entry_redacts_again_at_render_boundary():
    secret = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    entry = logs_screen.LogEntry(
        source="RUNTIME",
        timestamp="now",
        sort_time=None,
        text=f"Authorization: Bearer render-bearer {secret}",
        kind="attachment",
        method="upload",
        account="personal",
    )

    rendered = logs_screen.format_entry(entry)

    assert secret not in rendered
    assert "render-bearer" not in rendered
    assert "RUNTIME" in rendered
    assert "kind=attachment" in rendered
    assert "method=upload" in rendered
    assert "account=personal" in rendered


class _FakeLog:
    def __init__(self):
        self.clear_calls = 0
        self.writes = []
        self.scroll_calls = 0

    def clear(self):
        self.clear_calls += 1

    def write(self, text):
        self.writes.append(text)

    def scroll_end(self, animate=False):
        assert animate is False
        self.scroll_calls += 1


class _FakeLabel:
    def __init__(self):
        self.values = []

    def update(self, value):
        self.values.append(value)


def test_screen_applies_stable_snapshot_without_duplicate_accumulation():
    log = _FakeLog()
    label = _FakeLabel()
    fake_screen = SimpleNamespace(
        _last_snapshot_key=None,
        query_one=lambda selector, _widget: label if selector == "#logs-status" else log,
    )
    snapshot = logs_screen.LogsSnapshot(
        entries=(logs_screen.LogEntry("FILE", "now", 1.0, "failure"),),
        file_status="FILE: 1",
        runtime_status="RUNTIME: демон/IPC недоступен (FileNotFoundError)",
    )

    logs_screen.LogsScreen._apply_snapshot(fake_screen, snapshot)
    logs_screen.LogsScreen._apply_snapshot(fake_screen, snapshot)

    assert log.clear_calls == 1
    assert len(log.writes) == 1
    assert log.scroll_calls == 1
    assert len(label.values) == 2
    assert "FILE: 1" in label.values[-1]
    assert "недоступен" in label.values[-1]


def test_screen_refreshes_every_two_seconds_and_has_manual_and_back_keys():
    calls = []
    fake_screen = SimpleNamespace(
        set_interval=lambda interval, callback: calls.append((interval, callback)),
        action_refresh=lambda: calls.append("refresh"),
    )

    logs_screen.LogsScreen.on_mount(fake_screen)

    assert calls[0][0] == 2
    assert calls[0][1] == fake_screen.action_refresh
    assert calls[1] == "refresh"
    keys = {binding.key for binding in logs_screen.LogsScreen.BINDINGS}
    assert keys == {"escape", "r"}
    assert RichLog is not None and Label is not None


def test_screen_does_not_start_overlapping_refresh_workers():
    refresh = SimpleNamespace(calls=0)

    def start_refresh():
        refresh.calls += 1

    fake_screen = SimpleNamespace(_refresh_active=True, _refresh=start_refresh)
    logs_screen.LogsScreen.action_refresh(fake_screen)
    assert refresh.calls == 0

    fake_screen._refresh_active = False
    logs_screen.LogsScreen.action_refresh(fake_screen)
    assert refresh.calls == 1
    assert fake_screen._refresh_active is True
