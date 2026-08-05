"""QR-авторизация — модальный экран."""

import asyncio
import io
import logging

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, RichLog

from maxbridge.auth.qr_auth import complete_qr_auth, request_qr_session
from maxbridge.auth.session import Session
from maxbridge.protocol.max_client import MaxClient
from maxbridge.tui.styles import CYBERPUNK_CSS

logger = logging.getLogger("maxbridge.tui.qr")

_PASSWORD_TIMEOUT_SECONDS = 300
_SUCCESS_DISPLAY_SECONDS = 1


class QRScreen(ModalScreen[bool]):
    CSS = CYBERPUNK_CSS
    BINDINGS = [Binding("escape", "cancel", "Назад")]

    def __init__(self, session: Session, account_id: str) -> None:
        super().__init__()
        self._session = session
        self._aid = account_id
        self._password_future: asyncio.Future[str] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="qr-box"):
            yield Label(f"  📱 QR АВТОРИЗАЦИЯ [{self._aid}]", classes="screen-title")
            yield RichLog(id="qr-log", wrap=True, markup=True)
            yield Label("⏳ Подключение...", id="qr-status-label")
            yield Input(
                placeholder="Пароль MAX",
                password=True,
                id="qr-password",
                disabled=True,
            )

    def on_mount(self) -> None:
        self._do_auth()

    def action_cancel(self) -> None:
        if self._password_future and not self._password_future.done():
            self._password_future.cancel()
        self.dismiss(False)

    @on(Input.Submitted, "#qr-password")
    def _on_password_submitted(self, event: Input.Submitted) -> None:
        future = self._password_future
        if future is None or future.done():
            return

        password = event.value.strip()
        if not password:
            self.query_one("#qr-status-label", Label).update(
                "[red]❌ Пароль MAX не может быть пустым[/red]"
            )
            return

        event.input.value = ""
        event.input.disabled = True
        self.query_one("#qr-status-label", Label).update(
            "⏳ Проверяем пароль MAX..."
        )
        future.set_result(password)

    @work(thread=False)
    async def _do_auth(self) -> None:
        log = self.query_one("#qr-log", RichLog)
        status = self.query_one("#qr-status-label", Label)
        client = MaxClient()
        try:
            await client.connect()
            qr_session = await request_qr_session(client)

            try:
                import qrcode as qr_mod
                qr = qr_mod.QRCode(box_size=1, border=1)
                qr.add_data(qr_session.qr_link)
                qr.make(fit=True)
                buf = io.StringIO()
                qr.print_ascii(out=buf, invert=True)
                log.write(buf.getvalue())
            except ImportError:
                pass
            log.write(f"\n[bold cyan]{qr_session.qr_link}[/bold cyan]\n")
            status.update("📷 Отсканируйте QR в приложении MAX...")

            elapsed = 0.0
            while elapsed < qr_session.ttl:
                await asyncio.sleep(qr_session.poll_interval)
                elapsed += qr_session.poll_interval
                try:
                    resp = await client.check_qr_status(qr_session.track_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("QR status check failed for '%s': %s", self._aid, exc)
                    status.update(
                        f"[yellow]⚠️ Ошибка проверки QR: {escape(str(exc))}. Повторяем...[/yellow]"
                    )
                    continue

                st = resp.get("status", {})
                if isinstance(st, dict) and st.get("loginAvailable"):
                    status.update("✅ Отсканировано! Завершаем вход...")
                    await complete_qr_auth(
                        client,
                        qr_session,
                        self._session,
                        password_provider=self._request_password,
                    )
                    status.update("[bold green]✅ АВТОРИЗАЦИЯ УСПЕШНА[/bold green]")
                    await asyncio.sleep(_SUCCESS_DISPLAY_SECONDS)
                    self.dismiss(True)
                    return

                error = resp.get("error") or (
                    st.get("error") if isinstance(st, dict) else None
                )
                if error:
                    self._show_error(f"QR отклонён: {error}")
                    return

                remaining = max(0, int(qr_session.ttl - elapsed))
                status.update(f"📷 Ожидание сканирования... {remaining}с")

            self._show_error("Время действия QR-кода истекло")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("QR authentication failed for '%s'", self._aid)
            self._show_error(str(exc))
        finally:
            try:
                await client.disconnect()
            except Exception as exc:
                logger.warning("QR client disconnect failed for '%s': %s", self._aid, exc)

    async def _request_password(self, challenge: dict) -> str:
        status = self.query_one("#qr-status-label", Label)
        log = self.query_one("#qr-log", RichLog)
        password_input = self.query_one("#qr-password", Input)

        details: list[str] = []
        hint = str(challenge.get("hint") or "").strip()
        email = str(challenge.get("email") or "").strip()
        if hint:
            details.append(f"Подсказка: {escape(hint)}")
        if email:
            details.append(f"Email: {escape(email)}")

        log.display = False
        message = "🔐 MAX запросил пароль двухэтапной проверки"
        if details:
            message += "\n" + "\n".join(details)
        status.update(message)

        future = asyncio.get_running_loop().create_future()
        self._password_future = future
        password_input.value = ""
        password_input.disabled = False
        password_input.display = True
        password_input.focus()

        try:
            return await asyncio.wait_for(future, timeout=_PASSWORD_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "Время ожидания пароля MAX истекло. Запустите авторизацию заново."
            ) from exc
        finally:
            if self._password_future is future:
                self._password_future = None
            password_input.value = ""
            password_input.disabled = True
            password_input.display = False

    def _show_error(self, message: str) -> None:
        safe_message = escape(message.strip() or "Неизвестная ошибка")
        log = self.query_one("#qr-log", RichLog)
        log.write(f"\n[red]❌ Ошибка авторизации: {safe_message}[/red]")
        log.display = False
        self.query_one("#qr-status-label", Label).update(
            f"[red]❌ Ошибка авторизации: {safe_message}[/red]"
        )
