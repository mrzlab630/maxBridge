"""QR-code based authentication for MAX.

Flow: request QR → user scans → poll returns loginAvailable=true → call LOGIN_BY_QR.
"""

import asyncio
import logging
import sys
from typing import Any

from maxbridge.auth.session import Session
from maxbridge.protocol.max_client import MaxClient

logger = logging.getLogger("maxbridge.auth.qr")


async def authenticate_qr(client: MaxClient, session: Session) -> dict[str, Any]:
    """Run QR code auth flow: display QR, wait for scan, login, save session."""
    # Step 1: Request QR
    qr_data = await client.request_qr()
    qr_link = qr_data["qrLink"]
    track_id = qr_data["trackId"]
    ttl = qr_data.get("ttl", 120000) / 1000
    poll_interval = qr_data.get("pollingInterval", 5000) / 1000

    # Step 2: Display QR
    _display_qr(qr_link)
    logger.info("QR code displayed. Waiting for scan (%.0fs timeout)...", ttl)

    # Step 3: Poll until loginAvailable=true
    scanned = await _poll_until_scanned(client, track_id, ttl, poll_interval)
    if not scanned:
        raise RuntimeError("QR code expired. Run --auth-only again.")

    # Step 4: Complete login via opcode 291
    print("  Completing login...")
    login_response = await client.login_by_qr(track_id)

    # Step 5: Save session
    login_token = client.extract_login_token(login_response)
    session.save(client.device_id, login_token)
    logger.info("QR authentication successful")
    return login_response


async def _poll_until_scanned(client: MaxClient, track_id: str,
                              ttl: float, interval: float) -> bool:
    """Poll QR status until loginAvailable=true or expired."""
    elapsed = 0.0
    while elapsed < ttl:
        await asyncio.sleep(interval)
        elapsed += interval

        try:
            status = await client.check_qr_status(track_id)
        except Exception as e:
            logger.debug("Poll error: %s", e)
            continue

        payload = status
        status_info = payload.get("status", {})

        # Check for loginAvailable — user scanned and confirmed
        if isinstance(status_info, dict) and status_info.get("loginAvailable"):
            print("\n  QR scanned! Logging in...")
            return True

        # Check for error (expired/not found)
        error = payload.get("error") or (
            status_info.get("error") if isinstance(status_info, dict) else None
        )
        if error and ("not.found" in str(error) or "expired" in str(error)):
            print("\n  QR code expired.")
            return False

        remaining = int(ttl - elapsed)
        sys.stdout.write(f"\r  Waiting for QR scan... {remaining}s remaining  ")
        sys.stdout.flush()

    print("\n  Timeout.")
    return False


def _display_qr(url: str) -> None:
    """Render QR code in terminal."""
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=1, border=2)
        qr.add_data(url)
        qr.make(fit=True)

        print("\n  Scan this QR code with the MAX app on your phone:\n")
        matrix = qr.get_matrix()
        for r in range(0, len(matrix) - 1, 2):
            line = "  "
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
            print(line)
        print()
        print(f"  Or open this link on your phone: {url}")
        print()
    except ImportError:
        print(f"\n  Open this link on your phone: {url}\n")
