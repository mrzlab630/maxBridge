"""Экраны управления сессиями."""

import logging
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Label, OptionList, RichLog
from textual.widgets.option_list import Option

from maxbridge.auth.encryption import TokenEncryptor
from maxbridge.auth.session import Session
from maxbridge.cache.entity_cache import _extract_user_name
from maxbridge.client.account_manager import AccountManager
from maxbridge.tui.accounts_store import add_account, remove_account
from maxbridge.tui.helpers import (
    connect_and_login,
    format_phone,
    format_ts,
    mask_device_id,
    resolve_session_path,
)
from maxbridge.tui.screens.qr import QRScreen
from maxbridge.tui.styles import CYBERPUNK_CSS
from maxbridge.utils.logger import redact_secrets

logger = logging.getLogger("maxbridge.tui.sessions")


class SessionDetailScreen(Screen):
    """Подробные данные сессии + действия."""

    CSS = CYBERPUNK_CSS
    BINDINGS = [Binding("escape", "go_back", "Назад")]

    def __init__(self, aid: str, session: Session, encryptor: TokenEncryptor,
                 on_activate=None, on_deleted=None) -> None:
        super().__init__()
        self._aid = aid
        self._session = session
        self._encryptor = encryptor
        self._on_activate = on_activate
        self._on_deleted = on_deleted

    def compose(self) -> ComposeResult:
        yield Label(f"  👤 СЕССИЯ [{self._aid}]", classes="screen-title")
        yield RichLog(id="detail-profile", wrap=True, markup=True)
        with Vertical(id="detail-actions"):
            yield OptionList(
                Option("⭐ Сделать основной   "
                       "[dim]использовать для чатов[/dim]", id="default"),
                Option("[red]🗑️[/red]  Удалить сессию     "
                       "[dim]удалить токен[/dim]", id="delete"),
                id="detail-action-list",
            )
        yield Label("[dim]↑↓ Навигация  Enter=Выбрать  ESC=Назад[/dim]",
                    classes="hint")

    def on_mount(self) -> None:
        self._load_profile()

    @work(thread=False)
    async def _load_profile(self) -> None:
        log = self.query_one("#detail-profile", RichLog)
        log.write("[dim]⏳ Загрузка профиля...[/dim]")
        client = None
        try:
            client, resp = await connect_and_login(self._session)
            payload = resp.get("payload") or {}
            profile = payload.get("profile", {})
            contact = profile.get("contact", {})

            name = _extract_user_name(contact)
            phone = contact.get("phone", "?")
            uid = contact.get("id", "?")
            country = contact.get("country", "")
            photo_id = contact.get("photoId", "")
            update_time = contact.get("updateTime", "")
            options = contact.get("options", [])

            names_lines = ""
            for n in contact.get("names", []):
                ntype = n.get("type", "?")
                nname = (n.get("name")
                         or f"{n.get('firstName', '')} {n.get('lastName', '')}".strip())
                names_lines += f"    {ntype}: [bold]{nname}[/bold]\n"

            chats = payload.get("chats", [])
            contacts_list = payload.get("contacts", [])

            log.clear()
            log.write(
                f"[bold cyan]  👤 ПРОФИЛЬ[/bold cyan]\n"
                f"  Имя       [bold]{name or '?'}[/bold]\n"
                f"  Телефон   [green]{format_phone(phone)}[/green]\n"
                f"  ID        [magenta]{uid}[/magenta]\n"
                f"  Страна    {country or '-'}\n"
                f"  Опции     {', '.join(options) if options else '-'}\n\n"
                f"[bold cyan]  📝 ИМЕНА[/bold cyan]\n"
                f"{names_lines}\n"
                f"[bold cyan]  🔑 СЕССИЯ[/bold cyan]\n"
                f"  Аккаунт   [cyan]{self._aid}[/cyan]\n"
                f"  Устройство [dim]{mask_device_id(self._session.device_id)}[/dim]\n"
                f"  Файл      [dim]{self._session.path}[/dim]\n\n"
                f"[bold cyan]  📊 СТАТИСТИКА[/bold cyan]\n"
                f"  Чатов     {len(chats)}\n"
                f"  Контактов {len(contacts_list)}\n"
                f"  Фото ID   {photo_id or '-'}\n"
                f"  Обновлён  {format_ts(update_time)}"
            )
        except Exception as e:
            logger.exception("Failed to load profile for account '%s'", self._aid)
            log.clear()
            log.write(
                "[red]  ❌ Ошибка загрузки профиля: "
                f"{redact_secrets(e)}[/red]"
            )
        finally:
            if client:
                await client.disconnect()

    @on(OptionList.OptionSelected, "#detail-action-list")
    def _on_action(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == "default":
            self._set_default()
        elif event.option_id == "delete":
            self._confirm_delete()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _set_default(self) -> None:
        if self._on_activate:
            self._on_activate(self._aid, self._session)
        self.notify(f"⭐ '{self._aid}' — основная сессия", severity="information")
        self.app.pop_screen()

    def _confirm_delete(self) -> None:
        def on_result(deleted: bool) -> None:
            if deleted:
                if self._on_deleted:
                    self._on_deleted(self._aid)
                self.app.pop_screen()

        self.app.push_screen(
            ConfirmDeleteScreen(self._aid, self._session), on_result)


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Подтверждение удаления сессии."""

    CSS = CYBERPUNK_CSS
    BINDINGS = [Binding("escape", "confirm_no", "Отмена")]

    def __init__(self, aid: str, session: Session) -> None:
        super().__init__()
        self._aid = aid
        self._session = session

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(
                f"[bold red]  ⚠️ Удалить сессию '{self._aid}'?[/bold red]\n\n"
                f"  [dim]Зашифрованный токен будет удалён.\n"
                f"  Потребуется повторная QR-авторизация.[/dim]",
            )
            yield OptionList(
                Option("[red]🗑️ Да, удалить[/red]", id="yes"),
                Option("[green]✋ Нет, отмена[/green]", id="no"),
                id="confirm-options",
            )

    @on(OptionList.OptionSelected, "#confirm-options")
    def _on_confirm(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == "yes":
            self._session.clear()
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm_no(self) -> None:
        self.dismiss(False)


class SessionScreen(Screen):
    """Список сессий."""

    CSS = CYBERPUNK_CSS

    BINDINGS = [
        Binding("escape", "go_back", "Назад"),
        Binding("enter", "open_session", "Подробнее"),
    ]

    def __init__(self, accounts_cfg: dict, config_accounts: dict,
                 encryptor: TokenEncryptor,
                 on_activate=None, active_aid: str | None = None) -> None:
        super().__init__()
        self._accounts_cfg = accounts_cfg
        self._config_accounts = config_accounts
        self._encryptor = encryptor
        self._on_activate = on_activate
        self._active_aid = active_aid
        self._aids: list[str] = []
        self._sessions_cache: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        yield Label("  🔐 СЕССИИ", classes="screen-title")
        yield OptionList(id="session-list")
        yield Label("[dim]↑↓ Навигация  Enter=Подробнее  ESC=Назад[/dim]",
                    classes="hint")

    def on_mount(self) -> None:
        self._refresh()

    def on_screen_resume(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        ol = self.query_one("#session-list", OptionList)
        ol.clear_options()
        self._aids.clear()
        for aid, cfg in self._accounts_cfg.items():
            sess_path = resolve_session_path(aid, cfg)
            p = Path(sess_path)
            exists = p.exists() and p.stat().st_size > 0
            self._aids.append(aid)

            if exists:
                is_active = (aid == self._active_aid)
                star = " ⭐" if is_active else ""
                cached = self._sessions_cache.get(aid)
                if cached:
                    ol.add_option(Option(
                        f"[green]●[/green] [bold cyan]{aid}[/bold cyan]"
                        f"{star}\n"
                        f"   [bold]{cached.get('name', '?')}[/bold]  "
                        f"[green]{format_phone(cached.get('phone', ''))}[/green]  "
                        f"[dim]id:{cached.get('id', '')}[/dim]",
                        id=aid))
                else:
                    ol.add_option(Option(
                        f"[green]●[/green] [bold cyan]{aid}[/bold cyan]"
                        f"{star}  [dim](загрузка...)[/dim]", id=aid))
                    self._load_profile(aid, cfg)
            else:
                ol.add_option(Option(
                    f"[dim]○ {aid}  — нет сессии[/dim]", id=aid))

        # Кнопка авторизации внизу списка
        ol.add_option(Option(
            "[bold green]📱 Новая авторизация (QR)[/bold green]",
            id="_auth_new"))

    @work(thread=False)
    async def _load_profile(self, aid: str, cfg: dict) -> None:
        sess_path = resolve_session_path(aid, cfg)
        session = Session(sess_path, self._encryptor)
        if not session.load():
            return
        client = None
        try:
            client, resp = await connect_and_login(session)
            contact = (resp.get("payload") or {}).get(
                "profile", {}).get("contact", {})
            self._sessions_cache[aid] = {
                "name": _extract_user_name(contact),
                "phone": contact.get("phone", ""),
                "id": contact.get("id", ""),
            }
            self._refresh()
        except Exception:
            pass
        finally:
            if client:
                await client.disconnect()

    def _selected_aid(self) -> str | None:
        ol = self.query_one("#session-list", OptionList)
        if ol.highlighted is not None and ol.highlighted < len(self._aids):
            return self._aids[ol.highlighted]
        return None

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_open_session(self) -> None:
        aid = self._selected_aid()
        if not aid:
            return
        cfg = self._accounts_cfg.get(aid, {})
        sess_path = resolve_session_path(aid, cfg)
        session = Session(sess_path, self._encryptor)
        if not session.load():
            self.notify("❌ Нет сессии. Пройдите авторизацию.", severity="warning")
            return
        def on_activate_wrapper(a: str, s: Session) -> None:
            self._active_aid = a
            if self._on_activate:
                self._on_activate(a, s)

        self.app.push_screen(SessionDetailScreen(
            aid, session, self._encryptor,
            on_activate=on_activate_wrapper,
            on_deleted=self._on_session_deleted,
        ))

    @on(OptionList.OptionSelected, "#session-list")
    def _on_opt_select(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == "_auth_new":
            self.action_auth_new()
        else:
            self.action_open_session()

    def _on_session_deleted(self, aid: str) -> None:
        self._sessions_cache.pop(aid, None)
        remove_account(self._accounts_cfg, self._config_accounts, aid)
        self._refresh()

    def action_auth_new(self) -> None:
        n = len(self._accounts_cfg) + 1
        while f"account_{n}" in self._accounts_cfg:
            n += 1
        tmp_aid = f"account_{n}"
        sess_path = f"data/{tmp_aid}.session"
        cfg = {"session_file": sess_path}
        session = Session(sess_path, self._encryptor)

        def on_qr(ok: bool) -> None:
            self._finish_new_auth(tmp_aid, cfg, session, ok)

        self._accounts_cfg[tmp_aid] = cfg
        self.app.push_screen(
            QRScreen(
                session,
                tmp_aid,
                duplicate_guard=lambda identity: self._find_duplicate_account(
                    identity,
                    tmp_aid,
                ),
            ),
            on_qr,
        )

    async def _find_duplicate_account(
        self,
        max_contact_id: str,
        exclude_account_id: str,
    ) -> str | None:
        manager = AccountManager(self._encryptor)
        for account_id, config in self._accounts_cfg.items():
            if account_id != exclude_account_id:
                manager.add_account(account_id, config)
        return await manager.find_duplicate_account(max_contact_id)

    def _finish_new_auth(
        self,
        account_id: str,
        config: dict,
        session: Session,
        ok: bool,
    ) -> None:
        if ok:
            add_account(
                self._accounts_cfg,
                self._config_accounts,
                account_id,
                config,
            )
            self._load_profile(account_id, config)
        elif not session.exists():
            self._accounts_cfg.pop(account_id, None)
        self._refresh()
