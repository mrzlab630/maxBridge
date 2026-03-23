"""maxBridge Commander — interactive CLI for managing the bridge."""

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from maxbridge import __version__
from maxbridge.auth.encryption import TokenEncryptor
from maxbridge.auth.qr_auth import authenticate_qr
from maxbridge.auth.session import Session
from maxbridge.cache.entity_cache import _extract_user_name
from maxbridge.config import get_nested, load_config
from maxbridge.protocol.errors import MaxApiError, MaxConnectionError
from maxbridge.protocol.max_client import MaxClient
from maxbridge.utils.constants import Opcode

# ANSI colors
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_RED = "\033[31m"
C_BLUE = "\033[34m"


def _p(text: str) -> None:
    """Print with flush."""
    print(text, flush=True)


def _header() -> None:
    _p(f"\n{C_BOLD}  maxBridge Commander v{__version__}{C_RESET}")
    _p(f"  {C_DIM}Type 'help' for available commands{C_RESET}\n")


class Commander:
    """Interactive CLI shell for maxBridge management."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._data_dir = Path(
            get_nested(config, "security.key_file", "data/master.key")
        ).parent
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._encryptor = TokenEncryptor(str(self._data_dir / "master.key"))
        self._encryptor.ensure_key()
        self._pid_file = get_nested(config, "daemon.pid_file", "/tmp/maxbridge.pid")
        self._accounts_cfg = config.get("accounts", {})
        if not self._accounts_cfg:
            max_cfg = config.get("max", {})
            if max_cfg:
                self._accounts_cfg = {"default": max_cfg}

    # ── Daemon control ──────────────────────────────────────

    def cmd_start(self) -> None:
        """Start the daemon in background."""
        if self._daemon_pid():
            _p(f"{C_YELLOW}Daemon already running (PID {self._daemon_pid()}){C_RESET}")
            return
        _p(f"{C_DIM}Starting daemon...{C_RESET}")
        subprocess.Popen(
            [sys.executable, "-m", "maxbridge.main"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(1)
        pid = self._daemon_pid()
        if pid:
            _p(f"{C_GREEN}Daemon started (PID {pid}){C_RESET}")
        else:
            _p(f"{C_RED}Daemon failed to start{C_RESET}")

    def cmd_stop(self) -> None:
        """Stop the running daemon."""
        pid = self._daemon_pid()
        if not pid:
            _p(f"{C_YELLOW}Daemon is not running{C_RESET}")
            return
        try:
            os.kill(pid, signal.SIGTERM)
            _p(f"{C_GREEN}Sent SIGTERM to PID {pid}{C_RESET}")
            for _ in range(10):
                time.sleep(0.5)
                if not self._daemon_pid():
                    _p(f"{C_GREEN}Daemon stopped{C_RESET}")
                    return
            _p(f"{C_YELLOW}Daemon still running — try 'kill {pid}'{C_RESET}")
        except ProcessLookupError:
            _p(f"{C_DIM}Process already gone{C_RESET}")
            try:
                Path(self._pid_file).unlink()
            except FileNotFoundError:
                pass

    def cmd_restart(self) -> None:
        """Restart the daemon."""
        self.cmd_stop()
        time.sleep(1)
        self.cmd_start()

    def cmd_status(self) -> None:
        """Show daemon status."""
        pid = self._daemon_pid()
        if pid:
            _p(f"{C_GREEN}Daemon running (PID {pid}){C_RESET}")
        else:
            _p(f"{C_DIM}Daemon is not running{C_RESET}")

    def cmd_logs(self) -> None:
        """Show live daemon logs. Restarts daemon with --debug in foreground."""
        _p(f"{C_DIM}Starting daemon in foreground with debug logs...{C_RESET}")
        _p(f"{C_DIM}Press Ctrl+C to stop{C_RESET}\n")
        try:
            subprocess.run(
                [sys.executable, "-m", "maxbridge.main", "--debug"],
            )
        except KeyboardInterrupt:
            pass

    def _daemon_pid(self) -> int | None:
        """Read PID from file and verify process exists."""
        try:
            pid = int(Path(self._pid_file).read_text().strip())
            os.kill(pid, 0)  # check if alive
            return pid
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
            return None

    # ── Session management ──────────────────────────────────

    def cmd_sessions(self) -> None:
        """List all sessions with user data."""
        _p(f"\n{C_BOLD}  Sessions:{C_RESET}")
        found = False
        for aid, cfg in self._accounts_cfg.items():
            sess_path = cfg.get("session_file", f"data/{aid}.session")
            session = Session(sess_path, self._encryptor)
            exists = session.exists()
            phone = cfg.get("phone", "?")
            status = f"{C_GREEN}active{C_RESET}" if exists else f"{C_DIM}no session{C_RESET}"
            _p(f"    {C_CYAN}{aid}{C_RESET}: {status}  phone={phone}  file={sess_path}")
            if exists and session.load():
                dev = session.device_id[:8] + "..." if session.device_id else "?"
                _p(f"      device={dev}")
            found = True
        if not found:
            _p(f"    {C_DIM}No accounts configured{C_RESET}")
        _p("")

    def cmd_auth(self, account_id: str = "") -> None:
        """Authenticate an account via QR code."""
        aid = account_id or self._pick_account("Authenticate which account?")
        if not aid:
            return
        cfg = self._accounts_cfg.get(aid)
        if not cfg:
            _p(f"{C_RED}Unknown account: {aid}{C_RESET}")
            return

        sess_path = cfg.get("session_file", f"data/{aid}.session")
        session = Session(sess_path, self._encryptor)
        if session.exists():
            r = input(f"  [{aid}] Session already exists. Re-authenticate? [y/N]: ").strip()
            if r.lower() != "y":
                return
            session.clear()

        _p(f"\n  [{aid}] Scan QR code with MAX app on your phone:\n")
        try:
            asyncio.run(self._do_qr_auth(session))
            _p(f"\n  {C_GREEN}[{aid}] Authenticated successfully!{C_RESET}\n")
        except (MaxApiError, MaxConnectionError, RuntimeError) as e:
            _p(f"\n  {C_RED}[{aid}] Auth failed: {e}{C_RESET}\n")

    def cmd_logout(self, account_id: str = "") -> None:
        """Remove a session (logout)."""
        aid = account_id or self._pick_account("Logout which account?")
        if not aid:
            return
        cfg = self._accounts_cfg.get(aid)
        if not cfg:
            _p(f"{C_RED}Unknown account: {aid}{C_RESET}")
            return
        sess_path = cfg.get("session_file", f"data/{aid}.session")
        session = Session(sess_path, self._encryptor)
        if not session.exists():
            _p(f"{C_YELLOW}[{aid}] No session to remove{C_RESET}")
            return
        session.clear()
        _p(f"{C_GREEN}[{aid}] Session removed{C_RESET}")

    async def _do_qr_auth(self, session: Session) -> None:
        client = MaxClient()
        try:
            await client.connect()
            await authenticate_qr(client, session)
        finally:
            await client.disconnect()

    # ── Working with account ────────────────────────────────

    def cmd_chats(self, account_id: str = "") -> None:
        """List channels and dialogs for an account."""
        aid = account_id or self._pick_account("Which account?")
        if not aid:
            return
        try:
            asyncio.run(self._show_chats(aid))
        except (MaxApiError, MaxConnectionError, RuntimeError) as e:
            _p(f"{C_RED}Error: {e}{C_RESET}")

    def cmd_open(self, args: str = "") -> None:
        """Open a chat: load history + listen for new messages."""
        parts = args.split(maxsplit=1)
        chat_id_str = parts[0] if parts else ""
        account_id = parts[1] if len(parts) > 1 else ""

        if not chat_id_str:
            chat_id_str = input("  Chat ID: ").strip()
        if not chat_id_str.isdigit():
            _p(f"{C_RED}Invalid chat ID{C_RESET}")
            return

        chat_id = int(chat_id_str)
        aid = account_id or self._pick_account("Which account?")
        if not aid:
            return
        try:
            asyncio.run(self._open_chat(aid, chat_id))
        except (MaxApiError, MaxConnectionError, RuntimeError) as e:
            _p(f"{C_RED}Error: {e}{C_RESET}")
        except KeyboardInterrupt:
            _p(f"\n{C_DIM}Left chat{C_RESET}")

    async def _show_chats(self, account_id: str) -> None:
        client, session = self._connect_account(account_id)
        try:
            await client.connect()
            await client.login_by_token(session.token, session.device_id)

            # Get recent chats from login response sync data
            await client.invoke_method(
                opcode=Opcode.GET_HISTORY,
                payload={"chatId": 0, "from": 0, "forward": 0, "backward": 0,
                         "getMessages": False},
            )
            # Alternatively use opcode 48 to resolve known chats
            # For now fetch via the initial login chatsSync
            # The login response already has chats — let's use opcode 49
            # with chatId=0 which returns recent dialogs list

            _p(f"\n{C_BOLD}  Chats for [{account_id}]:{C_RESET}\n")
            _p(f"  {C_DIM}(Fetching from login sync data...){C_RESET}")

            # Login response sends chats as part of sync
            # We need to listen for incoming packets
            chats_received = []

            async def capture(cli, pkt):
                p = pkt.get("payload", {})
                if pkt.get("opcode") == 128:
                    chats_received.append(p)

            client.set_packet_callback(capture)
            await asyncio.sleep(2)  # wait for sync packets

            # Try explicit chat list request
            # MAX doesn't have a "list all chats" opcode,
            # but we can check what came during login sync
            # The chats come as part of opcode 19 response
            _p(f"\n  {C_DIM}Use 'open <chat_id>' to enter a chat.{C_RESET}")
            _p(f"  {C_DIM}Chat IDs are visible in incoming messages.{C_RESET}\n")

        finally:
            await client.disconnect()

    async def _open_chat(self, account_id: str, chat_id: int) -> None:
        client, session = self._connect_account(account_id)
        try:
            await client.connect()
            await client.login_by_token(session.token, session.device_id)

            # Get chat info
            chat_resp = await client.invoke_method(
                opcode=Opcode.RESOLVE_CHAT, payload={"chatIds": [chat_id]},
            )
            chat_payload = (chat_resp.get("payload") or {})
            chats = chat_payload.get("chats", [])
            chat_name = "?"
            chat_type = "?"
            if chats:
                c = chats[0]
                chat_name = c.get("title") or c.get("name") or "DM"
                chat_type = c.get("type", "?")
            _p(f"\n{C_BOLD}  Chat: {chat_name} ({chat_type}) [id:{chat_id}]{C_RESET}")

            # Load history
            hist_resp = await client.invoke_method(
                opcode=Opcode.GET_HISTORY,
                payload={"chatId": chat_id, "from": 0, "forward": 0,
                         "backward": 20, "getMessages": True},
            )
            messages = (hist_resp.get("payload") or {}).get("messages", [])
            if messages:
                _p(f"  {C_DIM}--- Last {len(messages)} messages ---{C_RESET}")
                # Resolve sender names
                sender_ids = {m.get("sender") for m in messages
                              if isinstance(m.get("sender"), int)}
                names = {}
                if sender_ids:
                    ur = await client.invoke_method(
                        opcode=Opcode.RESOLVE_USERS,
                        payload={"contactIds": list(sender_ids)},
                    )
                    for c in (ur.get("payload") or {}).get("contacts", []):
                        uid = c.get("id") or c.get("userId")
                        names[uid] = _extract_user_name(c) or str(uid)

                for msg in reversed(messages):
                    sid = msg.get("sender")
                    name = names.get(sid, str(sid))
                    text = msg.get("text", "")
                    _p(f"  {C_CYAN}{name}{C_RESET}: {text}")

            _p(f"\n  {C_DIM}Listening for new messages (Ctrl+C to exit)...{C_RESET}\n")

            # Listen for new messages
            async def on_message(cli, packet):
                p = packet.get("payload") or {}
                if packet.get("opcode") == 128 and p.get("chatId") == chat_id:
                    msg = p.get("message", {})
                    sid = msg.get("sender")
                    text = msg.get("text", "")
                    # Quick resolve
                    name = names.get(sid)
                    if not name and isinstance(sid, int):
                        try:
                            ur = await cli.invoke_method(
                                opcode=Opcode.RESOLVE_USERS,
                                payload={"contactIds": [sid]},
                            )
                            for c in (ur.get("payload") or {}).get("contacts", []):
                                name = _extract_user_name(c)
                                names[sid] = name
                        except Exception:
                            name = str(sid)
                    _p(f"  {C_CYAN}{name or sid}{C_RESET}: {text}")

            client.set_packet_callback(on_message)
            await asyncio.Event().wait()  # block forever

        finally:
            await client.disconnect()

    def _connect_account(self, account_id: str) -> tuple[MaxClient, Session]:
        """Create client + load session for an account."""
        cfg = self._accounts_cfg.get(account_id)
        if not cfg:
            raise RuntimeError(f"Unknown account: {account_id}")
        sess_path = cfg.get("session_file", f"data/{account_id}.session")
        session = Session(sess_path, self._encryptor)
        if not session.load():
            raise RuntimeError(f"No session for '{account_id}'. Run 'auth' first.")
        return MaxClient(), session

    # ── Helpers ─────────────────────────────────────────────

    def _pick_account(self, prompt: str) -> str:
        """Pick an account — auto-select if only one."""
        ids = list(self._accounts_cfg.keys())
        if not ids:
            _p(f"{C_RED}No accounts configured{C_RESET}")
            return ""
        if len(ids) == 1:
            return ids[0]
        _p(f"\n  {prompt}")
        for i, aid in enumerate(ids, 1):
            _p(f"    {i}. {aid}")
        choice = input("  > ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(ids):
            return ids[int(choice) - 1]
        if choice in ids:
            return choice
        _p(f"{C_RED}Invalid choice{C_RESET}")
        return ""

    def _has_any_session(self) -> bool:
        """Check if at least one account has a saved session."""
        for aid, cfg in self._accounts_cfg.items():
            sess_path = cfg.get("session_file", f"data/{aid}.session")
            if Path(sess_path).exists() and Path(sess_path).stat().st_size > 0:
                return True
        return False

    # ── Main loop ───────────────────────────────────────────

    def run(self) -> None:
        """Main interactive loop."""
        _header()

        # Check if any sessions exist
        if not self._has_any_session():
            _p(f"{C_YELLOW}  No authenticated sessions found.{C_RESET}")
            _p(f"  {C_DIM}Starting QR authentication...{C_RESET}")
            self.cmd_auth()

        commands = {
            "start": (self.cmd_start, "Start daemon in background"),
            "stop": (self.cmd_stop, "Stop the daemon"),
            "restart": (self.cmd_restart, "Restart the daemon"),
            "status": (self.cmd_status, "Daemon status"),
            "logs": (self.cmd_logs, "Run daemon in foreground with debug logs"),
            "sessions": (self.cmd_sessions, "List all sessions"),
            "auth": (self.cmd_auth, "Authenticate an account (QR)"),
            "logout": (self.cmd_logout, "Remove a session"),
            "chats": (self.cmd_chats, "List chats for an account"),
            "open": (self.cmd_open, "Open chat: history + live messages"),
            "help": (None, "Show this help"),
            "exit": (None, "Exit commander"),
        }

        while True:
            try:
                raw = input(f"{C_BOLD}maxbridge>{C_RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                _p(f"\n{C_DIM}Bye{C_RESET}")
                break

            if not raw:
                continue

            parts = raw.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "exit" or cmd == "quit" or cmd == "q":
                _p(f"{C_DIM}Bye{C_RESET}")
                break
            elif cmd == "help" or cmd == "?":
                _p(f"\n{C_BOLD}  Commands:{C_RESET}")
                for name, (_, desc) in commands.items():
                    _p(f"    {C_CYAN}{name:12}{C_RESET} {desc}")
                _p("")
            elif cmd in commands:
                fn = commands[cmd][0]
                if fn:
                    try:
                        if cmd in ("auth", "logout", "chats"):
                            fn(arg)
                        elif cmd == "open":
                            fn(arg)
                        else:
                            fn()
                    except Exception as e:
                        _p(f"{C_RED}Error: {e}{C_RESET}")
            else:
                _p(f"{C_DIM}Unknown command: {cmd}. Type 'help'.{C_RESET}")


def commander_entry() -> None:
    """Entry point for maxbridge-commander CLI."""
    config = load_config()
    cmd = Commander(config)
    cmd.run()


if __name__ == "__main__":
    commander_entry()
