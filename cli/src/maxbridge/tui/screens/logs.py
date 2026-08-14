"""Durable and runtime error-log viewer for the Textual UI."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Label, RichLog

from maxbridge.tui.styles import CYBERPUNK_CSS
from maxbridge.utils.logger import error_log_path, redact_secrets

_CONNECT_TIMEOUT = 1.0
_READ_TIMEOUT = 1.5
_CLOSE_TIMEOUT = 0.5
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_RESPONSE_FRAMES = 16
_ERROR_LIMIT = 100


@dataclass(frozen=True)
class LogEntry:
    source: str
    timestamp: str
    sort_time: float | None
    text: str
    level: str = ""
    logger: str = ""
    kind: str = ""
    method: str = ""
    account: str = ""

    @property
    def fingerprint(self) -> tuple[str, ...]:
        return (
            self.source,
            self.timestamp,
            self.text,
            self.level,
            self.logger,
            self.kind,
            self.method,
            self.account,
        )


@dataclass(frozen=True)
class SourceResult:
    entries: tuple[LogEntry, ...]
    status: str


@dataclass(frozen=True)
class LogsSnapshot:
    entries: tuple[LogEntry, ...]
    file_status: str
    runtime_status: str


class _RpcResponseError(RuntimeError):
    pass


def _timestamp_parts(value: object) -> tuple[str, float | None]:
    if value is None or value == "":
        return "время неизвестно", None
    try:
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            local = datetime.fromtimestamp(timestamp).astimezone()
        else:
            raw = str(value).strip()
            local = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if local.tzinfo is None:
                local = local.astimezone()
            else:
                local = local.astimezone()
        return local.strftime("%Y-%m-%d %H:%M:%S"), local.timestamp()
    except (OSError, OverflowError, TypeError, ValueError):
        return redact_secrets(value), None


def _safe_text(value: object) -> str:
    if isinstance(value, str):
        return redact_secrets(value)
    try:
        return redact_secrets(json.dumps(value, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError):
        return redact_secrets(value)


def _file_entry(record: dict[str, Any]) -> LogEntry:
    timestamp, sort_time = _timestamp_parts(record.get("timestamp"))
    text = "\n".join(
        part
        for part in (
            _safe_text(record.get("message", "")).strip(),
            _safe_text(record.get("traceback", "")).strip(),
        )
        if part
    ) or "Неизвестная ошибка"
    return LogEntry(
        source="FILE",
        timestamp=timestamp,
        sort_time=sort_time,
        text=redact_secrets(text),
        level=_safe_text(record.get("level", "ERROR")),
        logger=_safe_text(record.get("logger", "")),
    )


def read_durable_errors(path: Path) -> SourceResult:
    """Read every durable record without following a symlink."""
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        return SourceResult((), "FILE: файл пока не создан")
    except OSError as exc:
        return SourceResult((), f"FILE: недоступен ({type(exc).__name__})")

    if stat.S_ISLNK(path_stat.st_mode):
        return SourceResult((), "FILE: путь является симлинком; чтение запрещено")
    if not stat.S_ISREG(path_stat.st_mode):
        return SourceResult((), "FILE: путь не является обычным файлом")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        stream = os.fdopen(descriptor, "r", encoding="utf-8", errors="replace")
        descriptor = -1
        with stream:
            lines = stream.readlines()
    except OSError as exc:
        return SourceResult((), f"FILE: недоступен ({type(exc).__name__})")
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    partial_record = bool(lines and not lines[-1].endswith("\n"))
    if partial_record:
        lines.pop()

    entries: list[LogEntry] = []
    malformed = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            malformed += 1
            entries.append(
                LogEntry(
                    source="FILE",
                    timestamp="время неизвестно",
                    sort_time=None,
                    text=redact_secrets(line.rstrip("\n")),
                    level="ERROR",
                )
            )
            continue
        if not isinstance(record, dict):
            malformed += 1
            entries.append(
                LogEntry(
                    source="FILE",
                    timestamp="время неизвестно",
                    sort_time=None,
                    text=_safe_text(record),
                    level="ERROR",
                )
            )
            continue
        entries.append(_file_entry(record))

    if not entries and partial_record:
        return SourceResult((), "FILE: запись обновляется")
    if not entries:
        return SourceResult((), "FILE: ошибок нет")
    suffix_parts = []
    if malformed:
        suffix_parts.append(f"некорректных строк: {malformed}")
    if partial_record:
        suffix_parts.append("запись обновляется")
    suffix = f", {', '.join(suffix_parts)}" if suffix_parts else ""
    return SourceResult(tuple(entries), f"FILE: {len(entries)}{suffix}")


def _runtime_entry(record: object) -> LogEntry:
    if not isinstance(record, dict):
        return LogEntry(
            source="RUNTIME",
            timestamp="время неизвестно",
            sort_time=None,
            text=_safe_text(record),
        )

    timestamp_value = next(
        (record[key] for key in ("timestamp", "time", "created_at", "ts") if key in record),
        None,
    )
    timestamp, sort_time = _timestamp_parts(timestamp_value)
    pieces: list[str] = []
    for key in ("message", "error", "traceback", "trace"):
        value = record.get(key)
        if value not in (None, ""):
            text = _safe_text(value).strip()
            if text and text not in pieces:
                pieces.append(text)
    if not pieces:
        pieces.append(_safe_text(record))
    return LogEntry(
        source="RUNTIME",
        timestamp=timestamp,
        sort_time=sort_time,
        text=redact_secrets("\n".join(pieces)),
        kind=_safe_text(record.get("kind") or record.get("type") or ""),
        method=_safe_text(record.get("method") or record.get("rpc_method") or ""),
        account=_safe_text(
            record.get("account_id") or record.get("account") or record.get("aid") or ""
        ),
    )


async def _read_rpc_response(
    reader: asyncio.StreamReader, request_id: int
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _READ_TIMEOUT
    for _ in range(_MAX_RESPONSE_FRAMES):
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        line = await asyncio.wait_for(reader.readline(), timeout=remaining)
        if not line:
            raise ConnectionError("IPC closed before response")
        try:
            response = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("malformed IPC response") from exc
        if not isinstance(response, dict):
            raise ValueError("malformed IPC response")
        if response.get("id") != request_id:
            continue
        if response.get("error") is not None:
            raise _RpcResponseError
        if "result" not in response:
            raise ValueError("malformed IPC response")
        return response
    raise ValueError("matching IPC response not found")


async def _rpc_call(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: dict[str, object],
) -> object:
    request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }
    writer.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
    await asyncio.wait_for(writer.drain(), timeout=_READ_TIMEOUT)
    response = await _read_rpc_response(reader, request_id)
    return response["result"]


def _unix_socket_path(ipc_config: dict[str, object], runtime_root: Path) -> Path:
    configured = next(
        (
            ipc_config[key]
            for key in ("socket_path", "unix_path", "unix_socket", "path")
            if ipc_config.get(key)
        ),
        None,
    )
    if configured is None:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        return Path(runtime_dir) / "maxbridge.sock"
    path = Path(str(configured))
    return path if path.is_absolute() else runtime_root / path


def _loopback_host(value: object) -> str:
    host = str(value or "127.0.0.1").strip()
    if host.lower() == "localhost":
        return "127.0.0.1"
    try:
        if ipaddress.ip_address(host).is_loopback:
            return host
    except ValueError:
        pass
    raise ValueError("TCP IPC host is not loopback")


async def fetch_runtime_errors(
    ipc_config: dict[str, object], runtime_root: Path
) -> SourceResult:
    """Fetch the current daemon error ring without logging connection failures."""
    writer: asyncio.StreamWriter | None = None
    try:
        transport = str(ipc_config.get("transport", "unix")).strip().lower()
        if transport == "unix":
            socket_path = _unix_socket_path(ipc_config, runtime_root)
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    str(socket_path), limit=_MAX_RESPONSE_BYTES
                ),
                timeout=_CONNECT_TIMEOUT,
            )
        elif transport == "tcp":
            host = _loopback_host(ipc_config.get("tcp_host"))
            port = int(ipc_config.get("tcp_port", 9100))
            if not 1 <= port <= 65535:
                raise ValueError("invalid TCP IPC port")
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=host, port=port, limit=_MAX_RESPONSE_BYTES
                ),
                timeout=_CONNECT_TIMEOUT,
            )
        else:
            return SourceResult((), "RUNTIME: неизвестный IPC transport")

        auth_secret = ipc_config.get("auth_secret")
        request_id = 1
        if auth_secret:
            auth_result = await _rpc_call(
                reader,
                writer,
                request_id,
                "auth",
                {"secret": str(auth_secret)},
            )
            if auth_result is False or (
                isinstance(auth_result, dict)
                and auth_result.get("authenticated") is False
            ):
                return SourceResult((), "RUNTIME: ошибка аутентификации IPC")
            request_id += 1

        result = await _rpc_call(
            reader, writer, request_id, "errors", {"limit": _ERROR_LIMIT}
        )
        if isinstance(result, dict):
            result = result.get("errors")
        if not isinstance(result, list):
            return SourceResult((), "RUNTIME: некорректный ответ IPC")
        entries = tuple(_runtime_entry(record) for record in result)
        if not entries:
            return SourceResult((), "RUNTIME: ошибок нет")
        return SourceResult(entries, f"RUNTIME: {len(entries)}")
    except _RpcResponseError:
        return SourceResult((), "RUNTIME: JSON-RPC вернул ошибку")
    except (OSError, asyncio.TimeoutError, ConnectionError) as exc:
        return SourceResult(
            (), f"RUNTIME: демон/IPC недоступен ({type(exc).__name__})"
        )
    except (TypeError, ValueError) as exc:
        return SourceResult((), f"RUNTIME: некорректная конфигурация/ответ ({type(exc).__name__})")
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=_CLOSE_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                pass


def merge_entries(*sources: tuple[LogEntry, ...]) -> tuple[LogEntry, ...]:
    """Merge source snapshots while retaining one copy of identical records."""
    entries: list[LogEntry] = []
    seen: set[tuple[str, ...]] = set()
    for source in sources:
        for entry in source:
            if entry.fingerprint in seen:
                continue
            seen.add(entry.fingerprint)
            entries.append(entry)
    entries.sort(
        key=lambda entry: (
            entry.sort_time is not None,
            entry.sort_time if entry.sort_time is not None else 0.0,
        )
    )
    return tuple(entries)


async def load_logs_snapshot(
    runtime_root: Path, ipc_config: dict[str, object]
) -> LogsSnapshot:
    file_result, runtime_result = await asyncio.gather(
        asyncio.to_thread(read_durable_errors, error_log_path(runtime_root)),
        fetch_runtime_errors(ipc_config, runtime_root),
    )
    return LogsSnapshot(
        entries=merge_entries(file_result.entries, runtime_result.entries),
        file_status=file_result.status,
        runtime_status=runtime_result.status,
    )


def format_entry(entry: LogEntry) -> str:
    """Render one already-redacted entry as plain text."""
    metadata = [entry.source, entry.timestamp]
    if entry.level:
        metadata.append(entry.level)
    if entry.logger:
        metadata.append(entry.logger)
    if entry.kind:
        metadata.append(f"kind={entry.kind}")
    if entry.method:
        metadata.append(f"method={entry.method}")
    if entry.account:
        metadata.append(f"account={entry.account}")
    return f"[{' | '.join(metadata)}]\n{redact_secrets(entry.text)}"


class LogsScreen(Screen):
    """Auto-refreshing merged view of durable and daemon errors."""

    CSS = CYBERPUNK_CSS
    BINDINGS = [
        Binding("escape", "go_back", "Назад"),
        Binding("r", "refresh", "Обновить"),
    ]

    def __init__(self, config: dict[str, object], runtime_root: Path) -> None:
        super().__init__()
        self._ipc_config = dict(config.get("ipc") or {})
        self._runtime_root = runtime_root
        self._last_snapshot_key: tuple[object, ...] | None = None
        self._refresh_active = False

    def compose(self) -> ComposeResult:
        yield Label("  📋 ЛОГИ ОШИБОК", classes="screen-title")
        yield Label("Загрузка...", id="logs-status")
        yield RichLog(id="error-log", wrap=True, markup=False)
        yield Label("[dim]R=Обновить  ESC=Назад[/dim]", classes="hint")

    def on_mount(self) -> None:
        self.set_interval(2, self.action_refresh)
        self.action_refresh()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        if self._refresh_active:
            return
        self._refresh_active = True
        self._refresh()

    @work(thread=False, exclusive=True, group="logs-refresh", exit_on_error=False)
    async def _refresh(self) -> None:
        try:
            snapshot = await load_logs_snapshot(self._runtime_root, self._ipc_config)
            self._apply_snapshot(snapshot)
        finally:
            self._refresh_active = False

    def _apply_snapshot(self, snapshot: LogsSnapshot) -> None:
        status = f"{snapshot.file_status}  |  {snapshot.runtime_status}"
        self.query_one("#logs-status", Label).update(status)
        snapshot_key: tuple[object, ...] = (
            snapshot.file_status,
            snapshot.runtime_status,
            *(entry.fingerprint for entry in snapshot.entries),
        )
        if snapshot_key == self._last_snapshot_key:
            return
        self._last_snapshot_key = snapshot_key

        log = self.query_one("#error-log", RichLog)
        log.clear()
        if not snapshot.entries:
            log.write("История ошибок отсутствует.")
            return
        for entry in snapshot.entries:
            log.write(format_entry(entry) + "\n")
        log.scroll_end(animate=False)
