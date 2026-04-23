"""Tests for message transformer."""

from maxbridge.bridge.transformer import packet_to_unified
from maxbridge.utils.types import LinkedMessage, MessageStatus


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

    def test_timestamp_uses_time_when_cid_missing(self, sample_packet):
        sample_packet["payload"]["message"].pop("cid")
        sample_packet["payload"]["message"]["time"] = 1711234567999
        msg = packet_to_unified(sample_packet)
        assert msg.timestamp == 1711234567999

    def test_extracts_forwarded_message(self, sample_packet):
        sample_packet["payload"]["message"]["text"] = ""
        sample_packet["payload"]["message"]["attaches"] = []
        sample_packet["payload"]["message"]["link"] = {
            "type": "FORWARD",
            "chatId": -42,
            "message": {
                "id": "orig001",
                "text": "Forwarded body",
                "time": 1711234500000,
                "sender": 777,
                "attaches": [
                    {"_type": "SHARE", "title": "School doc", "url": "https://example.com/doc"},
                ],
            },
        }

        msg = packet_to_unified(sample_packet)

        assert msg.link_type == "FORWARD"
        assert msg.link_chat_id == -42
        assert isinstance(msg.linked_message, LinkedMessage)
        assert msg.linked_message.message_id == "orig001"
        assert msg.linked_message.sender_id == 777
        assert msg.linked_message.text == "Forwarded body"
        assert msg.linked_message.timestamp == 1711234500000
        assert msg.linked_message.attachments[0]["type"] == "SHARE"

    def test_to_dict_includes_linked_message(self, sample_packet):
        sample_packet["payload"]["message"]["link"] = {
            "type": "REPLY",
            "chatId": 0,
            "message": {
                "id": "orig002",
                "text": "Quoted body",
                "time": 1711234500001,
                "sender": {"userId": 888},
                "attaches": [],
            },
        }

        msg = packet_to_unified(sample_packet)
        d = msg.to_dict()

        assert d["link_type"] == "REPLY"
        assert d["link_chat_id"] == 0
        assert d["linked_message"]["message_id"] == "orig002"
        assert d["linked_message"]["sender_id"] == 888
        assert d["linked_message"]["text"] == "Quoted body"
