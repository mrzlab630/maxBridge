"""QR-авторизация — модальный экран."""

import asyncio
import io

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, RichLog

from maxbridge.auth.session import Session
from maxbridge.protocol.max_client import MaxClient
from maxbridge.tui.styles import CYBERPUNK_CSS


class QRScreen(ModalScreen[bool]):
    CSS = CYBERPUNK_CSS
    BINDINGS = [Binding("escape", "cancel", "Назад")]

    def __init__(self, session: Session, account_id: str) -> None:
        super().__init__()
        self._session = session
        self._aid = account_id

    def compose(self) -> ComposeResult:
        with Vertical(id="qr-box"):
            yield Label(f"  📱 QR АВТОРИЗАЦИЯ [{self._aid}]", classes="screen-title")
            yield RichLog(id="qr-log", wrap=True, markup=True)
            yield Label("⏳ Подключение...", id="qr-status-label")

    def on_mount(self) -> None:
        self._do_auth()

    def action_cancel(self) -> None:
        self.dismiss(False)

    @work(thread=False)
    async def _do_auth(self) -> None:
        log = self.query_one("#qr-log", RichLog)
        status = self.query_one("#qr-status-label", Label)
        client = MaxClient()
        try:
            await client.connect()
            qr_data = await client.request_qr()
            qr_link = qr_data["qrLink"]
            track_id = qr_data["trackId"]
            ttl = qr_data.get("ttl", 120000) / 1000
            interval = qr_data.get("pollingInterval", 5000) / 1000

            try:
                import qrcode as qr_mod
                qr = qr_mod.QRCode(box_size=1, border=1)
                qr.add_data(qr_link)
                qr.make(fit=True)
                buf = io.StringIO()
                qr.print_ascii(out=buf, invert=True)
                log.write(buf.getvalue())
            except ImportError:
                pass
            log.write(f"\n[bold cyan]{qr_link}[/bold cyan]\n")
            status.update("📷 Отсканируйте QR в приложении MAX...")

            elapsed = 0.0
            while elapsed < ttl:
                await asyncio.sleep(interval)
                elapsed += interval
                try:
                    resp = await client.check_qr_status(track_id)
                    st = resp.get("status", {})
                    if isinstance(st, dict) and st.get("loginAvailable"):
                        status.update("✅ Отсканировано! Входим...")
                        lr = await client.login_by_qr(track_id)
                        token = client.extract_login_token(lr)
                        self._session.save(client.device_id, token)
                        status.update("[bold green]✅ АВТОРИЗАЦИЯ УСПЕШНА[/bold green]")
                        await asyncio.sleep(1)
                        self.dismiss(True)
                        return
                    if resp.get("error") or (isinstance(st, dict) and st.get("error")):
                        status.update("[red]❌ QR код истёк[/red]")
                        self.dismiss(False)
                        return
                except Exception:
                    pass
                status.update(f"📷 Ожидание сканирования... {int(ttl - elapsed)}с")

            status.update("[red]⏰ Время вышло[/red]")
            self.dismiss(False)
        except Exception as e:
            status.update(f"[red]❌ Ошибка: {e}[/red]")
            self.dismiss(False)
        finally:
            await client.disconnect()
