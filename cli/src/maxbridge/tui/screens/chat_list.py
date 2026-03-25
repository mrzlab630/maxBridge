"""Экран списка чатов — каналы и диалоги."""

import asyncio

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from maxbridge.auth.session import Session
from maxbridge.cache.entity_cache import _extract_user_name
from maxbridge.tui.helpers import connect_and_login
from maxbridge.tui.screens.chat_view import ChatViewScreen
from maxbridge.tui.styles import CYBERPUNK_CSS
from maxbridge.utils.constants import Opcode

TEXT_PREVIEW_LEN = 40


class JoinChannelScreen(ModalScreen[bool]):
    """Ввод ссылки для подписки на канал."""

    CSS = CYBERPUNK_CSS + """
    #join-box {
        width: 65; height: 8; border: heavy #00ffcc;
        background: #0d0d1a; padding: 1 2; margin: 5 10;
    }
    """
    BINDINGS = [Binding("escape", "cancel", "Отмена")]

    def __init__(self, client) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        with Vertical(id="join-box"):
            yield Label("[bold cyan]📥 Подписка на канал[/bold cyan]\n")
            yield Input(
                placeholder="https://max.ru/join/... или хэш приглашения",
                id="join-input")

    def on_mount(self) -> None:
        self.query_one("#join-input", Input).focus()

    @on(Input.Submitted, "#join-input")
    def _on_submit(self, event: Input.Submitted) -> None:
        link = event.value.strip()
        if link:
            self._do_join(link)
        else:
            self.dismiss(False)

    @work(thread=False)
    async def _do_join(self, link: str) -> None:
        # Извлекаем хэш из ссылки
        hash_part = link
        if "/join/" in link:
            hash_part = link.split("/join/")[-1].strip("/")
        elif "max.ru/" in link:
            hash_part = link.split("max.ru/")[-1].strip("/")

        try:
            # Резолв по ссылке (opcode 89)
            resp = await self._client.invoke_method(
                opcode=Opcode.RESOLVE_BY_LINK,
                payload={"link": f"join/{hash_part}"})
            payload = resp.get("payload") or {}
            if payload.get("error"):
                self.notify(
                    f"❌ {payload.get('localizedMessage') or payload['error']}",
                    severity="error")
                self.dismiss(False)
                return

            # Подписка (opcode 57)
            resp2 = await self._client.invoke_method(
                opcode=Opcode.JOIN_CHANNEL,
                payload={"link": f"join/{hash_part}"})
            p2 = resp2.get("payload") or {}
            if p2.get("error"):
                self.notify(
                    f"❌ {p2.get('localizedMessage') or p2['error']}",
                    severity="error")
                self.dismiss(False)
            else:
                self.notify("✅ Подписка оформлена!", severity="information")
                self.dismiss(True)
        except Exception as e:
            self.notify(f"❌ Ошибка: {e}", severity="error")
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ChatListScreen(Screen):
    CSS = CYBERPUNK_CSS

    BINDINGS = [
        Binding("escape", "go_back", "Назад"),
        Binding("enter", "open_selected", "Открыть"),
    ]

    def __init__(self, session: Session, aid: str) -> None:
        super().__init__()
        self._session = session
        self._aid = aid
        self._chat_ids: list[int] = []
        self._unread: dict[int, int] = {}
        self._titles: dict[int, str] = {}
        self._types: dict[int, str] = {}
        self._last_texts: dict[int, str] = {}
        self._client = None

    def compose(self) -> ComposeResult:
        yield Label(f"  📋 КАНАЛЫ И ЧАТЫ [{self._aid}]", classes="screen-title")
        yield OptionList(id="chat-list-view")
        yield Label("[dim]↑↓ Навигация  Enter=Открыть  ESC=Назад[/dim]",
                    classes="hint")

    def on_mount(self) -> None:
        self._load_chats()

    def action_go_back(self) -> None:
        self._disconnect()
        self.app.pop_screen()

    def action_open_selected(self) -> None:
        ol = self.query_one("#chat-list-view", OptionList)
        if ol.highlighted is not None and ol.highlighted < len(self._chat_ids):
            cid = self._chat_ids[ol.highlighted]
            self._disconnect()
            self.app.push_screen(
                ChatViewScreen(self._session, cid, self._aid))

    @on(OptionList.OptionSelected)
    def _on_select(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == "_join":
            self._open_join()
            return
        idx = event.option_index
        if idx < len(self._chat_ids):
            cid = self._chat_ids[idx]
            self._disconnect()
            self.app.push_screen(
                ChatViewScreen(self._session, cid, self._aid))

    def _open_join(self) -> None:
        if not self._client:
            self.notify("❌ Нет подключения", severity="error")
            return

        def on_result(ok: bool) -> None:
            if ok:
                # Перезагружаем список чатов
                self._disconnect()
                self._chat_ids.clear()
                self._load_chats()

        self.app.push_screen(JoinChannelScreen(self._client), on_result)

    def _disconnect(self) -> None:
        if self._client:
            asyncio.ensure_future(self._client.disconnect())
            self._client = None

    def _render_list(self) -> None:
        ol = self.query_one("#chat-list-view", OptionList)
        highlighted = ol.highlighted
        ol.clear_options()
        for cid in self._chat_ids:
            ctype = self._types.get(cid, "?")
            title = self._titles.get(cid, str(cid))
            last_text = self._last_texts.get(cid, "")
            new = self._unread.get(cid, 0)
            badge = f" [bold red]({new})[/bold red]" if new else ""
            ol.add_option(Option(
                f"[magenta]{ctype:8}[/magenta] "
                f"[cyan]{title}[/cyan]{badge}\n"
                f"         [dim]{last_text}[/dim]",
                id=str(cid),
            ))
        # Кнопка подписки внизу
        ol.add_option(Option(
            "[bold green]📥 Подписаться на канал[/bold green]",
            id="_join"))
        if highlighted is not None and highlighted < len(self._chat_ids):
            ol.highlighted = highlighted

    @work(thread=False)
    async def _load_chats(self) -> None:
        ol = self.query_one("#chat-list-view", OptionList)
        ol.clear_options()
        ol.add_option(Option("[dim]⏳ Загрузка...[/dim]", id="loading"))
        try:
            self._client, login_resp = await connect_and_login(self._session)
            lp = login_resp.get("payload") or {}
            my_id = lp.get("profile", {}).get("contact", {}).get("id")

            # Имена контактов
            contact_names: dict[int, str] = {}
            for c in lp.get("contacts", []):
                uid = c.get("id") or c.get("userId")
                if uid:
                    contact_names[uid] = _extract_user_name(c) or str(uid)

            chats_data = lp.get("chats", [])
            chats_data.sort(
                key=lambda c: c.get("lastEventTime", 0), reverse=True)

            self._chat_ids.clear()
            for chat in chats_data:
                cid = chat.get("id") or chat.get("chatId", 0)
                if not cid:
                    continue
                ctype = chat.get("type", "?")
                title = chat.get("title") or chat.get("name", "")
                if not title and ctype == "DIALOG":
                    for pid_str in chat.get("participants", {}):
                        pid = int(pid_str) if str(pid_str).isdigit() else 0
                        if pid and pid != my_id:
                            title = contact_names.get(pid, f"User {pid}")
                            break
                    title = title or "DM"
                last_msg = chat.get("lastMessage", {})
                last_text = (last_msg.get("text", "") or "")[:TEXT_PREVIEW_LEN]
                self._chat_ids.append(cid)
                self._unread[cid] = chat.get("newMessages", 0)
                self._titles[cid] = title
                self._types[cid] = ctype
                self._last_texts[cid] = last_text

            self._render_list()
            if not self._chat_ids:
                ol.clear_options()
                ol.add_option(Option("[dim]💤 Чатов не найдено[/dim]"))

            # Live обновление счётчиков
            async def on_msg(cli, pkt):
                p = pkt.get("payload") or {}
                if pkt.get("opcode") == 128:
                    cid = p.get("chatId")
                    if cid and cid in self._unread:
                        unread = p.get("unread")
                        self._unread[cid] = (
                            unread if isinstance(unread, int)
                            else self._unread.get(cid, 0) + 1)
                        text = (p.get("message", {}).get("text", "") or "")
                        if text:
                            self._last_texts[cid] = text[:TEXT_PREVIEW_LEN]
                        self._render_list()

            if self._client:
                self._client.set_packet_callback(on_msg)
        except Exception as e:
            ol.clear_options()
            ol.add_option(Option(f"[red]❌ Ошибка: {e}[/red]"))
