"""Golden-vector tests for the shared sandbox scope token (RFC #4741)."""

from deerflow.sandbox.identity import (
    SANDBOX_ID_VERSION,
    derive_sandbox_scope_token,
    is_sandbox_scope_token,
)


class TestGoldenVectors:
    def test_ascii(self):
        assert derive_sandbox_scope_token(user_id="alice", thread_id="thread-1") == "dc977b840cb8638e"

    def test_empty_user(self):
        assert derive_sandbox_scope_token(user_id="", thread_id="t") == "983743815e8fe2ac"

    def test_empty_thread(self):
        assert derive_sandbox_scope_token(user_id="u", thread_id="") == "27e14d2b41b03178"

    def test_non_ascii(self):
        assert derive_sandbox_scope_token(user_id="用户", thread_id="线程") == "cfa8a5bf41ddff8d"

    def test_literal_none_string(self):
        assert derive_sandbox_scope_token(user_id="None", thread_id="abc") == "c0b80df1b97d187e"

    def test_version_constant(self):
        assert SANDBOX_ID_VERSION == 1

    def test_keyword_only(self):
        import inspect

        sig = inspect.signature(derive_sandbox_scope_token)
        for param in sig.parameters.values():
            assert param.kind is inspect.Parameter.KEYWORD_ONLY


class TestShapeValidation:
    def test_accepts_token(self):
        token = derive_sandbox_scope_token(user_id="alice", thread_id="thread-1")
        assert is_sandbox_scope_token(token) is True

    def test_rejects_wrong_shape(self):
        assert is_sandbox_scope_token("") is False
        assert is_sandbox_scope_token("dc977b840cb8638") is False
        assert is_sandbox_scope_token("dc977b840cb8638ee") is False
        assert is_sandbox_scope_token("DC977B840CB8638E") is False
        assert is_sandbox_scope_token("local:alice:thread-1") is False

    def test_rejects_non_str(self):
        assert is_sandbox_scope_token(None) is False
        assert is_sandbox_scope_token(123) is False
