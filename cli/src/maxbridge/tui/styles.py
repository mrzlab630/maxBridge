"""Стили и константы TUI — cyberpunk тема."""

from maxbridge import __version__

LOGO = r"""
[bold cyan]
 ███▄ ▄███▓ ▄▄▄      ▒██   ██▒ ▄▄▄▄    ██▀███   ██▓▓█████▄   ▄████ ▓█████
▓██▒▀█▀ ██▒▒████▄    ▒▒ █ █ ▒░▓█████▄ ▓██ ▒ ██▒▓██▒▒██▀ ██▌ ██▒ ▀█▒▓█   ▀
▓██    ▓██░▒██  ▀█▄  ░░  █   ░▒██▒ ▄██▓██ ░▄█ ▒▒██▒░██   █▌▒██░▄▄▄░▒███
▒██    ▒██ ░██▄▄▄▄██  ░ █ █ ▒ ▒██░█▀  ▒██▀▀█▄  ░██░░▓█▄   ▌░▓█  ██▓▒▓█  ▄
▒██▒   ░██▒ ▓█   ▓██▒▒██▒ ▒██▒░▓█  ▀█▓░██▓ ▒██▒░██░░▒████▓ ░▒▓███▀▒░▒████▒
░ ▒░   ░  ░ ▒▒   ▓▒█░▒▒ ░ ░▓ ░░▒▓███▀▒░ ▒▓ ░▒▓░░▓   ▒▒▓  ▒  ░▒   ▒ ░░ ▒░ ░
░  ░      ░  ▒   ▒▒ ░░░   ░▒ ░▒░▒   ░   ░▒ ░ ▒░ ▒ ░ ░ ▒  ▒   ░   ░  ░ ░  ░
░      ░     ░   ▒    ░    ░   ░    ░   ░░   ░  ▒ ░ ░ ░  ░ ░ ░   ░    ░
       ░         ░  ░ ░    ░   ░        ░      ░     ░          ░    ░  ░
                                   ░                ░
[/bold cyan][dim cyan]─────────────────── v{ver} ── [ 🌉 мост для MAX мессенджера ] ───[/dim cyan]
""".replace("{ver}", __version__)

CYBERPUNK_CSS = """
Screen { background: #0a0a12; }
Header { background: #1a0a2e; color: #00ffcc; text-style: bold; }
Footer { background: #1a0a2e; color: #00ffcc; }
Footer > .footer--key { background: #2a1a4e; color: #ff00ff; }

#main-menu { width: 100%; height: 100%; padding: 1 2; }
#logo-box { height: auto; margin-bottom: 1; }
#menu-options, #session-list, #chat-list-view, #session-action-menu,
#detail-action-list, #confirm-options {
    border: solid #00ffcc; background: #0d0d1a; padding: 0;
}
#menu-options > .option-list--option-highlighted,
#session-list > .option-list--option-highlighted,
#chat-list-view > .option-list--option-highlighted,
#session-action-menu > .option-list--option-highlighted,
#detail-action-list > .option-list--option-highlighted,
#confirm-options > .option-list--option-highlighted {
    background: #2a1a4e; color: #00ffcc; text-style: bold;
}
.option-list--option { color: #8888aa; padding: 0 2; }
#status-bar { height: 3; margin-top: 1; padding: 0 1; border: solid #333355; background: #0d0d1a; }
.screen-title { color: #ff00ff; text-style: bold; margin-bottom: 1; }
.hint { color: #555577; margin-top: 1; }

#chat-log { height: 1fr; border: solid #00ffcc; background: #0a0a12; }
#chat-input { background: #0d0d1a; border: solid #333355; color: #00ffcc; }
#chat-header-label {
    background: #1a0a2e; color: #ff00ff; text-style: bold; padding: 0 2; height: 1;
}

#qr-box {
    width: 90%; max-width: 80; height: auto; border: heavy #ff00ff;
    background: #0d0d1a; padding: 1 2; margin: 1 2;
}
#qr-log { height: auto; max-height: 25; background: #0a0a12; }
#qr-status-label { text-align: center; color: #00ffcc; margin-top: 1; }
#qr-password { display: none; margin-top: 1; }

#detail-profile { height: 1fr; border: solid #00ffcc; background: #0d0d1a; padding: 1; }
#detail-actions { height: auto; margin-top: 1; }
#confirm-box {
    width: 50; height: 12; border: heavy #ff0000;
    background: #1a0a0a; padding: 1 2; margin: 5 10;
}

RichLog { scrollbar-color: #00ffcc; scrollbar-background: #0d0d1a; }
OptionList { scrollbar-color: #00ffcc; scrollbar-background: #0d0d1a; }
"""
