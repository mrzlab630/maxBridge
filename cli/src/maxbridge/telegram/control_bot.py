"""Telegram control bot for maxBridge operational commands."""

import asyncio
import contextlib
import html
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from maxbridge.auth.qr_auth import (
    complete_qr_auth,
    poll_qr_until_scanned,
    render_qr_png,
    render_qr_text,
    request_qr_session,
)
from maxbridge.client.account_manager import AccountManager
from maxbridge.ipc.stats import StatsCollector
from maxbridge.telegram.config import TelegramConfig, load_telegram_config

logger = logging.getLogger("maxbridge.telegram.control_bot")

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_TEXT = 4096
_POLL_TIMEOUT = 30
_RETRY_DELAY = 3
_TRANSIENT_REQUEST_ERRORS = (
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    asyncio.TimeoutError,
)
_SECRET_IGNORED_CHARS = frozenset({
    "\u200b",  # zero width space
    "\u200c",  # zero width non-joiner
    "\u200d",  # zero width joiner
    "\u2060",  # word joiner
    "\ufeff",  # zero width no-break space / BOM
})


@dataclass
class _SecretReply:
    text: str
    message_id: int | None


@dataclass
class _PendingSecretPrompt:
    future: asyncio.Future[_SecretReply]
    account_id: str
    prompt_message_id: int | None
    kind: str


@dataclass
class _NormalizedSecret:
    value: str
    raw_length: int
    clean_length: int
    trimmed: bool
    zero_width_removed: int


