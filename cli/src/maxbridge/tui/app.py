"""maxBridge TUI — главный модуль приложения."""

import os
import signal
import subprocess
import sys
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

from maxbridge.auth.encryption import TokenEncryptor
from maxbridge.auth.session import Session
from maxbridge.config import get_nested, load_config
from maxbridge.tui.accounts_store import load_accounts, load_active, save_active
from maxbridge.tui.helpers import resolve_session_path, session_exists
from maxbridge.tui.screens import ChatListScreen, SessionScreen
from maxbridge.tui.styles import CYBERPUNK_CSS, LOGO


def _runtime_root() -> Path:
    """Locate the checkout-local cli root from the installed package or entrypoint."""
    starts = (Path(__file__).resolve(), Path(sys.executable).resolve(), Path.cwd().resolve())
    for start in starts:
        for candidate in (start, *start.parents):
            if (candidate / "pyproject.toml").is_file() and (
                candidate / "src/maxbridge"
            ).is_dir():
                return candidate
    return Path.cwd().resolve()


def _local_config_path() -> Path:
    return _runtime_root() / "config/local.yaml"


def _daemon_log_path() -> Path:
    return _runtime_root() / "logs/maxbridge-tui-daemon.log"


def _open_daemon_log():
    log_path = _daemon_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(
        log_path, flags, 0o600
    )
    try:
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "a", encoding="utf-8")
    except OSError:
        os.close(descriptor)
        raise


