"""QR-code based authentication for MAX.

Flow: request QR → user scans → poll returns loginAvailable=true → call LOGIN_BY_QR.
"""

import asyncio
import getpass
import logging
import struct
import sys
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from maxbridge.auth.session import Session
from maxbridge.protocol.max_client import MaxClient

logger = logging.getLogger("maxbridge.auth.qr")

PasswordProvider = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class QrAuthSession:
    """Parameters required to complete a QR auth session."""

    qr_link: str
    track_id: str
    ttl: float
    poll_interval: float


async def authenticate_qr(client: MaxClient, session: Session) -> dict[str, Any]:
    """Run QR code auth flow: display QR, wait for scan, login, save session."""
    qr_session = await request_qr_session(client)

    # Step 2: Display QR
    _display_qr(qr_session.qr_link)
    logger.info(
        "QR code displayed. Waiting for scan (%.0fs timeout)...",
        qr_session.ttl,
    )

    # Step 3: Poll until loginAvailable=true
    scanned = await poll_qr_until_scanned(client, qr_session, verbose=True)
    if not scanned:
        raise RuntimeError("QR code expired. Run --auth-only again.")

    # Step 4: Complete login via opcode 291
    print("  Completing login...")
    return await complete_qr_auth(client, qr_session, session)


async def request_qr_session(client: MaxClient) -> QrAuthSession:
    """Request QR auth parameters from MAX."""
    qr_data = await client.request_qr()
    return QrAuthSession(
        qr_link=qr_data["qrLink"],
        track_id=qr_data["trackId"],
        ttl=qr_data.get("ttl", 120000) / 1000,
        poll_interval=qr_data.get("pollingInterval", 5000) / 1000,
    )


async def poll_qr_until_scanned(client: MaxClient,
                                qr_session: QrAuthSession,
                                verbose: bool = False) -> bool:
    """Poll QR status until loginAvailable=true or expired."""
    elapsed = 0.0
    while elapsed < qr_session.ttl:
        await asyncio.sleep(qr_session.poll_interval)
        elapsed += qr_session.poll_interval

        try:
            status = await client.check_qr_status(qr_session.track_id)
        except Exception as e:
            logger.debug("Poll error: %s", e)
            continue

        payload = status
        status_info = payload.get("status", {})

        # Check for loginAvailable — user scanned and confirmed
        if isinstance(status_info, dict) and status_info.get("loginAvailable"):
            if verbose:
                print("\n  QR scanned! Logging in...")
            return True

        # Check for error (expired/not found)
        error = payload.get("error") or (
            status_info.get("error") if isinstance(status_info, dict) else None
        )
        if error and ("not.found" in str(error) or "expired" in str(error)):
            if verbose:
                print("\n  QR code expired.")
            return False

        if verbose:
            remaining = int(qr_session.ttl - elapsed)
            sys.stdout.write(f"\r  Waiting for QR scan... {remaining}s remaining  ")
            sys.stdout.flush()

    if verbose:
        print("\n  Timeout.")
    return False


async def complete_qr_auth(client: MaxClient, qr_session: QrAuthSession,
                           session: Session,
                           password_provider: PasswordProvider | None = None) -> dict[str, Any]:
    """Complete QR login and persist the new session."""
    login_response = await client.login_by_qr(qr_session.track_id)
    challenge = client.extract_password_challenge(login_response)
    auth_response = login_response

    if challenge:
        logger.warning(
            "QR auth requires MAX password challenge: keys=%s",
            list(challenge.keys()),
        )
        password = await _resolve_password(
            challenge,
            password_provider=password_provider,
        )
        track_id = str(challenge.get("trackId") or qr_session.track_id)
        auth_response = await client.check_password(track_id, password)

    login_token = client.extract_login_token(auth_response)
    session.save(client.device_id, login_token)
    logger.info("QR authentication successful")
    return auth_response


def render_qr_text(url: str) -> str:
    """Render QR code as a block-text string for Telegram/terminal fallback."""
    try:
        import qrcode

        qr = qrcode.QRCode(box_size=1, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
    except ImportError:
        return url

    lines = []
    for r in range(0, len(matrix) - 1, 2):
        line = ""
        for c in range(len(matrix[r])):
            top = matrix[r][c]
            bot = matrix[r + 1][c] if r + 1 < len(matrix) else False
            if top and bot:
                line += "\u2588"
            elif top and not bot:
                line += "\u2580"
            elif not top and bot:
                line += "\u2584"
            else:
                line += " "
        lines.append(line.rstrip())
    return "\n".join(lines)


def render_qr_png(url: str) -> bytes | None:
    """Render QR code to PNG bytes for Telegram/photo delivery."""
    try:
        import qrcode
    except ImportError:
        return None

    qr = qrcode.QRCode(box_size=1, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    if not matrix:
        return None
    return _matrix_to_png(matrix, scale=8)


def _display_qr(url: str) -> None:
    """Render QR code in terminal."""
    try:
        print("\n  Scan this QR code with the MAX app on your phone:\n")
        for line in render_qr_text(url).splitlines():
            print(f"  {line}")
        print()
        print(f"  Or open this link on your phone: {url}")
        print()
    except ImportError:
        print(f"\n  Open this link on your phone: {url}\n")


async def _resolve_password(
    challenge: dict[str, Any],
    password_provider: PasswordProvider | None,
) -> str:
    if password_provider is not None:
        password = (await password_provider(challenge)).strip()
    else:
        password = (await _prompt_password_in_terminal(challenge)).strip()

    if not password:
        raise RuntimeError("MAX account password was empty")
    return password


async def _prompt_password_in_terminal(challenge: dict[str, Any]) -> str:
    hint = challenge.get("hint") or "нет"
    email = challenge.get("email") or "не указан"
    prompt = (
        "\nMAX запросил пароль аккаунта для завершения входа.\n"
        f"Подсказка: {hint}\n"
        f"Email: {email}\n"
        "Введите пароль: "
    )
    return await asyncio.to_thread(getpass.getpass, prompt)


def _matrix_to_png(matrix: list[list[bool]], scale: int) -> bytes:
    width = len(matrix[0]) * scale
    height = len(matrix) * scale

    rows = bytearray()
    for source_row in matrix:
        expanded = bytearray()
        for cell in source_row:
            color = 0 if cell else 255
            expanded.extend([color] * scale)
        scanline = b"\x00" + bytes(expanded)
        for _ in range(scale):
            rows.extend(scanline)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    idat = zlib.compress(bytes(rows), level=9)
    return header + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", crc)
    )
