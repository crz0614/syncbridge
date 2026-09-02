import os
import unittest
from unittest.mock import patch

from syncbridge.app import Runtime


class DestinationConfigTests(unittest.TestCase):
    def test_invalid_destination_fails_before_opening_storage(self):
        for destination in ("", "notoin", "REST", " notion", "private-value"):
            with self.subTest(destination=destination), patch.dict(os.environ, {
                "SYNCBRIDGE_DESTINATION": destination,
                "SYNCBRIDGE_API_TOKEN": "test-token",
                "SYNCBRIDGE_WEBHOOK_SECRET": "test-secret",
            }, clear=True), patch("syncbridge.app.Store") as storage:
                with self.assertRaisesRegex(ValueError, "SYNCBRIDGE_DESTINATION") as error:
                    Runtime()
                self.assertEqual(str(error.exception), "SYNCBRIDGE_DESTINATION must be rest or notion")
                storage.assert_not_called()

    def test_default_rest_can_start_unconfigured_without_delivering(self):
        with patch.dict(os.environ, {
            "SYNCBRIDGE_API_TOKEN": "test-token",
            "SYNCBRIDGE_WEBHOOK_SECRET": "test-secret",
        }, clear=True), patch("syncbridge.app.Store"), patch("syncbridge.app.send_rest") as send:
            runtime = Runtime()
            self.assertEqual(runtime.health()["destination"], "rest")
            self.assertFalse(runtime.health()["destination_configured"])
            send.assert_not_called()

    def test_valid_adapters_dispatch_and_invalid_mutation_never_sends(self):
        for destination in ("rest", "notion"):
            with self.subTest(destination=destination), patch.dict(os.environ, {
                "SYNCBRIDGE_DESTINATION": destination,
                "SYNCBRIDGE_API_TOKEN": "test-token",
                "SYNCBRIDGE_WEBHOOK_SECRET": "test-secret",
                "DESTINATION_URL": "https://receiver.example.test/events",
                "DESTINATION_TOKEN": "rest-test-token",
                "NOTION_DATABASE_ID": "test-database",
                "NOTION_TOKEN": "notion-test-token",
            }, clear=True), patch("syncbridge.app.Store"), patch("syncbridge.app.send_rest") as rest, patch("syncbridge.app.send_notion") as notion:
                runtime = Runtime()
                runtime.deliver({"test": True})
                self.assertEqual(runtime.health()["destination"], destination)
                expected, other = (rest, notion) if destination == "rest" else (notion, rest)
                expected.assert_called_once()
                other.assert_not_called()
                rest.reset_mock()
                notion.reset_mock()
                os.environ["SYNCBRIDGE_DESTINATION"] = "notoin"
                with self.assertRaises(ValueError):
                    runtime.deliver({"test": True})
                rest.assert_not_called()
                notion.assert_not_called()
