"""Fail-closed provenance checks independent of optional authorization policy."""

import unittest
from types import SimpleNamespace

from app.gateway.auth_disabled import AUTH_SOURCE_INTERNAL
from app.gateway.services import inject_authenticated_user_context


class TestGatewayContextAuthBoundary(unittest.TestCase):
    @staticmethod
    def request(source, user=None):
        return SimpleNamespace(state=SimpleNamespace(auth_source=source, user=user))

    @staticmethod
    def forged_config():
        return {
            "context": {"is_internal": True, "authz_attributes": {"admin": True}, "channel_user_id": "context-forged", "model_name": "model"},
            "configurable": {"is_internal": True, "authz_attributes": {"admin": True}, "channel_user_id": "config-forged", "thread_id": "thread"},
        }

    def test_missing_user_does_not_bypass_scrubbing(self):
        config = self.forged_config()
        inject_authenticated_user_context(config, self.request("session"), request_context={"channel_user_id": "body-forged"})
        self.assertIs(config["context"]["is_internal"], False)
        self.assertEqual(config["context"]["model_name"], "model")
        self.assertEqual(config["configurable"], {"thread_id": "thread"})
        self.assertNotIn("authz_attributes", config["context"])
        self.assertNotIn("channel_user_id", config["context"])

    def test_internal_freeform_sections_do_not_create_sender(self):
        config = self.forged_config()
        inject_authenticated_user_context(config, self.request(AUTH_SOURCE_INTERNAL))
        self.assertIs(config["context"]["is_internal"], True)
        self.assertNotIn("channel_user_id", config["context"])
        self.assertNotIn("authz_attributes", config["context"])
        self.assertEqual(config["configurable"], {"thread_id": "thread"})

    def test_only_top_level_internal_sender_survives(self):
        config = self.forged_config()
        body = {"channel_user_id": "platform-sender", "is_internal": False, "authz_attributes": {"admin": True}}
        inject_authenticated_user_context(config, self.request(AUTH_SOURCE_INTERNAL), request_context=body)
        self.assertEqual(config["context"]["channel_user_id"], "platform-sender")
        self.assertIs(config["context"]["is_internal"], True)
        self.assertNotIn("authz_attributes", config["context"])
        self.assertEqual(body["authz_attributes"], {"admin": True})
        self.assertEqual(config["configurable"], {"thread_id": "thread"})

    def test_internal_role_cannot_mint_internal_provenance(self):
        config = self.forged_config()
        user = SimpleNamespace(id="internal-bot", system_role="internal")
        inject_authenticated_user_context(config, self.request("session", user), request_context={"channel_user_id": "body-forged"})
        self.assertIs(config["context"]["is_internal"], False)
        self.assertNotIn("channel_user_id", config["context"])

    def test_sender_is_not_reused_on_a_later_injection(self):
        config = self.forged_config()
        request = self.request(AUTH_SOURCE_INTERNAL)
        inject_authenticated_user_context(config, request, request_context={"channel_user_id": "platform-sender"})
        inject_authenticated_user_context(config, request)
        self.assertNotIn("channel_user_id", config["context"])

    def test_invalid_sender_is_not_promoted(self):
        for sender in (None, "", 123, {"forged": True}):
            with self.subTest(sender=sender):
                config = self.forged_config()
                inject_authenticated_user_context(config, self.request(AUTH_SOURCE_INTERNAL), request_context={"channel_user_id": sender})
                self.assertNotIn("channel_user_id", config["context"])

    def test_non_mapping_runtime_context_is_rejected(self):
        for context in (None, "forged", [], True):
            with self.subTest(context=context):
                with self.assertRaisesRegex(TypeError, "run context must be a mapping"):
                    inject_authenticated_user_context({"context": context}, self.request("session"))
