"""maxBridge entry point — multi-account daemon with all features."""

import argparse
import asyncio
import fcntl
import logging
import os
import signal
from pathlib import Path

from maxbridge import __version__
from maxbridge.auth.encryption import TokenEncryptor
from maxbridge.bridge.event_bus import EventBus
from maxbridge.cache.entity_cache import EntityCache
from maxbridge.client.account_manager import AccountManager
from maxbridge.client.event_router import EventRouter
from maxbridge.config import get_nested, load_config
from maxbridge.handlers.message import create_message_handler
from maxbridge.ipc.methods import RpcMethods
from maxbridge.ipc.server import IpcServer
from maxbridge.ipc.stats import StatsCollector
from maxbridge.protocol.errors import MaxApiError, MaxConnectionError
from maxbridge.telegram.forwarder import TelegramForwarder
from maxbridge.utils.constants import Opcode
from maxbridge.utils.logger import setup_logging

logger = logging.getLogger("maxbridge.main")


class MaxBridgeDaemon:
    """Main daemon: multi-account, entity cache, stats, IPC server."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._shutdown_event = asyncio.Event()
        self._pid_fd: int | None = None

        key_path = get_nested(config, "security.key_file", "data/master.key")
        self._encryptor = TokenEncryptor(key_path)
        self._encryptor.ensure_key()

        self._manager = AccountManager(self._encryptor)
        self._manager.set_on_fatal(self.request_shutdown)

        self._entity_cache = EntityCache(
            ttl=get_nested(config, "bridge.cache_ttl", 600),
        )
        self._event_bus = EventBus()
        self._router = EventRouter()
        self._stats = StatsCollector()
        self._rpc_methods = RpcMethods(self._manager, self._event_bus, self._stats)
        self._ipc_server = IpcServer(
            methods=self._rpc_methods,
            event_bus=self._event_bus,
            config=config.get("ipc", {}),
            stats=self._stats,
        )
        self._telegram = TelegramForwarder(self._event_bus, self._manager)
        self._register_accounts(config)

    def _register_accounts(self, config: dict) -> None:
        accounts_cfg = config.get("accounts", {})
        if not accounts_cfg:
            max_cfg = config.get("max", {})
            if max_cfg:
                accounts_cfg = {"default": max_cfg}
        for account_id, acc_config in accounts_cfg.items():
            self._manager.add_account(account_id, acc_config)

    async def start(self) -> None:
        logger.info("maxBridge v%s starting (%d accounts)...",
                     __version__, len(self._manager.account_ids))
        self._write_pid()

        listen_chats = get_nested(self._config, "bridge.listen_chats", "all")
        for account_id in self._manager.account_ids:
            account = self._manager.require(account_id)
            handler = create_message_handler(
                self._event_bus,
                connection=account.connection,
                account_id=account_id,
                listen_chats=listen_chats,
                stats=self._stats,
                entity_cache=self._entity_cache,
            )
            self._router.on_opcode(Opcode.INCOMING_MESSAGE, handler)

        self._manager.set_packet_callback(self._router.dispatch)
        await self._manager.connect_all()
        await self._ipc_server.start()
        await self._telegram.start()

        logger.info("maxBridge is running. Waiting for messages...")
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        logger.info("Shutting down maxBridge...")
        await self._telegram.stop()
        await self._ipc_server.stop()
        await self._manager.disconnect_all()
        self._remove_pid()
        logger.info("maxBridge stopped.")

    async def authenticate(self, account_id: str | None = None) -> None:
        """Authenticate accounts. Uses QR code (primary) or SMS (fallback)."""
        targets = self._get_auth_targets(account_id)
        for aid in targets:
            account = self._manager.require(aid)
            if account.has_session():
                logger.info("Account '%s' already has a session — skipping", aid)
                continue

            print(f"\n[{aid}] Authenticating via QR code...")
            print(f"[{aid}] Open MAX on your phone and scan the QR code.\n")
            try:
                await account.authenticate_qr()
            except (MaxApiError, MaxConnectionError):
                raise
            except Exception as e:
                raise RuntimeError(f"[{aid}] QR auth failed: {e}") from e
            logger.info("Account '%s' authenticated successfully", aid)

    def _get_auth_targets(self, account_id: str | None) -> list[str]:
        if account_id:
            self._manager.require(account_id)
            return [account_id]
        return self._manager.account_ids

    def _write_pid(self) -> None:
        pid_path = Path(get_nested(self._config, "daemon.pid_file", "/tmp/maxbridge.pid"))
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        if pid_path.is_symlink():
            raise OSError(f"PID path {pid_path} is a symlink — refusing")
        self._pid_fd = os.open(
            str(pid_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._pid_fd)
            self._pid_fd = None
            raise RuntimeError(f"Another instance is running (PID locked: {pid_path})")
        os.write(self._pid_fd, str(os.getpid()).encode())
        os.fsync(self._pid_fd)

    def _remove_pid(self) -> None:
        pid_path = get_nested(self._config, "daemon.pid_file", "/tmp/maxbridge.pid")
        if self._pid_fd is not None:
            try:
                fcntl.flock(self._pid_fd, fcntl.LOCK_UN)
                os.close(self._pid_fd)
            except Exception:
                pass
            self._pid_fd = None
        try:
            Path(pid_path).unlink()
        except FileNotFoundError:
            pass

    def request_shutdown(self) -> None:
        self._shutdown_event.set()


def cli_entry() -> None:
    parser = argparse.ArgumentParser(
        prog="maxbridge",
        description="MAX Messenger bridge daemon (multi-account)",
    )
    parser.add_argument("-c", "--config", help="Path to config YAML", default=None)
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug output")
    parser.add_argument("--auth-only", action="store_true", help="QR auth only")
    parser.add_argument("--account", help="Target account ID", default=None)
    parser.add_argument("--generate-token", action="store_true",
                        help="Generate a random auth secret and print it")
    args = parser.parse_args()

    if args.generate_token:
        import secrets
        print(secrets.token_hex(32))
        return

    config = load_config(args.config)

    # --debug overrides config logging level
    log_level = "DEBUG" if args.debug else get_nested(config, "logging.level", "WARNING")
    setup_logging(
        level=log_level,
        log_file=get_nested(config, "logging.file"),
        fmt=get_nested(config, "logging.format",
                       "%(asctime)s [%(levelname)s] %(name)s: %(message)s"),
    )
    daemon = MaxBridgeDaemon(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, daemon.request_shutdown)
    try:
        if args.auth_only:
            loop.run_until_complete(daemon.authenticate(args.account))
            print("Authentication complete. Session(s) saved.")
        else:
            loop.run_until_complete(daemon.start())
    except KeyboardInterrupt:
        pass
    except (MaxApiError, MaxConnectionError) as e:
        print(f"\n[ERROR] {e}")
    except RuntimeError as e:
        print(f"\n[ERROR] {e}")
    finally:
        loop.run_until_complete(daemon.stop())
        loop.close()


if __name__ == "__main__":
    cli_entry()