class TelegramControlBot:
    """Receives Telegram commands and can trigger QR re-auth."""

    def __init__(self, account_manager: AccountManager, stats: StatsCollector,
                 bot_username: str = "msgMaxBridge_bot",
                 status_provider: Callable[[], dict[str, Any]] | None = None) -> None:
        self._manager = account_manager
        self._stats = stats
        self._bot_username = bot_username.lower().lstrip("@")
        self._status_provider = status_provider
        self._config: TelegramConfig | None = None
        self._http: aiohttp.ClientSession | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._offset = 0
        self._poll_task: asyncio.Task | None = None
        self._auth_tasks: dict[str, asyncio.Task] = {}
        self._pending_secret_prompts: dict[str, _PendingSecretPrompt] = {}

    @property
    def is_ready(self) -> bool:
        return bool(self._config and self._http and self._loop and self._poll_task)

    def set_status_provider(self,
                            provider: Callable[[], dict[str, Any]] | None) -> None:
        self._status_provider = provider

    async def start(self) -> None:
        if self._poll_task and not self._poll_task.done():
            return

        self._config = load_telegram_config()
        self._loop = asyncio.get_running_loop()
        if not self._config.enabled:
            logger.info("Telegram control bot disabled")
            return
        if not self._config.bot_token or not self._config.chat_id:
            logger.warning("Telegram control bot: missing bot_token or chat_id")
            return

        self._http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_POLL_TIMEOUT + 10),
        )
        self._offset = await self._bootstrap_offset()
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram control bot enabled (chat_id=%s)", self._config.chat_id)

    async def stop(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
        self._poll_task = None

        for task in list(self._auth_tasks.values()):
            task.cancel()
        if self._auth_tasks:
            await asyncio.gather(*self._auth_tasks.values(), return_exceptions=True)
        self._auth_tasks.clear()

        for pending in self._pending_secret_prompts.values():
            if not pending.future.done():
                pending.future.cancel()
        self._pending_secret_prompts.clear()

        if self._http and not self._http.closed:
            await self._http.close()
        self._http = None
        self._loop = None

    def request_auth_nowait(self, account_id: str, exc: Exception) -> None:
        """Schedule QR auth in Telegram after auth/session failure."""
        if not self._loop or self._loop.is_closed() or not self._config or not self._http:
            logger.warning("Telegram control bot is not ready for auth request: %s", account_id)
            return

        reason = str(exc) or exc.__class__.__name__

        def _schedule() -> None:
            self._start_auth_task(account_id, reason, requested_by="auto")

        self._loop.call_soon_threadsafe(_schedule)

    async def _bootstrap_offset(self) -> int:
        updates = await self._get_updates(timeout=0)
        if not updates:
            return 0
        return int(updates[-1].get("update_id", 0)) + 1

    async def _poll_loop(self) -> None:
        while True:
            try:
                updates = await self._get_updates(timeout=_POLL_TIMEOUT)
                if updates is None:
                    await asyncio.sleep(_RETRY_DELAY)
                    continue
                for update in updates:
                    update_id = int(update.get("update_id", 0))
                    if update_id:
                        self._offset = max(self._offset, update_id + 1)
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram control polling failed")
                await asyncio.sleep(_RETRY_DELAY)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("channel_post")
        if isinstance(message, dict):
            await self._handle_message(message)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        source_chat_id = str(message.get("chat", {}).get("id") or "")
        raw_text = str(message.get("text") or "")
        text = raw_text.strip()
        if text.startswith("/") and not self._is_allowed_chat(message):
            logger.warning(
                "Telegram control command ignored from unexpected chat_id=%s",
                source_chat_id,
            )
            return
        if not self._is_allowed_chat(message):
            return
        if not text.startswith("/"):
            self._try_consume_secret_reply(message, raw_text)
            return

        command, arg = self._parse_command(text)
        logger.info(
            "Telegram control command received: command=%s chat_id=%s",
            command or "?",
            source_chat_id,
        )
        if command == "auth":
            await self._handle_auth_command(arg, source_chat_id)
        elif command == "status":
            await self._send_message(self._format_status(), chat_id=source_chat_id)
        elif command == "update":
            await self._send_message(self._format_update(), chat_id=source_chat_id)

    async def _handle_auth_command(self, arg: str, source_chat_id: str) -> None:
        try:
            account_id = self._resolve_account_id(arg)
        except ValueError as exc:
            await self._send_message(
                f"❌ <b>Не удалось запустить авторизацию</b>\n<pre>{_esc(str(exc))}</pre>",
                chat_id=source_chat_id,
            )
            return

        if not self._start_auth_task(account_id, "Manual Telegram /auth request",
                                     requested_by="manual",
                                     target_chat_id=source_chat_id):
            await self._send_message(
                "ℹ️ <b>QR уже активен</b>\n"
                f"Аккаунт: <code>{_esc(account_id)}</code>",
                chat_id=source_chat_id,
            )
            return

        await self._send_message(
            "🔐 <b>Запрашиваю QR для повторной авторизации</b>\n"
            f"Аккаунт: <code>{_esc(account_id)}</code>",
            chat_id=source_chat_id,
        )

    def _start_auth_task(self, account_id: str, reason: str,
                         requested_by: str,
                         target_chat_id: str | None = None) -> bool:
        current = self._auth_tasks.get(account_id)
        if current and not current.done():
            return False

        task = asyncio.create_task(
            self._run_auth_flow(account_id, reason, requested_by, target_chat_id),
            name=f"telegram-auth-{account_id}",
        )
        self._auth_tasks[account_id] = task

        def _cleanup(done: asyncio.Task, aid: str = account_id) -> None:
            self._auth_tasks.pop(aid, None)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Telegram auth task crashed for '%s'", aid)

        task.add_done_callback(_cleanup)
        return True

    async def _run_auth_flow(self, account_id: str, reason: str,
                             requested_by: str,
                             target_chat_id: str | None) -> None:
        qr_message_id: int | None = None
        account = self._manager.require(account_id)
        reply_chat_id = target_chat_id or self._default_chat_id

        if account.is_connected:
            await self._send_message(
                "ℹ️ <b>QR не требуется</b>\n"
                f"Аккаунт: <code>{_esc(account_id)}</code> уже подключён.",
                chat_id=reply_chat_id,
            )
            return

        if requested_by == "auto":
            await self._send_message(
                self._format_auth_required_message(account_id, reason),
                chat_id=reply_chat_id,
            )

        try:
            client = await account.connection.create_raw_client()
            qr_session = await request_qr_session(client)
            qr_response = await self._send_qr_message(
                account_id,
                qr_session.qr_link,
                reason,
                chat_id=reply_chat_id,
            )
            qr_message_id = _message_id(qr_response)

            scanned = await poll_qr_until_scanned(client, qr_session, verbose=False)
            if not scanned:
                await self._send_message(
                    "⌛ <b>QR истёк</b>\n"
                    f"Аккаунт: <code>{_esc(account_id)}</code>\n"
                    f"Отправьте <code>/auth {_esc(account_id)}</code>, чтобы получить новый QR.",
                    chat_id=reply_chat_id,
                )
                return

            await complete_qr_auth(
                client,
                qr_session,
                account.session,
                password_provider=lambda challenge: self._request_max_password(
                    chat_id=reply_chat_id,
                    account_id=account_id,
                    challenge=challenge,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Telegram QR auth failed for '%s'", account_id)
            await self._send_message(
                "❌ <b>Не удалось выполнить QR авторизацию</b>\n"
                f"Аккаунт: <code>{_esc(account_id)}</code>\n"
                f"<pre>{_esc(str(exc))[:1000]}</pre>",
                chat_id=reply_chat_id,
            )
            return
        finally:
            with contextlib.suppress(Exception):
                await account.connection.release_raw_client()

        try:
            await account.connect()
        except Exception as exc:
            logger.exception("Reconnect after QR auth failed for '%s'", account_id)
            await self._send_message(
                "⚠️ <b>Сессия обновлена, но переподключение не удалось</b>\n"
                f"Аккаунт: <code>{_esc(account_id)}</code>\n"
                f"<pre>{_esc(str(exc))[:1000]}</pre>",
                chat_id=reply_chat_id,
            )
            return

        if qr_message_id is not None:
            await self._delete_message(qr_message_id, chat_id=reply_chat_id)

        await self._send_message(
            "✅ <b>Авторизация восстановлена</b>\n"
            f"Аккаунт: <code>{_esc(account_id)}</code>",
            chat_id=reply_chat_id,
        )

    def _try_consume_secret_reply(self, message: dict[str, Any], text: str) -> bool:
        chat_id = str(message.get("chat", {}).get("id") or "")
        pending = self._pending_secret_prompts.get(chat_id)
        if pending is None or pending.future.done() or not text.strip():
            return False

        reply = _SecretReply(
            text=text,
            message_id=_safe_int(message.get("message_id")),
        )
        pending.future.set_result(reply)
        logger.info(
            "Telegram secret reply received: kind=%s account=%s chat_id=%s",
            pending.kind,
            pending.account_id,
            chat_id,
        )
        return True

    async def _request_max_password(self, chat_id: str, account_id: str,
                                    challenge: dict[str, Any]) -> str:
        if not self._chat_allows_secret_input(chat_id):
            raise RuntimeError(
                "MAX запросил пароль аккаунта. Повторите /auth в личном чате с ботом.",
            )

        current = self._pending_secret_prompts.get(chat_id)
        if current is not None and not current.future.done():
            raise RuntimeError(
                "В этом чате уже ожидается ввод секретных данных. Завершите текущую авторизацию.",
            )

        future: asyncio.Future[_SecretReply] = asyncio.get_running_loop().create_future()
        prompt_response = await self._send_message(
            self._format_password_prompt(account_id, challenge),
            chat_id=chat_id,
        )
        prompt_message_id = _message_id(prompt_response)
        self._pending_secret_prompts[chat_id] = _PendingSecretPrompt(
            future=future,
            account_id=account_id,
            prompt_message_id=prompt_message_id,
            kind="max-password",
        )

        try:
            reply = await asyncio.wait_for(future, timeout=300)
            if reply.message_id is not None:
                with contextlib.suppress(Exception):
                    await self._delete_message(reply.message_id, chat_id=chat_id)
            normalized = _normalize_secret_text(reply.text)
            logger.info(
                "MAX password input normalized: account=%s chat_id=%s "
                "raw_len=%d clean_len=%d trimmed=%s zero_width_removed=%d",
                account_id,
                chat_id,
                normalized.raw_length,
                normalized.clean_length,
                normalized.trimmed,
                normalized.zero_width_removed,
            )
            if not normalized.value:
                raise RuntimeError("Пароль MAX не был получен.")
            return normalized.value
        except ValueError as exc:
            logger.warning(
                "MAX password input rejected: account=%s chat_id=%s reason=%s",
                account_id,
                chat_id,
                str(exc),
            )
            raise RuntimeError(str(exc)) from exc
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "Время ожидания пароля истекло. Повторите /auth и завершите вход заново.",
            ) from exc
        finally:
            self._pending_secret_prompts.pop(chat_id, None)
            if prompt_message_id is not None:
                with contextlib.suppress(Exception):
                    await self._delete_message(prompt_message_id, chat_id=chat_id)

    async def _get_updates(self, timeout: int) -> list[dict[str, Any]] | None:
        payload = {
            "timeout": timeout,
            "offset": self._offset,
            "allowed_updates": ["message", "channel_post"],
        }
        data = await self._call("getUpdates", payload)
        if not data:
            return None
        result = data.get("result")
        return result if isinstance(result, list) else None

    async def _send_message(self, text: str,
                            chat_id: str | None = None) -> dict[str, Any] | None:
        if not self._config or not self._default_chat_id:
            return None
        payload = {
            "chat_id": chat_id or self._default_chat_id,
            "text": text[:_MAX_TEXT],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        return await self._call("sendMessage", payload)

    async def _send_photo(self, file_bytes: bytes, filename: str, caption: str,
                          chat_id: str | None = None) -> dict[str, Any] | None:
        if not self._config or not self._default_chat_id or not self._http:
            return None

        form = aiohttp.FormData()
        form.add_field("chat_id", chat_id or self._default_chat_id)
        form.add_field("photo", file_bytes, filename=filename, content_type="image/png")
        form.add_field("caption", caption[:1024])
        form.add_field("parse_mode", "HTML")
        return await self._call_form("sendPhoto", form)

    async def _delete_message(self, message_id: int,
                              chat_id: str | None = None) -> None:
        if not self._config or not self._default_chat_id:
            return
        payload = {
            "chat_id": chat_id or self._default_chat_id,
            "message_id": message_id,
        }
        await self._call("deleteMessage", payload)

    async def _call(self, method: str,
                    payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self._config or not self._http:
            return None

        api = _API.format(token=self._config.bot_token, method=method)
        try:
            async with self._http.post(api, json=payload) as resp:
                return await self._parse_tg_response(method, resp)
        except asyncio.CancelledError:
            raise
        except _TRANSIENT_REQUEST_ERRORS as exc:
            logger.warning(
                "Telegram control bot %s transient request error: %s: %s",
                method,
                exc.__class__.__name__,
                exc,
            )
            return None
        except Exception:
            logger.exception("Telegram control bot %s request failed", method)
            return None

    async def _call_form(self, method: str,
                         form: aiohttp.FormData) -> dict[str, Any] | None:
        if not self._config or not self._http:
            return None

        api = _API.format(token=self._config.bot_token, method=method)
        try:
            async with self._http.post(api, data=form) as resp:
                return await self._parse_tg_response(method, resp)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram control bot %s request failed", method)
            return None

    async def _parse_tg_response(self, method: str,
                                 resp: aiohttp.ClientResponse) -> dict[str, Any] | None:
        data = await resp.json(content_type=None)
        if resp.status != 200 or not data.get("ok", False):
            logger.warning(
                "Telegram control bot %s failed: status=%s body=%s",
                method, resp.status, str(data)[:300],
            )
            return None
        return data

    def _is_allowed_chat(self, message: dict[str, Any]) -> bool:
        if not self._config:
            return False
        chat_id = str(message.get("chat", {}).get("id") or "")
        return chat_id in self._allowed_chat_ids

    @property
    def _allowed_chat_ids(self) -> set[str]:
        if not self._config:
            return set()
        return set(self._config.allowed_chat_ids or [])

    @property
    def _default_chat_id(self) -> str:
        if not self._config:
            return ""
        return self._config.chat_id

    def _parse_command(self, text: str) -> tuple[str, str]:
        command_text, _, remainder = text.partition(" ")
        command = command_text.split("@", 1)[0].lstrip("/").lower()
        if "@" in command_text and not command_text.lower().endswith(f"@{self._bot_username}"):
            return "", ""
        return command, remainder.strip()

    def _resolve_account_id(self, raw: str) -> str:
        account_ids = self._manager.account_ids
        if not account_ids:
            raise ValueError("Нет настроенных аккаунтов MAX.")
        if raw:
            if raw in account_ids:
                return raw
            raise ValueError(
                "Неизвестный аккаунт. Доступно: " + ", ".join(sorted(account_ids)),
            )
        if len(account_ids) == 1:
            return account_ids[0]
        raise ValueError(
            "Укажите аккаунт: /auth <account_id>. Доступно: "
            + ", ".join(sorted(account_ids)),
        )

    def _chat_allows_secret_input(self, chat_id: str) -> bool:
        return bool(chat_id) and not chat_id.startswith("-")

    def _format_update(self) -> str:
        status = self._manager.status()
        health = self._stats.get_health(status)
        stats = self._stats.get_stats()
        messages = stats.get("messages", {})

        lines = [
            "<b>Сводка maxBridge</b>",
            f"Статус: <b>{_esc(health['status'])}</b>",
            f"Uptime: {int(health['uptime_seconds'])}s",
            (
                "Сообщения: "
                f"{messages.get('total_received', 0)} получено / "
                f"{messages.get('total_delivered', 0)} доставлено / "
                f"{messages.get('total_dropped', 0)} отброшено"
            ),
            "<b>Аккаунты</b>",
        ]

        received = messages.get("received", {})
        delivered = messages.get("delivered", {})
        dropped = messages.get("dropped", {})
        for account_id, info in status.items():
            state = "online" if info.get("connected") else "offline"
            session = "yes" if info.get("has_session") else "no"
            lines.append(
                "• "
                f"<code>{_esc(account_id)}</code>: {state}, session={session}, "
                f"recv={received.get(account_id, 0)}, "
                f"sent={delivered.get(account_id, 0)}, "
                f"drop={dropped.get(account_id, 0)}",
            )

        errors = self._stats.get_errors(limit=3)
        if errors:
            lines.append("<b>Последние ошибки</b>")
            for item in errors:
                stamp = time.strftime("%H:%M:%S UTC", time.gmtime(item["timestamp"]))
                account = item.get("account_id") or "-"
                method = item.get("method") or "-"
                error = (item.get("error") or "unknown")[:160]
                lines.append(
                    "• "
                    f"{stamp} <code>{_esc(account)}</code> "
                    f"{_esc(method)}: {_esc(error)}",
                )

        return "\n".join(lines)[:_MAX_TEXT]

    def _format_password_prompt(self, account_id: str,
                                challenge: dict[str, Any]) -> str:
        hint = str(challenge.get("hint") or "").strip()
        email = str(challenge.get("email") or "").strip()
        lines = [
            "🔒 <b>MAX запросил пароль аккаунта</b>",
            f"Аккаунт: <code>{_esc(account_id)}</code>",
            "Отправьте следующим сообщением пароль от MAX.",
            "После получения бот попытается удалить и ваш ответ, и это сообщение.",
        ]
        if hint:
            lines.append(f"Подсказка: <code>{_esc(hint)}</code>")
        if email:
            lines.append(f"Email: <code>{_esc(_mask_email(email))}</code>")
        return "\n".join(lines)[:_MAX_TEXT]

    def _format_status(self) -> str:
        accounts = self._manager.status()
        health = self._stats.get_health(accounts)
        stats = self._stats.get_stats()
        runtime = self._status_provider() if self._status_provider else {}

        telegram = runtime.get("telegram", {})
        bridge = runtime.get("bridge", {})
        daemon = runtime.get("daemon", {})
        error_stats = stats.get("errors", {})
        attachment_stats = stats.get("attachments", {})
        connected = sum(1 for info in accounts.values() if info.get("connected"))
        with_session = sum(1 for info in accounts.values() if info.get("has_session"))

        title, subtitle = _status_headline(
            health["status"],
            daemon_running=daemon.get("running", True),
            connected=connected,
            with_session=with_session,
            total=len(accounts),
        )

        lines = [
            f"{_health_icon(health['status'])} <b>{_esc(title)}</b>",
        ]
        if subtitle:
            lines.append(_esc(subtitle))
        lines.extend([
            f"⏱️ Аптайм: {_esc(_format_uptime(health['uptime_seconds']))}",
            "",
            "<b>🧭 Общая картина</b>",
            f"• Состояние: {_esc(_health_label(health['status']))}",
            f"• Технический статус: <code>{_esc(health['status'])}</code>",
            f"• Демон: {_esc(_service_state(daemon.get('running', True)))}",
            (
                "• Остановка запрошена: "
                f"{_esc(_yes_no(daemon.get('shutdown_requested', False)))}"
            ),
            "",
            "<b>🤖 Telegram</b>",
            (
                "• Бот управления: "
                f"{_esc(_service_state(telegram.get('control_bot_ready', self.is_ready)))}"
            ),
            f"• Long polling: {_esc(_polling_state(self._poll_task))}",
            f"• Активных QR: {len(self._auth_tasks)}",
            (
                "• Форвардер: "
                f"{_esc(_service_state(telegram.get('forwarder_ready', False)))}"
            ),
            (
                "• Алерты в чат: "
                f"{_esc(_feature_state(telegram.get('alerts_enabled', False)))}"
            ),
            "",
            "<b>🌉 Мост</b>",
            (
                "• IPC-сервер: "
                f"{_esc(_service_state(bridge.get('ipc_running', False)))}"
            ),
            f"• Подключённых клиентов: {int(bridge.get('ipc_clients', 0))}",
            f"• Подписчиков: {int(bridge.get('subscribers', 0))}",
            (
                "• Сообщения: "
                f"{stats.get('messages', {}).get('total_received', 0)} получено / "
                f"{stats.get('messages', {}).get('total_delivered', 0)} отправлено / "
                f"{stats.get('messages', {}).get('total_dropped', 0)} потеряно"
            ),
            "",
            "<b>📎 Вложения</b>",
            (
                "• Отложенных уведомлений: "
                f"{int(attachment_stats.get('total_notifications', 0))}"
            ),
            (
                "• Успешно восстановлено: "
                f"{int(attachment_stats.get('total_reconciled', 0))}"
            ),
            (
                "• Не удалось восстановить: "
                f"{int(attachment_stats.get('total_unresolved', 0))}"
            ),
            "",
            "<b>🔐 Авторизация MAX</b>",
            f"• Аккаунтов онлайн: {connected} из {len(accounts)}",
            f"• С сохранённой сессией: {with_session} из {len(accounts)}",
        ])

        for account_id, info in accounts.items():
            qr_state = _qr_state_label(account_id in self._auth_tasks)
            lines.append(
                "• "
                f"<code>{_esc(account_id)}</code>: "
                f"{_esc(_connection_state(info.get('connected', False)))}; "
                f"{_esc(_session_state(info.get('has_session', False)))}; "
                f"QR: {_esc(qr_state)}",
            )

        lines.extend([
            "",
            "<b>⚠️ Ошибки</b>",
            f"• В буфере: {int(error_stats.get('buffered', 0))}",
            f"• RPC-ошибок: {int(error_stats.get('rpc_total', 0))}",
            f"• Ошибок логов: {int(error_stats.get('log_total', 0))}",
        ])

        errors = self._stats.get_errors(limit=5)
        if errors:
            lines.extend(self._format_error_lines(errors))
        else:
            lines.append("• Последние: нет")

        actions = _status_actions(
            daemon_running=daemon.get("running", True),
            polling_running=self._poll_task is not None and not self._poll_task.done(),
            connected=connected,
            with_session=with_session,
            total=len(accounts),
            active_qr=len(self._auth_tasks),
        )
        if actions:
            lines.extend(["", "<b>💡 Что сделать</b>"])
            lines.extend(f"• {_esc(item)}" for item in actions)

        return "\n".join(lines)[:_MAX_TEXT]

    def _format_error_lines(self, errors: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = ["• Последние события:"]
        for item in errors:
            stamp = time.strftime("%H:%M:%S UTC", time.gmtime(item["timestamp"]))
            kind = item.get("kind") or "unknown"
            account = item.get("account_id") or "-"
            method = item.get("method") or "-"
            error = (item.get("error") or "unknown")[:160]
            lines.append(
                f"• {stamp} {_esc(_error_kind_label(kind))} "
                f"<code>{_esc(account)}</code> {_esc(method)}: {_esc(error)}",
            )
        return lines

    def _format_auth_required_message(self, account_id: str, reason: str) -> str:
        return (
            "⚠️ <b>Требуется повторная авторизация MAX</b>\n"
            f"Аккаунт: <code>{_esc(account_id)}</code>\n"
            f"<pre>{_esc(reason)[:1000]}</pre>\n"
            f"Можно также вручную запросить новый QR: <code>/auth {_esc(account_id)}</code>"
        )

    async def _send_qr_message(self, account_id: str, qr_link: str, reason: str,
                               chat_id: str | None = None) -> dict[str, Any] | None:
        qr_png = render_qr_png(qr_link)
        if qr_png:
            caption = self._format_qr_caption(account_id, qr_link, reason)
            response = await self._send_photo(
                qr_png,
                filename=f"max-{account_id}-qr.png",
                caption=caption,
                chat_id=chat_id,
            )
            if response is not None:
                return response
            logger.warning("Telegram QR photo upload failed, falling back to text QR")
        return await self._send_message(
            self._format_qr_message(account_id, qr_link, reason),
            chat_id=chat_id,
        )

    def _format_qr_caption(self, account_id: str, qr_link: str, reason: str) -> str:
        parts = [
            f"🔐 <b>QR для входа в MAX</b>\nАккаунт: <code>{_esc(account_id)}</code>",
        ]
        if reason and reason != "Manual Telegram /auth request":
            parts.append(f"<pre>{_esc(reason)[:500]}</pre>")
        parts.append(
            f'<a href="{html.escape(qr_link, quote=True)}">Открыть ссылку авторизации</a>',
        )
        parts.append("Если картинка не читается, откройте ссылку вручную.")
        return "\n".join(parts)[:1024]

    def _format_qr_message(self, account_id: str, qr_link: str, reason: str) -> str:
        qr_text = render_qr_text(qr_link)
        parts = [
            f"🔐 <b>QR для входа в MAX</b>\nАккаунт: <code>{_esc(account_id)}</code>",
        ]
        if reason and reason != "Manual Telegram /auth request":
            parts.append(f"<pre>{_esc(reason)[:800]}</pre>")
        if qr_text and qr_text != qr_link:
            parts.append(f"<pre>{_esc(qr_text)}</pre>")
        parts.append(
            f'<a href="{html.escape(qr_link, quote=True)}">Открыть ссылку авторизации</a>',
        )
        parts.append("После успешного входа QR будет удалён из чата.")
        return "\n".join(parts)[:_MAX_TEXT]


def _esc(value: str) -> str:
    return html.escape(value, quote=False)


def _bool_state(value: bool, invert: bool = False) -> str:
    current = not value if invert else bool(value)
    return "ok" if current else "down"


def _flag_state(value: bool) -> str:
    return "yes" if bool(value) else "no"


def _task_state(task: asyncio.Task | None) -> str:
    if task and not task.done():
        return "running"
    return "stopped"


def _format_uptime(seconds: float) -> str:
    total = max(int(seconds), 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}ч {minutes}м {secs}с"
    if minutes:
        return f"{minutes}м {secs}с"
    return f"{secs}с"


def _health_icon(status: str) -> str:
    return {
        "healthy": "✅",
        "degraded": "⚠️",
        "unhealthy": "❌",
        "no_accounts": "⚪️",
    }.get(status, "⚪️")


def _health_label(status: str) -> str:
    return {
        "healthy": "✅ Всё работает",
        "degraded": "⚠️ Частично работает",
        "unhealthy": "❌ Нужна реакция",
        "no_accounts": "⚪️ Нет аккаунтов",
    }.get(status, f"⚪️ {status}")


def _status_headline(
    status: str,
    *,
    daemon_running: bool,
    connected: int,
    with_session: int,
    total: int,
) -> tuple[str, str | None]:
    if not daemon_running:
        return "maxBridge остановлен", "Демон сейчас не запущен."
    if total == 0:
        return "maxBridge не настроен", "В системе пока нет ни одного аккаунта MAX."
    if with_session == 0:
        return (
            "maxBridge ждёт авторизацию MAX",
            "Нет активной сессии. Запросите новый QR-код через /auth.",
        )
    if connected == 0:
        return (
            "maxBridge не подключён к MAX",
            "Сессия есть, но сейчас нет активного соединения.",
        )
    if status == "healthy":
        return "maxBridge работает штатно", "Все аккаунты онлайн и мост отвечает."
    if status == "degraded":
        return (
            "maxBridge работает частично",
            "Часть аккаунтов недоступна или требует внимания.",
        )
    return "maxBridge требует внимания", "Проверьте авторизацию, мост и последние ошибки."


def _service_state(value: bool) -> str:
    return "✅ работает" if bool(value) else "❌ не работает"


def _feature_state(value: bool) -> str:
    return "✅ включены" if bool(value) else "❌ выключены"


def _polling_state(task: asyncio.Task | None) -> str:
    return "🟢 активен" if task and not task.done() else "🔴 остановлен"


def _yes_no(value: bool) -> str:
    return "да" if bool(value) else "нет"


def _connection_state(value: bool) -> str:
    return "✅ подключён" if bool(value) else "❌ не подключён"


def _session_state(value: bool) -> str:
    return "✅ сессия есть" if bool(value) else "❌ сессии нет"


def _qr_state_label(active: bool) -> str:
    return "🟢 активен" if active else "⚪️ не запрошен"


def _error_kind_label(kind: str) -> str:
    return {
        "rpc": "📡 RPC",
        "log": "🪵 лог",
        "attachment": "📎 вложения",
    }.get(kind, f"ℹ️ {kind}")


def _status_actions(
    *,
    daemon_running: bool,
    polling_running: bool,
    connected: int,
    with_session: int,
    total: int,
    active_qr: int,
) -> list[str]:
    actions: list[str] = []
    if not daemon_running:
        actions.append("Проверьте PM2 и перезапустите сервис, если он остановлен.")
    if daemon_running and not polling_running:
        actions.append("Telegram long polling неактивен. Перезапустите бот.")
    if total > 0 and with_session == 0 and active_qr == 0:
        actions.append("Отправьте /auth, чтобы получить QR-код для входа в MAX.")
    if total > 0 and with_session == 0 and active_qr > 0:
        actions.append("В чате уже есть активный QR-код. Завершите вход через MAX.")
    if total > 0 and with_session > 0 and connected == 0:
        actions.append("Сессия сохранена, но соединения нет. Проверьте сеть и MAX API.")
    return actions


def _message_id(response: dict[str, Any] | None) -> int | None:
    if not response:
        return None
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    message_id = result.get("message_id")
    return message_id if isinstance(message_id, int) else None


def _safe_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _mask_email(email: str) -> str:
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    if len(local) <= 2:
        masked_local = local[:1] + "***"
    else:
        masked_local = local[:2] + "***"
    return f"{masked_local}@{domain}"


def _normalize_secret_text(raw_text: str) -> _NormalizedSecret:
    stripped = raw_text.strip()
    zero_width_removed = sum(1 for ch in stripped if ch in _SECRET_IGNORED_CHARS)
    cleaned = "".join(ch for ch in stripped if ch not in _SECRET_IGNORED_CHARS)

    if any(not ch.isprintable() for ch in cleaned):
        raise ValueError(
            "Пароль содержит скрытые символы или переносы строк. "
            "Отправьте его заново одной строкой.",
        )

    return _NormalizedSecret(
        value=cleaned,
        raw_length=len(raw_text),
        clean_length=len(cleaned),
        trimmed=raw_text != stripped,
        zero_width_removed=zero_width_removed,
    )
