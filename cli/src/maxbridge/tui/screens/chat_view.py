"""Экран чата — история + live сообщения + отправка."""

import asyncio
import time as _time

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, Label, OptionList, RichLog
from textual.widgets.option_list import Option

from maxbridge.auth.session import Session
from maxbridge.cache.entity_cache import _extract_user_name
from maxbridge.protocol.max_client import MaxClient
from maxbridge.tui.helpers import connect_and_login
from maxbridge.tui.styles import CYBERPUNK_CSS
from maxbridge.utils.constants import Opcode

HISTORY_COUNT = 30


class ConfirmLeaveScreen(ModalScreen[bool]):
    """Подтверждение отписки от канала/чата."""

    CSS = CYBERPUNK_CSS + """
    #leave-box {
        width: 50; height: 10; border: heavy #ff0000;
        background: #1a0a0a; padding: 1 2; margin: 5 10;
    }
    """
    BINDINGS = [Binding("escape", "cancel", "Отмена")]

    def __init__(self, chat_name: str) -> None:
        super().__init__()
        self._chat_name = chat_name

    def compose(self) -> ComposeResult:
        with Vertical(id="leave-box"):
            yield Label(
                f"[bold red]⚠️ Отписаться от '{self._chat_name}'?[/bold red]\n\n"
                f"  [dim]Вы перестанете получать сообщения.[/dim]")
            yield OptionList(
                Option("[red]🚪 Да, отписаться[/red]", id="yes"),
                Option("[green]✋ Нет, остаться[/green]", id="no"),
                id="leave-options",
            )

    @on(OptionList.OptionSelected, "#leave-options")
    def _on_confirm(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


class ChatViewScreen(Screen):
    CSS = CYBERPUNK_CSS + """
    #chat-actions {
        height: auto; max-height: 3; border: none;
        background: #0a0a12; padding: 0;
    }
    """
    BINDINGS = [Binding("escape", "go_back", "Назад")]

    def __init__(self, session: Session, chat_id: int, aid: str) -> None:
        super().__init__()
        self._session = session
        self._chat_id = chat_id
        self._aid = aid
        self._client: MaxClient | None = None
        self._names: dict[int, str] = {}
        self._my_id: int = 0
        self._chat_name: str = ""

    def compose(self) -> ComposeResult:
        yield Label(f"  💬 ЧАТ {self._chat_id}", id="chat-header-label")
        yield RichLog(id="chat-log", wrap=True, markup=True)
        yield OptionList(
            Option("[red]🚪 Отписаться от канала[/red]", id="leave"),
            id="chat-actions",
        )
        yield Input(
            placeholder="✏️ Сообщение... Enter=отправить, ESC=назад",
            id="chat-input")

    def action_go_back(self) -> None:
        self._cleanup()
        self.app.pop_screen()

    @on(OptionList.OptionSelected, "#chat-actions")
    def _on_action(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == "leave":
            name = self._chat_name or str(self._chat_id)
            self.app.push_screen(
                ConfirmLeaveScreen(name), self._on_leave_result)

    def _on_leave_result(self, confirmed: bool) -> None:
        if confirmed:
            self._do_leave()

    @work(thread=False)
    async def _do_leave(self) -> None:
        if not self._client:
            self._write_error("Нет подключения")
            return
        try:
            await self._client.invoke_method(
                opcode=Opcode.LEAVE_CHAT,
                payload={"chatId": self._chat_id})
            self._write_error(
                f"✅ Вы отписались от '{self._chat_name or self._chat_id}'")
            await asyncio.sleep(2)
            self._cleanup()
            self.app.pop_screen()
        except Exception as e:
            self._write_error(f"❌ Ошибка отписки: {e}")

    def on_mount(self) -> None:
        self.query_one("#chat-input", Input).focus()
        self._load()

    async def on_unmount(self) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        if self._client:
            asyncio.ensure_future(self._safe_disconnect())

    async def _safe_disconnect(self) -> None:
        client = self._client
        self._client = None
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    @on(Input.Submitted, "#chat-input")
    def _on_send(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        if not self._client:
            self._write_error("⏳ Подключение, подождите...")
            return
        # Запускаем в том же asyncio loop где живёт WebSocket
        asyncio.ensure_future(self._do_send_async(text))

    async def _do_send_async(self, text: str) -> None:
        payload = {"chatId": self._chat_id, "message": {"text": text}}
        try:
            resp = await self._client.invoke_method(
                opcode=Opcode.SEND_MESSAGE, payload=payload, timeout=10)
            rp = resp.get("payload") or {}
            if rp.get("error"):
                self._write_error(
                    rp.get("localizedMessage")
                    or rp.get("message")
                    or str(rp["error"]))
            else:
                self._write_msg(
                    {"sender": self._my_id, "text": text, "attaches": []})
        except Exception as e:
            self._write_error(f"❌ Не отправлено: {e}")

    async def _reconnect(self) -> None:
        await self._safe_disconnect()
        self._client, _ = await connect_and_login(self._session)
        self._attach_live_listener()

    def _attach_live_listener(self) -> None:
        chat_id = self._chat_id
        names = self._names
        screen = self

        async def on_pkt(cli, pkt):
            p = pkt.get("payload") or {}
            if pkt.get("opcode") == 128 and p.get("chatId") == chat_id:
                msg = p.get("message", {})
                sid = msg.get("sender")
                if isinstance(sid, int) and sid not in names:
                    try:
                        ur = await cli.invoke_method(
                            opcode=Opcode.RESOLVE_USERS,
                            payload={"contactIds": [sid]})
                        for c in (ur.get("payload") or {}).get("contacts", []):
                            names[c.get("id") or c.get("userId")] = (
                                _extract_user_name(c) or str(sid))
                    except Exception:
                        names[sid] = str(sid)
                screen._write_msg(msg)

        if self._client:
            self._client.set_packet_callback(on_pkt)

    def _write_msg(self, msg: dict) -> None:
        try:
            log = self.query_one("#chat-log", RichLog)
            sid = msg.get("sender")
            name = self._names.get(sid, str(sid))
            text = msg.get("text", "")
            att = msg.get("attaches", [])
            att_s = f" [dim]📎({len(att)})[/dim]" if att else ""
            is_me = sid == self._my_id
            color = "green" if is_me else "magenta"
            log.write(
                f"[bold {color}]{name}[/bold {color}] [dim]»[/dim] {text}{att_s}")
            log.scroll_end(animate=False)
        except Exception:
            pass

    def _write_error(self, text: str) -> None:
        try:
            log = self.query_one("#chat-log", RichLog)
            log.write(f"[bold red]  ✖ {text}[/bold red]")
            log.scroll_end(animate=False)
        except Exception:
            pass

    @work(thread=False)
    async def _load(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        header = self.query_one("#chat-header-label", Label)
        try:
            self._client, login_resp = await connect_and_login(self._session)
            self._my_id = ((login_resp.get("payload") or {})
                           .get("profile", {}).get("contact", {}).get("id", 0))

            # Инфо о чате
            cr = await self._client.invoke_method(
                opcode=Opcode.RESOLVE_CHAT,
                payload={"chatIds": [self._chat_id]})
            chats = (cr.get("payload") or {}).get("chats", [])
            name, ctype = "DM", ""
            if chats:
                name = chats[0].get("title") or chats[0].get("name") or "DM"
                ctype = chats[0].get("type", "")
            self._chat_name = name
            header.update(
                f"  💬 {name} ({ctype}) [id:{self._chat_id}]  │  ESC=назад")

            # История
            now_ms = int(_time.time() * 1000)
            hr = await self._client.invoke_method(
                opcode=Opcode.GET_HISTORY,
                payload={"chatId": self._chat_id, "from": now_ms,
                         "forward": 0, "backward": HISTORY_COUNT,
                         "getMessages": True})
            messages = (hr.get("payload") or {}).get("messages", [])

            # Резолв отправителей
            sids = {m.get("sender") for m in messages
                    if isinstance(m.get("sender"), int)}
            if sids:
                ur = await self._client.invoke_method(
                    opcode=Opcode.RESOLVE_USERS,
                    payload={"contactIds": list(sids)})
                for c in (ur.get("payload") or {}).get("contacts", []):
                    uid = c.get("id") or c.get("userId")
                    self._names[uid] = _extract_user_name(c) or str(uid)

            for msg in messages:
                self._write_msg(msg)
            log.scroll_end(animate=False)
            self._attach_live_listener()
        except Exception as e:
            log.write(f"[bold red]  ❌ Ошибка подключения: {e}[/bold red]")
            log.write("[dim]  Нажмите ESC и откройте чат заново[/dim]")
