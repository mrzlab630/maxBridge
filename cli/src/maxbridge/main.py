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
from maxbridge.config import ResolvedConfig, get_nested, load_config
from maxbridge.handlers.attachment import create_upload_complete_handler
from maxbridge.handlers.message import create_message_handler
from maxbridge.ipc.methods import RpcMethods
from maxbridge.ipc.server import IpcServer
from maxbridge.ipc.stats import StatsCollector, StatsLogHandler
from maxbridge.protocol.errors import MaxApiError, MaxConnectionError
from maxbridge.telegram.config import load_telegram_config_snapshot, telegram_config_path
from maxbridge.telegram.control_bot import TelegramControlBot
from maxbridge.telegram.forwarder import TelegramForwarder
from maxbridge.utils.constants import Opcode
from maxbridge.utils.logger import redact_secrets, setup_error_logging, setup_logging

logger = logging.getLogger("maxbridge.main")

_TELEGRAM_CONFIG_POLL_SECONDS = 1.0


async def _drain_background_tasks(
    loop: asyncio.AbstractEventLoop,
    *,
    shutdown_executor: bool = True,
) -> None:
    current = asyncio.current_task()
    pending = [
        task
        for task in asyncio.all_tasks(loop)
        if task is not current and not task.done()
    ]
    if pending:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    await loop.shutdown_asyncgens()
    if shutdown_executor:
        await loop.shutdown_default_executor()


def _register_configured_accounts(manager: AccountManager, config: dict) -> None:
    accounts_cfg = config.get("accounts", {})
    if not accounts_cfg:
        max_cfg = config.get("max", {})
        if max_cfg:
            accounts_cfg = {"default": max_cfg}
    for account_id, acc_config in accounts_cfg.items():
        manager.add_account(account_id, acc_config)


class TerminalAuthenticator:
    """Terminal-only MAX authentication without daemon or Telegram resources."""

    def __init__(self, config: dict) -> None:
        key_path = get_nested(config, "security.key_file", "data/master.key")
        encryptor = TokenEncryptor(key_path)
        encryptor.ensure_key()
        self._manager = AccountManager(encryptor)
        _register_configured_accounts(self._manager, config)

    async def authenticate(self, account_id: str | None = None) -> None:
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
            except Exception as exc:
                raise RuntimeError(f"[{aid}] QR auth failed: {exc}") from exc
            logger.info("Account '%s' authenticated successfully", aid)

    def _get_auth_targets(self, account_id: str | None) -> list[str]:
        if account_id:
            self._manager.require(account_id)
            return [account_id]
        return self._manager.account_ids


