"""Tests for MAX media download URL helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.media.uploader import get_download_url
from maxbridge.utils.constants import Opcode


@pytest.mark.asyncio
async def test_get_download_url_file_uses_file_id_payload():
    conn = MagicMock()
    conn.client.invoke_method = AsyncMock(return_value={
        "payload": {"url": "https://cdn.example.com/doc.pdf"},
    })

    url = await get_download_url(conn, 123, "m1", 77, "file")

    assert url == "https://cdn.example.com/doc.pdf"
    conn.client.invoke_method.assert_awaited_once_with(
        opcode=Opcode.DOWNLOAD_FILE,
        payload={"chatId": 123, "messageId": "m1", "fileId": 77},
    )


@pytest.mark.asyncio
async def test_get_download_url_video_uses_video_id_and_format_url():
    conn = MagicMock()
    conn.client.invoke_method = AsyncMock(return_value={
        "payload": {
            "cache": "ignored-cache",
            "EXTERNAL": "ignored-external",
            "LOW": "https://cdn.example.com/video-low.mp4",
            "HIGH": "https://cdn.example.com/video-high.mp4",
        },
    })

    url = await get_download_url(conn, 123, "m-video", 42, "video")

    assert url == "https://cdn.example.com/video-low.mp4"
    conn.client.invoke_method.assert_awaited_once_with(
        opcode=Opcode.DOWNLOAD_VIDEO,
        payload={"chatId": 123, "messageId": "m-video", "videoId": 42},
    )


@pytest.mark.asyncio
async def test_get_download_url_video_prefers_direct_url_when_present():
    conn = MagicMock()
    conn.client.invoke_method = AsyncMock(return_value={
        "payload": {
            "url": "https://cdn.example.com/direct-video.mp4",
            "LOW": "https://cdn.example.com/video-low.mp4",
        },
    })

    url = await get_download_url(conn, 123, "m-video", 42, "video")

    assert url == "https://cdn.example.com/direct-video.mp4"