class MaxBridgeTUI(App):
    TITLE = "maxBridge"
    SUB_TITLE = ""
    CSS = CYBERPUNK_CSS

    BINDINGS = [Binding("escape", "quit", "Выход")]

    def __init__(self) -> None:
        super().__init__()
        self._pulse_tick = 0
        self._config = load_config()
        self._data_dir = Path(
            get_nested(self._config, "security.key_file", "data/master.key")
        ).parent
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._encryptor = TokenEncryptor(str(self._data_dir / "master.key"))
        self._encryptor.ensure_key()
        self._pid_file = get_nested(
            self._config, "daemon.pid_file", "/tmp/maxbridge.pid")
        config_accounts = self._config.get("accounts", {})
        if not config_accounts:
            max_cfg = self._config.get("max", {})
            if max_cfg:
                config_accounts = {"default": max_cfg}
        self._config_accounts = dict(config_accounts)
        self._accounts_cfg = load_accounts(config_accounts)
        saved_aid = load_active()
        self._active_aid: str | None = (
            saved_aid if saved_aid and saved_aid in self._accounts_cfg else None
        )
        self._active_session: Session | None = None
        if self._active_aid:
            sess = Session(
                resolve_session_path(self._active_aid,
                                     self._accounts_cfg[self._active_aid]),
                self._encryptor)
            if sess.load():
                self._active_session = sess

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main-menu"):
            yield Static(LOGO, id="logo-box")
            yield OptionList(
                Option("🔐 Сессии        [dim]управление аккаунтами[/dim]",
                       id="sessions"),
                Option("💬 Чаты           [dim]каналы и диалоги[/dim]",
                       id="chats"),
                Option("📨 Telegram       [dim]оповещения[/dim]",
                       id="telegram"),
                Option("", id="daemon-toggle"),
                Option("🚪 Выход", id="quit"),
                id="menu-options",
            )
        yield Footer()

    def on_mount(self) -> None:
        self._update_status()
        self.set_interval(1, self._pulse)
        if not self._has_any_session():
            self.call_later(self._auto_auth)

    def _pulse(self) -> None:
        self._pulse_tick += 1
        self._update_status()

    @on(OptionList.OptionSelected, "#menu-options")
    def _menu_select(self, event: OptionList.OptionSelected) -> None:
        actions = {
            "sessions": self._open_sessions,
            "chats": self._open_chats,
            "telegram": self._open_telegram,
            "daemon-toggle": self._toggle_daemon,
            "quit": self.exit,
        }
        action = actions.get(event.option_id)
        if action:
            action()

    # ── Действия ────────────────────────────────────────

    def _open_sessions(self) -> None:
        def on_activate(aid: str, sess: Session) -> None:
            self._active_aid = aid
            self._active_session = sess
            save_active(aid)
            self._update_status()

        self.push_screen(
            SessionScreen(self._accounts_cfg, self._config_accounts,
                          self._encryptor,
                          on_activate, active_aid=self._active_aid))

    def _open_telegram(self) -> None:
        from maxbridge.tui.screens.telegram import TelegramScreen
        self.push_screen(TelegramScreen())

    def _open_chats(self) -> None:
        aid, sess = self._require_session()
        if aid and sess:
            self.push_screen(ChatListScreen(sess, aid))

    def _toggle_daemon(self) -> None:
        pid = self._daemon_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                self.notify(f"🛑 Останавливаем демон (PID {pid})...")
            except ProcessLookupError:
                pass
        else:
            try:
                daemon_log = _open_daemon_log()
            except OSError as exc:
                self.notify(
                    f"❌ Не удалось открыть лог демона: {exc}", severity="error"
                )
                return
            try:
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "maxbridge.main",
                        "-c",
                        str(_local_config_path()),
                    ],
                    cwd=_runtime_root(),
                    stdout=daemon_log,
                    stderr=daemon_log,
                    start_new_session=True,
                )
            finally:
                daemon_log.close()
            self.notify("🚀 Запуск демона...")
        self.set_timer(2, self._update_status)

    # ── Статус ──────────────────────────────────────────

    def _daemon_pid(self) -> int | None:
        try:
            pid = int(Path(self._pid_file).read_text().strip())
            os.kill(pid, 0)
            return pid
        except (FileNotFoundError, ValueError, ProcessLookupError,
                PermissionError):
            return None

    def _update_status(self) -> None:
        pid = self._daemon_pid()
        sessions = sum(
            1 for cfg in self._accounts_cfg.values()
            if session_exists("x", cfg)
        )
        tick = self._pulse_tick

        # Header subtitle
        if pid:
            frames = ["●", "◉", "○", "◉"]
            frame = frames[tick % len(frames)]
            daemon_s = f"{frame} ДЕМОН PID {pid}"
        else:
            daemon_s = "✖ ДЕМОН ОСТАНОВЛЕН"
        active_s = (f"  │  {self._active_aid}"
                    if self._active_aid else "")
        self.sub_title = f"{daemon_s}{active_s}  │  {sessions} сессий"

        # Обновляем текст пункта меню "демон"
        try:
            ol = self.query_one("#menu-options", OptionList)
            if pid:
                label = f"🔴 Остановить демон  [dim]PID {pid}[/dim]"
            else:
                label = "🟢 Запустить демон"
            ol.replace_option_prompt_at_index(3, label)
        except Exception:
            pass

    # ── Хелперы ─────────────────────────────────────────

    def _has_any_session(self) -> bool:
        return any(
            session_exists(aid, cfg)
            for aid, cfg in self._accounts_cfg.items()
        )

    def _auto_auth(self) -> None:
        self.notify("🔐 Нет сессий — откройте авторизацию", severity="warning")
        self._open_sessions()

    def _require_session(self) -> tuple[str | None, Session | None]:
        if self._active_session and self._active_aid:
            return self._active_aid, self._active_session
        for aid, cfg in self._accounts_cfg.items():
            sess_path = resolve_session_path(aid, cfg)
            sess = Session(sess_path, self._encryptor)
            if sess.load():
                self._active_aid = aid
                self._active_session = sess
                self._update_status()
                return aid, sess
        self.notify("❌ Нет сессии. Откройте Сессии.", severity="error")
        return None, None


def tui_entry() -> None:
    os.chdir(_runtime_root())
    app = MaxBridgeTUI()
    app.run()


if __name__ == "__main__":
    tui_entry()