class MaxBridgeDaemon:
    """Main daemon: multi-account, entity cache, stats, IPC server."""

    def __init__(self, config: dict) -> None:
        if not isinstance(config, ResolvedConfig):
            raise RuntimeError("MaxBridgeDaemon requires resolved configuration paths")
        self._config = config
        self._shutdown_event = asyncio.Event()
        self._pid_fd: int | None = None
        self._pid_identity: tuple[int, int, int] | None = None
        self._pid_path: Path | None = None
        self._telegram_error_handler: logging.Handler | None = None
        self._stats_error_handler: logging.Handler | None = None
        self._telegram_watch_task: asyncio.Task | None = None

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
        self._telegram_config_path = telegram_config_path(config.telegram_config_path)
        self._telegram = TelegramForwarder(
            self._event_bus,
            self._manager,
            config_path=self._telegram_config_path,
        )
        self._control_bot = TelegramControlBot(self._manager, self._stats)
        self._control_bot.set_status_provider(self._runtime_status)
        self._manager.set_on_auth_required(self._control_bot.request_auth_nowait)
        self._register_accounts(config)

    def _register_accounts(self, config: dict) -> None:
        _register_configured_accounts(self._manager, config)

    async def start(self) -> None:
        logger.info("maxBridge v%s starting (%d accounts)...",
                     __version__, len(self._manager.account_ids))
        self._write_pid()
        await self._telegram.start()
        await self._control_bot.start()
        self._attach_error_notifications()
        self._start_telegram_config_watch()

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
            attachment_handler = create_upload_complete_handler(
                self._event_bus,
                connection=account.connection,
                account_id=account_id,
                listen_chats=listen_chats,
                stats=self._stats,
                entity_cache=self._entity_cache,
            )
            self._router.on_opcode(Opcode.INCOMING_MESSAGE, handler)
            self._router.on_opcode(Opcode.UPLOAD_COMPLETE, attachment_handler)

        self._manager.set_packet_callback(self._router.dispatch)
        await self._manager.connect_all()
        await self._ipc_server.start()

        logger.info("maxBridge is running. Waiting for messages...")
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        logger.info("Shutting down maxBridge...")
        await self._stop_telegram_config_watch()
        await self._control_bot.stop()
        await self._telegram.stop()
        await self._ipc_server.stop()
        await self._manager.disconnect_all()
        self._remove_pid()
        self._detach_error_notifications()
        logger.info("maxBridge stopped.")

    def _write_pid(self) -> None:
        pid_path = self._validated_pid_path()
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        if pid_path.is_symlink():
            raise OSError(f"PID path {pid_path} is a symlink — refusing")
        fd = os.open(
            str(pid_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise RuntimeError(f"Another instance is running (PID locked: {pid_path})")
        except Exception:
            os.close(fd)
            raise
        try:
            pid = os.getpid()
            stat_result = os.fstat(fd)
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(pid).encode())
            os.fsync(fd)
        except Exception:
            os.close(fd)
            raise
        self._pid_fd = fd
        self._pid_identity = (stat_result.st_dev, stat_result.st_ino, pid)
        self._pid_path = pid_path

    def _remove_pid(self) -> None:
        fd = self._pid_fd
        identity = self._pid_identity
        pid_path = self._pid_path
        if fd is None or identity is None or pid_path is None:
            return
        self._pid_fd = None
        self._pid_identity = None
        self._pid_path = None
        try:
            try:
                descriptor_stat = os.fstat(fd)
                path_stat = os.stat(pid_path, follow_symlinks=False)
            except OSError:
                return
            descriptor_identity = (descriptor_stat.st_dev, descriptor_stat.st_ino)
            if descriptor_identity != identity[:2]:
                return
            if (path_stat.st_dev, path_stat.st_ino) == identity[:2]:
                pid_path.unlink()
        finally:
            os.close(fd)

    def _validated_pid_path(self) -> Path:
        value = get_nested(self._config, "daemon.pid_file", "/tmp/maxbridge.pid")
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("daemon.pid_file must be a non-empty absolute path")
        path = Path(value)
        if not path.is_absolute():
            raise RuntimeError("daemon.pid_file must be a non-empty absolute path")
        return path

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def _attach_error_notifications(self) -> None:
        root = logging.getLogger("maxbridge")
        fmt = get_nested(
            self._config,
            "logging.format",
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        self._sync_telegram_error_handler(fmt)

        if self._stats_error_handler is None:
            self._stats_error_handler = StatsLogHandler(self._stats)
            self._stats_error_handler.setFormatter(logging.Formatter(fmt))
            root.addHandler(self._stats_error_handler)

    def _sync_telegram_error_handler(self, fmt: str | None = None) -> None:
        root = logging.getLogger("maxbridge")
        if self._telegram.is_ready and self._telegram_error_handler is None:
            if fmt is None:
                fmt = get_nested(
                    self._config,
                    "logging.format",
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                )
            self._telegram_error_handler = self._telegram.make_log_handler(fmt)
            root.addHandler(self._telegram_error_handler)
        elif not self._telegram.is_ready and self._telegram_error_handler is not None:
            root.removeHandler(self._telegram_error_handler)
            self._telegram_error_handler = None

    def _start_telegram_config_watch(self) -> None:
        if self._telegram_watch_task is None or self._telegram_watch_task.done():
            self._telegram_watch_task = asyncio.create_task(
                self._watch_telegram_config(),
                name="telegram-config-watch",
            )

    async def _stop_telegram_config_watch(self) -> None:
        task = self._telegram_watch_task
        self._telegram_watch_task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _reload_telegram_config_if_changed(self) -> bool:
        snapshot = load_telegram_config_snapshot(self._telegram_config_path)
        if snapshot.fingerprint == self._telegram.observed_fingerprint:
            return False
        changed = await self._telegram.reload(snapshot)
        if changed:
            self._sync_telegram_error_handler()
        return changed

    async def _watch_telegram_config(self) -> None:
        while True:
            try:
                await asyncio.sleep(_TELEGRAM_CONFIG_POLL_SECONDS)
                await self._reload_telegram_config_if_changed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Telegram config watcher failed; keeping current state: %s",
                    type(exc).__name__,
                )

    def _detach_error_notifications(self) -> None:
        root = logging.getLogger("maxbridge")
        if self._telegram_error_handler is not None:
            root.removeHandler(self._telegram_error_handler)
            self._telegram_error_handler = None
        if self._stats_error_handler is not None:
            root.removeHandler(self._stats_error_handler)
            self._stats_error_handler = None

    def _runtime_status(self) -> dict[str, dict[str, object]]:
        return {
            "daemon": {
                "running": True,
                "shutdown_requested": self._shutdown_event.is_set(),
            },
            "telegram": {
                "control_bot_ready": self._control_bot.is_ready,
                "control_bot": self._control_bot.polling_health,
                "forwarder_ready": self._telegram.is_ready,
                "alerts_enabled": self._telegram_error_handler is not None,
            },
            "bridge": {
                "ipc_running": self._ipc_server.is_running,
                "ipc_clients": self._ipc_server.client_count,
                "subscribers": self._event_bus.subscriber_count,
            },
        }


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
    runtime_root = Path(getattr(config, "runtime_root", Path.cwd()))
    os.chdir(runtime_root)

    # --debug overrides config logging level
    log_level = "DEBUG" if args.debug else get_nested(config, "logging.level", "WARNING")
    setup_logging(
        level=log_level,
        log_file=get_nested(config, "logging.file"),
        fmt=get_nested(config, "logging.format",
                       "%(asctime)s [%(levelname)s] %(name)s: %(message)s"),
    )
    try:
        setup_error_logging(runtime_root)
    except OSError:
        logger.exception("Canonical daemon error log is unavailable")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    daemon: MaxBridgeDaemon | None = None
    normal_daemon_entered = False
    daemon_lifecycle_started = False

    def _handle_loop_exception(_loop: asyncio.AbstractEventLoop,
                               context: dict) -> None:
        exc = context.get("exception")
        msg = context.get("message", "Unhandled asyncio exception")
        if exc is not None:
            logger.error(msg, exc_info=exc)
        else:
            logger.error(msg)

    loop.set_exception_handler(_handle_loop_exception)
    try:
        if args.auth_only:
            authenticator = TerminalAuthenticator(config)
            loop.run_until_complete(authenticator.authenticate(args.account))
            print("Authentication complete. Session(s) saved.")
        else:
            normal_daemon_entered = True
            daemon = MaxBridgeDaemon(config)
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, daemon.request_shutdown)
            daemon_lifecycle_started = True
            loop.run_until_complete(daemon.start())
    except KeyboardInterrupt:
        pass
    except (MaxApiError, MaxConnectionError) as e:
        logger.exception("MAX connection/auth error")
        print(f"\n[ERROR] {redact_secrets(e)}")
    except RuntimeError as e:
        logger.exception("Runtime error in maxBridge")
        print(f"\n[ERROR] {redact_secrets(e)}")
    except Exception as e:
        logger.exception("Unhandled maxBridge error")
        print(f"\n[ERROR] {redact_secrets(e)}")
    finally:
        try:
            if normal_daemon_entered:
                try:
                    if daemon_lifecycle_started and daemon is not None:
                        loop.run_until_complete(daemon.stop())
                finally:
                    loop.run_until_complete(_drain_background_tasks(loop))
            else:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


if __name__ == "__main__":
    cli_entry()
