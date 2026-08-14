"""Экран настроек Telegram оповещений."""

import logging

import aiohttp
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Input, Label, OptionList, RichLog
from textual.widgets.option_list import Option

from maxbridge.telegram.config import (
    load_telegram_config,
    save_telegram_config,
)
from maxbridge.tui.styles import CYBERPUNK_CSS
from maxbridge.utils.logger import redact_secrets

_DIRECT_TEST_LABEL = "🧪 Тест Bot API"
_DIRECT_TEST_SCOPE = (
    "[dim]Только прямая проверка Bot API; готовность форвардинга демона не проверяется.[/dim]"
)
logger = logging.getLogger("maxbridge.tui.telegram")
_ERROR_BODY_LIMIT = 500


class TelegramScreen(Screen):
    """Настройки Telegram оповещений."""

    CSS = CYBERPUNK_CSS + """
    #tg-settings { padding: 1; }
    #tg-settings Input { margin-bottom: 1; }
    #tg-settings Label { margin-top: 1; }
    #tg-log { height: 6; border: solid #333355; background: #0d0d1a; margin-top: 1; }
    """

    BINDINGS = [Binding("escape", "go_back", "Назад")]

    def __init__(self) -> None:
        super().__init__()
        self._config = load_telegram_config()

    def compose(self) -> ComposeResult:
        yield Label("  📨 TELEGRAM ОПОВЕЩЕНИЯ", classes="screen-title")
        with Vertical(id="tg-settings"):
            yield Label("Токен бота:")
            yield Input(
                value=self._config.bot_token,
                placeholder="000000000:EXAMPLE_TOKEN",
                password=True,
                id="tg-token")
            yield Label("ID получателя:")
            yield Input(
                value=self._config.chat_id,
                placeholder="-1000000000001 или @channel",
                id="tg-chat-id")
            yield OptionList(
                Option(self._toggle_label(), id="toggle"),
                Option("💾 Сохранить", id="save"),
                Option(_DIRECT_TEST_LABEL, id="test"),
                id="tg-actions",
            )
            yield RichLog(id="tg-log", wrap=True, markup=True)
        yield Label("[dim]↑↓ Навигация  Enter=Выбрать  ESC=Назад[/dim]",
                    classes="hint")

    def _toggle_label(self) -> str:
        if self._config.enabled:
            return "[green]🟢 Оповещения ВКЛЮЧЕНЫ[/green]  [dim]нажмите чтобы выключить[/dim]"
        return "[red]🔴 Оповещения ВЫКЛЮЧЕНЫ[/red]  [dim]нажмите чтобы включить[/dim]"

    @on(OptionList.OptionSelected, "#tg-actions")
    def _on_action(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == "toggle":
            self._toggle()
        elif event.option_id == "save":
            self._save()
        elif event.option_id == "test":
            self._test()

    def _toggle(self) -> None:
        self._config.enabled = not self._config.enabled
        if not self._save():
            self._config.enabled = not self._config.enabled
            return
        ol = self.query_one("#tg-actions", OptionList)
        ol.replace_option_prompt_at_index(0, self._toggle_label())
        state = "включены" if self._config.enabled else "выключены"
        self._log(f"Оповещения {state}")

    def _save(self) -> bool:
        self._config.bot_token = self.query_one(
            "#tg-token", Input).value.strip()
        self._config.chat_id = self.query_one(
            "#tg-chat-id", Input).value.strip()
        try:
            save_telegram_config(self._config)
        except Exception as exc:
            logger.exception("Failed to save Telegram settings")
            error_type = type(exc).__name__
            self._log(f"[red]❌ Не удалось сохранить настройки ({error_type})[/red]")
            self.notify(
                f"❌ Не удалось сохранить настройки ({error_type})",
                severity="error",
            )
            return False
        self._log("💾 Настройки сохранены")
        self.notify("💾 Сохранено", severity="information")
        return True

    @work(thread=False)
    async def _test(self) -> None:
        token = self.query_one("#tg-token", Input).value.strip()
        chat_id = self.query_one("#tg-chat-id", Input).value.strip()
        if not token or not chat_id:
            self._log("[red]❌ Укажите токен и ID получателя[/red]")
            return
        self._log(_DIRECT_TEST_SCOPE)
        self._log("🧪 Отправка прямого тестового сообщения через Bot API...")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": "🌉 <b>maxBridge</b> — тестовое оповещение",
            "parse_mode": "HTML",
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        self._log(
                            "[green]✅ Bot API доступен. Это не подтверждает готовность "
                            "форвардинга демона.[/green]"
                        )
                    else:
                        body = await resp.text()
                        safe_body = redact_secrets(
                            body[:_ERROR_BODY_LIMIT]
                        )[:_ERROR_BODY_LIMIT]
                        logger.error(
                            "Telegram Bot API returned HTTP %s: %s",
                            resp.status,
                            safe_body,
                        )
                        self._log(
                            f"[red]❌ Ошибка {resp.status}: {safe_body}[/red]"
                        )
        except Exception as exc:
            logger.exception("Telegram Bot API connectivity test failed")
            self._log(f"[red]❌ Ошибка Bot API: {type(exc).__name__}[/red]")

    def _log(self, text: str) -> None:
        try:
            self.query_one("#tg-log", RichLog).write(redact_secrets(text))
        except Exception:
            pass

    def action_go_back(self) -> None:
        self.app.pop_screen()
