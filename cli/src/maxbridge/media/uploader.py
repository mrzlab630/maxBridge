"""Media upload orchestrator for MAX messenger — native protocol."""

import logging
from pathlib import Path
from typing import Any

import aiohttp

from maxbridge.client.connection import MaxConnection
from maxbridge.utils.constants import MAX_UPLOAD_SIZE, Opcode

logger = logging.getLogger("maxbridge.media.uploader")

_DEFAULT_ALLOWED_DIRS: list[Path] = [Path.cwd() / "uploads"]


async def upload_and_send_photo(conn: MaxConnection, chat_id: int,
                                file_path: str,
                                caption: str = "") -> dict[str, Any]:
    """Upload photo via MAX upload flow and send as message."""
    _validate_file(file_path)
    p = Path(file_path)

    # Step 1: Request upload URL (opcode 80)
    slot = await conn.client.invoke_method(
        opcode=Opcode.UPLOAD_PHOTO,
        payload={"chatId": chat_id},
    )
    upload_url = slot.get("payload", {}).get("uploadUrl", "")
    if not upload_url:
        raise RuntimeError("MAX did not return upload URL for photo")

    # Step 2: Upload file via HTTP POST
    async with aiohttp.ClientSession() as session:
        with open(p, "rb") as f:
            data = aiohttp.FormData()
            data.add_field("file", f, filename=p.name)
            async with session.post(upload_url, data=data) as resp:
                upload_result = await resp.json()

    # Step 3: Send message with photo attachment
    photo_token = upload_result.get("photoToken") or upload_result.get("token", "")
    result = await conn.send_message(chat_id, caption, attaches=[
        {"_type": "PHOTO", "photoToken": photo_token},
    ])
    logger.info("Photo sent to chat %d", chat_id)
    return {"sent": True, "type": "photo", "result": str(result)}


async def upload_and_send_file(conn: MaxConnection, chat_id: int,
                               file_path: str,
                               caption: str = "") -> dict[str, Any]:
    """Upload file via MAX upload flow and send as message."""
    _validate_file(file_path)
    p = Path(file_path)

    # Step 1: Request upload URL (opcode 87)
    slot = await conn.client.invoke_method(
        opcode=Opcode.UPLOAD_FILE,
        payload={"chatId": chat_id, "fileName": p.name, "fileSize": p.stat().st_size},
    )
    upload_url = slot.get("payload", {}).get("uploadUrl", "")
    if not upload_url:
        raise RuntimeError("MAX did not return upload URL for file")

    # Step 2: Upload via HTTP POST
    async with aiohttp.ClientSession() as session:
        with open(p, "rb") as f:
            data = aiohttp.FormData()
            data.add_field("file", f, filename=p.name)
            async with session.post(upload_url, data=data) as resp:
                upload_result = await resp.json()

    # Step 3: Send with file attachment
    file_id = upload_result.get("fileId") or upload_result.get("id", 0)
    result = await conn.send_message(chat_id, caption, attaches=[
        {"_type": "FILE", "fileId": file_id},
    ])
    logger.info("File sent to chat %d", chat_id)
    return {"sent": True, "type": "file", "result": str(result)}


async def get_download_url(conn: MaxConnection, chat_id: int,
                           message_id: str, file_id: int,
                           media_type: str = "file") -> str:
    """Get direct download URL for a media attachment."""
    if media_type not in ("file", "video"):
        raise ValueError(f"Invalid media_type: {media_type}")
    opcode = Opcode.DOWNLOAD_VIDEO if media_type == "video" else Opcode.DOWNLOAD_FILE
    id_key = "videoId" if media_type == "video" else "fileId"
    result = await conn.client.invoke_method(
        opcode=opcode,
        payload={"chatId": chat_id, "messageId": message_id, id_key: file_id},
    )
    payload = result.get("payload", {})
    if not isinstance(payload, dict):
        return ""
    url = payload.get("url")
    if isinstance(url, str) and url:
        return url
    if media_type == "video":
        for key, value in payload.items():
            if key in {"cache", "EXTERNAL"}:
                continue
            if isinstance(value, str) and value:
                return value
    return ""


def _validate_file(file_path: str) -> None:
    """Validate: exists, regular file, no symlink, allowed dir, size limit."""
    p = Path(file_path)
    if p.is_symlink():
        raise ValueError(f"Symlinks not allowed: {file_path}")
    if not p.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not p.is_file():
        raise ValueError(f"Not a regular file: {file_path}")
    resolved = p.resolve()
    if _DEFAULT_ALLOWED_DIRS:
        if not any(resolved.is_relative_to(d) for d in _DEFAULT_ALLOWED_DIRS):
            raise ValueError(f"File outside allowed directories: {file_path}")
    size = p.stat().st_size
    if size > MAX_UPLOAD_SIZE:
        raise ValueError(f"File too large: {size} bytes (max {MAX_UPLOAD_SIZE})")
    if size == 0:
        raise ValueError("File is empty")
