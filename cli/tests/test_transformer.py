"""Tests for message transformer."""

from maxbridge.bridge.transformer import packet_to_unified
from maxbridge.utils.types import MessageStatus


class TestPacketToUnified:
    def test_new_message(self, sample_packet):
        msg = packet_to_unified(sample_packet, account_id="alice")
        assert msg is not None
        assert msg.account_id == "alice"
        assert msg.chat_id == 12345
        assert msg.message_id == "msg001"
        assert msg.text == "Hello test"
        assert msg.sender_id == 67890
        assert msg.status == MessageStatus.NEW
        assert msg.timestamp == 1711234567000
        assert len(msg.attachments) == 1
        assert msg.attachments[0]["type"] == "PHOTO"

    def test_edited_message(self, sample_packet):
        sample_packet["payload"]["message"]["status"] = "EDITED"
        msg = packet_to_unified(sample_packet)
        assert msg.status == MessageStatus.EDITED

    def test_deleted_message(self, sample_packet):
        sample_packet["payload"]["message"]["status"] = "REMOVED"
        msg = packet_to_unified(sample_packet)
        assert msg.status == MessageStatus.DELETED

    def test_sender_as_int(self, sample_packet):
        sample_packet["payload"]["message"]["sender"] = 99999
        msg = packet_to_unified(sample_packet)
        assert msg.sender_id == 99999

    def test_no_payload(self):
        assert packet_to_unified({}) is None
        assert packet_to_unified({"payload": None}) is None

    def test_no_message(self):
        assert packet_to_unified({"payload": {"chatId": 1}}) is None

    def test_no_chat_id(self):
        assert packet_to_unified({"payload": {"message": {}}}) is None

    def test_to_dict_omits_raw(self, sample_packet):
        msg = packet_to_unified(sample_packet, account_id="x")
        d = msg.to_dict()
        assert "raw" not in d
        assert d["account_id"] == "x"
        assert d["chat_id"] == 12345

    def test_empty_attaches(self, sample_packet):
        sample_packet["payload"]["message"]["attaches"] = []
        msg = packet_to_unified(sample_packet)
        assert msg.attachments == []
