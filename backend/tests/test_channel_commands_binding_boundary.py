"""Binding commands must not interpret ordinary chat as an identity handoff."""

import unittest

from app.channels.commands import extract_connect_code, is_known_channel_command, strip_leading_mentions


class ChannelBindingBoundaryTests(unittest.TestCase):
    def test_explicit_connect_accepts_platform_mentions(self):
        for prefix in ("", "@bot ", "@bot @_user_2 ", "<@U123> ", "<@!U123> ", "<@U123|alice> "):
            with self.subTest(prefix=prefix):
                self.assertEqual(extract_connect_code(prefix + "/Connect AbC123"), "AbC123")

    def test_chat_and_embedded_commands_never_bind(self):
        for text in ("connect abc", "connect the database to the api", "@bot connect abc", "please @bot /connect abc", "hi /connect abc", "/connect", "@bot", ""):
            with self.subTest(text=text):
                self.assertIsNone(extract_connect_code(text))

    def test_stripping_mentions_does_not_expand_control_command_authority(self):
        self.assertEqual(strip_leading_mentions("@bot /goal ship"), "/goal ship")
        self.assertFalse(is_known_channel_command("@bot /goal ship"))
        self.assertTrue(is_known_channel_command("/goal ship"))
        for text in (" /new", "hello /goal", "<@U123 /goal", "@@bot /goal", "@ /goal"):
            with self.subTest(text=text):
                self.assertEqual(strip_leading_mentions(text), text)


if __name__ == "__main__":
    unittest.main()
