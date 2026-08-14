"""Session-screen regression coverage for duplicate authorization cleanup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maxbridge.tui.screens.sessions import SessionScreen


def test_failed_duplicate_auth_does_not_persist_temporary_account():
    screen = SessionScreen.__new__(SessionScreen)
    screen._accounts_cfg = {
        "default": {"session_file": "data/default.session"},
        "account_2": {"session_file": "data/account_2.session"},
    }
    screen._config_accounts = {"default": {}}
    screen._refresh = MagicMock()
    screen._load_profile = MagicMock()
    session = MagicMock()
    session.exists.return_value = False

    with patch("maxbridge.tui.screens.sessions.add_account") as add_account:
        screen._finish_new_auth(
            "account_2",
            {"session_file": "data/account_2.session"},
            session,
            False,
        )

    add_account.assert_not_called()
    assert "account_2" not in screen._accounts_cfg
    screen._refresh.assert_called_once_with()


@pytest.mark.asyncio
async def test_duplicate_scan_preserves_declared_account_order():
    screen = SessionScreen.__new__(SessionScreen)
    screen._encryptor = MagicMock()
    screen._accounts_cfg = {
        "default": {"session_file": "data/default.session"},
        "account_2": {"session_file": "data/account_2.session"},
        "account_3": {"session_file": "data/account_3.session"},
    }
    manager = MagicMock()
    manager.find_duplicate_account = AsyncMock(return_value="default")

    with patch(
        "maxbridge.tui.screens.sessions.AccountManager",
        return_value=manager,
    ):
        result = await screen._find_duplicate_account("42", "account_3")

    assert result == "default"
    assert manager.add_account.call_args_list == [
        (("default", {"session_file": "data/default.session"}),),
        (("account_2", {"session_file": "data/account_2.session"}),),
    ]
    manager.find_duplicate_account.assert_awaited_once_with("42")
