"""Standalone, synthetic, offline tests: python -m unittest discover here."""

import base64
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

SCRIPT = Path(__file__).with_name("work-runtime-preflight-20261104.py")
SPEC = importlib.util.spec_from_file_location("work_preflight_test_target", SCRIPT)
PREFLIGHT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PREFLIGHT
SPEC.loader.exec_module(PREFLIGHT)


def snapshot(**updates):
    settings = types.SimpleNamespace(
        profile="production",
        enabled=True,
        persistent=True,
        api_url=PREFLIGHT.API_ORIGIN,
        authorize_url=PREFLIGHT.ACCOUNT_ORIGIN + "/open-apis/authen/v1/authorize",
        callback_url=PREFLIGHT.PRODUCTION_CALLBACK,
        document_host=PREFLIGHT.PRODUCTION_DOCUMENT_HOST,
        allowed_owners=frozenset({"synthetic-owner"}),
        turn_timeout=90,
        turn_queue_timeout=10,
        concurrency=2,
    )
    values = dict(
        settings=settings,
        credentials={
            "enabled": True,
            "app_id": "synthetic-app",
            "app_secret": "synthetic-secret",
            "domain": PREFLIGHT.API_ORIGIN,
        },
        database_url="postgresql://user:synthetic-db-password@database.invalid/work_test",
        state_key=base64.b64encode(b"K" * 32).decode(),
        runtime_present=True,
        channel_connections_enabled=True,
        bound_identity_required=True,
    )
    values.update(updates)
    return PREFLIGHT.Snapshot(**values)


def fake_requests(routes, session=None):
    session = session if session is not None else MagicMock()
    session.__enter__.return_value = session
    session.trust_env = True
    session.proxies = {}
    session.prepare_request.side_effect = lambda request: request
    session.merge_environment_settings.side_effect = (
        lambda url, proxies, stream, verify, cert: {
            "proxies": routes(url),
            "stream": stream,
            "verify": True,
            "cert": None,
        }
    )
    modules = {
        "requests": types.SimpleNamespace(
            Session=lambda: session,
            Request=lambda method, url, **kwargs: types.SimpleNamespace(
                method=method, url=url, **kwargs
            ),
        ),
        "requests.utils": types.SimpleNamespace(
            get_environ_proxies=routes,
            select_proxy=lambda url, values: values.get("https"),
        ),
    }
    return modules, session


class RuntimeReadTests(unittest.TestCase):
    def test_missing_runtime_creates_neither_directory_nor_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent/channels/runtime-config.json"
            self.assertEqual(PREFLIGHT.read_runtime(path), {})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_fresh_runtime_read_never_uses_a_previous_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime-config.json"
            path.write_text('{"feishu":{"app_secret":"first"}}')
            self.assertEqual(
                PREFLIGHT.read_runtime(path)["feishu"]["app_secret"], "first"
            )
            path.write_text('{"feishu":{"app_secret":"second"}}')
            self.assertEqual(
                PREFLIGHT.read_runtime(path)["feishu"]["app_secret"], "second"
            )
            self.assertEqual(
                {item.name for item in Path(directory).iterdir()}, {path.name}
            )

    def test_corrupt_oversize_and_wrong_shape_fail_instead_of_falling_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime-config.json"
            for value in ("invalid", "[]", '{"feishu":null}', " " * 1048577):
                with self.subTest(size=len(value)):
                    path.write_text(value)
                    with self.assertRaises(PREFLIGHT.UnsafeSnapshot):
                        PREFLIGHT.read_runtime(path)

    def test_locked_runtime_fails_without_fallback_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime-config.json"
            path.write_text('{"feishu":{"enabled":true}}')
            lock = path.with_name(path.name + ".lock")
            lock.write_text("")
            fake = types.SimpleNamespace(
                LOCK_SH=1, LOCK_NB=4, flock=Mock(side_effect=BlockingIOError)
            )
            with (
                patch.dict(sys.modules, {"fcntl": fake}),
                self.assertRaises(PREFLIGHT.UnsafeSnapshot),
            ):
                PREFLIGHT.read_runtime(path)
            self.assertEqual(path.read_text(), '{"feishu":{"enabled":true}}')

    def test_permission_error_is_not_misclassified_as_an_absent_runtime(self):
        with (
            patch.object(Path, "lstat", side_effect=PermissionError),
            self.assertRaises(PREFLIGHT.UnsafeSnapshot),
        ):
            PREFLIGHT.read_runtime(Path("not-read"))

    def test_symlink_and_during_read_replacement_are_not_accepted_as_stable(self):
        symbolic = types.SimpleNamespace(st_mode=stat.S_IFLNK)
        with (
            patch.object(Path, "lstat", side_effect=[FileNotFoundError, symbolic]),
            self.assertRaises(PREFLIGHT.UnsafeSnapshot),
        ):
            PREFLIGHT.read_runtime(Path("not-read"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime-config.json"
            path.write_text("{}")
            before = path.stat()
            changed = types.SimpleNamespace(
                st_dev=before.st_dev,
                st_ino=before.st_ino,
                st_size=before.st_size,
                st_mtime_ns=before.st_mtime_ns + 1,
            )
            with (
                patch.object(os, "fstat", side_effect=[before, changed]),
                self.assertRaises(PREFLIGHT.UnsafeSnapshot),
            ):
                PREFLIGHT.read_runtime(path)


class SnapshotTests(unittest.TestCase):
    def test_fingerprints_compare_pods_and_secret_rotation_without_exposing_values(
        self,
    ):
        one = snapshot()
        report = PREFLIGHT.config_report(one)
        self.assertEqual(report, PREFLIGHT.config_report(snapshot()))
        changed = replace(
            one, credentials={**one.credentials, "app_secret": "synthetic-rotated"}
        )
        next_report = PREFLIGHT.config_report(changed)
        self.assertNotEqual(
            report["fingerprints"]["credentials"],
            next_report["fingerprints"]["credentials"],
        )
        self.assertNotEqual(
            report["fingerprints"]["work_namespace"],
            next_report["fingerprints"]["work_namespace"],
        )
        self.assertEqual(
            report["fingerprints"]["receiver_namespace"],
            next_report["fingerprints"]["receiver_namespace"],
        )
        output = json.dumps(report)
        for private in (
            "synthetic-secret",
            "synthetic-app",
            "synthetic-owner",
            "synthetic-db-password",
            one.database_url,
            one.state_key,
        ):
            self.assertNotIn(private, output)

    def test_admin_runtime_values_take_precedence_without_constructing_the_mutable_store(
        self,
    ):
        base = snapshot()
        config = types.SimpleNamespace(
            model_extra={
                "channels": {
                    "feishu": {"enabled": True, "app_id": "base", "app_secret": "base"}
                }
            },
            channel_connections=types.SimpleNamespace(
                enabled=True, require_bound_identity=True
            ),
        )
        runtime = {
            "feishu": {"enabled": True, "app_id": "admin", "app_secret": "admin-secret"}
        }
        policy = Mock(side_effect=lambda name, value: dict(value))
        mutable_store = Mock(
            side_effect=AssertionError("must not create mutable store")
        )
        mutable_store._policy = policy

        def merge(channels, connection, *, store):
            channels["feishu"].update(store.load_all()["feishu"])

        def defaults(channels):
            channels.setdefault(
                "feishu", {"app_id": "environment", "app_secret": "environment-secret"}
            )

        modules = {
            "app.channels.runtime_config_store": types.SimpleNamespace(
                ChannelRuntimeConfigStore=mutable_store,
                merge_runtime_channel_configs=merge,
            ),
            "app.integrations.work.config": types.SimpleNamespace(
                WorkSettings=types.SimpleNamespace(from_env=lambda: base.settings),
                apply_work_credential_defaults=defaults,
            ),
            "deerflow.config.app_config": types.SimpleNamespace(
                get_app_config=lambda: config
            ),
            "deerflow.config.paths": types.SimpleNamespace(
                get_paths=lambda: types.SimpleNamespace(base_dir=Path("unused"))
            ),
        }
        with (
            patch.dict(sys.modules, modules),
            patch.object(PREFLIGHT, "read_runtime", return_value=runtime),
        ):
            result = PREFLIGHT.load_snapshot()
        self.assertEqual(result.credentials["app_id"], "admin")
        self.assertEqual(result.credentials["app_secret"], "admin-secret")
        mutable_store.assert_not_called()

    def test_default_cli_neither_probes_nor_inspects_database_and_cannot_claim_acceptance(
        self,
    ):
        output = io.StringIO()
        with (
            patch.object(PREFLIGHT, "load_snapshot", return_value=snapshot()),
            patch.object(PREFLIGHT, "proxy_report", return_value={}),
            patch.object(
                PREFLIGHT, "httpx_probe", side_effect=AssertionError
            ) as network,
            patch.object(
                PREFLIGHT, "database_report", side_effect=AssertionError
            ) as database,
            redirect_stdout(output),
        ):
            self.assertEqual(PREFLIGHT.main([]), 0)
        report = json.loads(output.getvalue())
        self.assertFalse(report["network_requested"])
        self.assertFalse(report["database_requested"])
        self.assertFalse(report["production_acceptance_verified"])
        self.assertFalse(report["production_egress_ip_known"])
        network.assert_not_called()
        database.assert_not_called()

    def test_snapshot_failure_never_outputs_configuration_or_exception_text(self):
        output = io.StringIO()
        with (
            patch.object(
                PREFLIGHT,
                "load_snapshot",
                side_effect=RuntimeError("PRIVATE_SECRET_URL"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(PREFLIGHT.main([]), 2)
        self.assertFalse(json.loads(output.getvalue())["configuration_loaded"])
        self.assertNotIn("PRIVATE_SECRET_URL", output.getvalue())

    def test_report_includes_current_utc_timestamp_even_when_configuration_fails(self):
        output = io.StringIO()
        before = datetime.now(UTC)
        with (
            patch.object(PREFLIGHT, "load_snapshot", side_effect=RuntimeError),
            redirect_stdout(output),
        ):
            self.assertEqual(PREFLIGHT.main([]), 2)
        after = datetime.now(UTC)
        report = json.loads(output.getvalue())
        checked_at = datetime.fromisoformat(report["checked_at_utc"])
        self.assertEqual(checked_at.utcoffset().total_seconds(), 0)
        self.assertLessEqual(before, checked_at)
        self.assertLessEqual(checked_at, after)
        self.assertEqual(report["schema"], "work-runtime-preflight.v1")

    def test_proxy_selection_prints_only_presence_and_selection_flags(self):
        routes = Mock(
            return_value={
                "https": "http://private-user:private-password@proxy.invalid:8080"
            }
        )
        modules, _ = fake_requests(routes)
        with patch.dict(sys.modules, modules):
            report = PREFLIGHT.proxy_report()
        self.assertTrue(report["requests_api_would_proxy"])
        self.assertTrue(report["requests_accounts_would_proxy"])
        self.assertNotIn("private-password", json.dumps(report))
        self.assertEqual(routes.call_count, 6)
        self.assertEqual(
            [call.args[0] for call in routes.call_args_list],
            [
                PREFLIGHT.API_ORIGIN + PREFLIGHT.TOKEN_PATH,
                PREFLIGHT.API_ORIGIN + PREFLIGHT.BOT_PATH,
                PREFLIGHT.ACCOUNT_ORIGIN + "/open-apis/authen/v1/authorize",
            ]
            * 2,
        )

    def test_proxy_selection_is_computed_for_each_full_path_instead_of_reusing_an_origin(
        self,
    ):
        def environment(url):
            return (
                {"https": "http://private-proxy.invalid"}
                if url.endswith(PREFLIGHT.BOT_PATH)
                else {}
            )

        modules, _ = fake_requests(environment)
        with patch.dict(sys.modules, modules):
            report = PREFLIGHT.proxy_report()
        self.assertFalse(report["requests_paths"]["tenant_token"])
        self.assertTrue(report["requests_paths"]["bot_info"])
        self.assertFalse(report["requests_paths"]["accounts_authorize"])

    def test_proxy_report_distinguishes_environment_from_effective_session_settings(
        self,
    ):
        modules, session = fake_requests(lambda url: {})
        session.proxies = {"https": "http://u:private-session-proxy@proxy.invalid"}
        session.merge_environment_settings.side_effect = None
        session.merge_environment_settings.return_value = {
            "proxies": session.proxies,
            "stream": True,
            "verify": True,
            "cert": None,
        }
        with patch.dict(sys.modules, modules):
            report = PREFLIGHT.proxy_report()
        self.assertFalse(report["requests_environment_paths"]["bot_info"])
        self.assertTrue(report["requests_paths"]["bot_info"])
        self.assertTrue(report["requests_session_proxy_map_present"])
        self.assertEqual(report["requests_path_basis"], "SESSION_MERGED_SETTINGS")
        self.assertNotIn("private-session-proxy", json.dumps(report))
        session.send.assert_not_called()

    def test_prepared_request_retarget_is_rejected_before_any_send(self):
        modules, session = fake_requests(lambda url: {})
        session.prepare_request.side_effect = None
        session.prepare_request.return_value = types.SimpleNamespace(
            url="https://private.invalid"
        )
        with (
            patch.dict(sys.modules, modules),
            self.assertRaises(PREFLIGHT.UnsafeSnapshot),
        ):
            PREFLIGHT.prepare_requests(
                session,
                "POST",
                PREFLIGHT.API_ORIGIN + PREFLIGHT.TOKEN_PATH,
                {"app_secret": "synthetic-secret"},
            )
        session.send.assert_not_called()

    def test_proxy_environment_fingerprint_is_keyed_without_exposing_urls(self):
        route = patch.object(PREFLIGHT, "request_proxy_selected", return_value=False)
        route.start()
        self.addCleanup(route.stop)
        modules, _ = fake_requests(lambda url: {})
        request_modules = patch.dict(sys.modules, modules)
        request_modules.start()
        self.addCleanup(request_modules.stop)
        key = b"K" * 32
        one = {
            "HTTPS_PROXY": "http://u:private-first@proxy.invalid:8080",
            "NO_PROXY": "internal.invalid",
            "no_proxy": "internal.invalid",
        }
        # The target Pod is case-sensitive; Windows os.environ folds names.
        with patch.object(PREFLIGHT.os, "getenv", side_effect=one.get):
            first = PREFLIGHT.proxy_report(key)
            self.assertEqual(
                first["environment_fingerprint"],
                PREFLIGHT.proxy_report(key)["environment_fingerprint"],
            )
            self.assertTrue(first["no_proxy_upper_lower_equal"])
            self.assertIsNone(PREFLIGHT.proxy_report()["environment_fingerprint"])
            self.assertIsNone(
                PREFLIGHT.proxy_report(b"invalid")["environment_fingerprint"]
            )
            self.assertNotEqual(
                first["environment_fingerprint"],
                PREFLIGHT.proxy_report(b"L" * 32)["environment_fingerprint"],
            )
        second_environment = {
            **one,
            "no_proxy": "different.invalid",
            "HTTPS_PROXY": "http://u:private-second@proxy.invalid:8080",
        }
        with patch.object(PREFLIGHT.os, "getenv", side_effect=second_environment.get):
            second = PREFLIGHT.proxy_report(key)
        self.assertNotEqual(
            first["environment_fingerprint"], second["environment_fingerprint"]
        )
        self.assertFalse(second["no_proxy_upper_lower_equal"])
        for private in (
            "private-first",
            "private-second",
            "proxy.invalid",
            "different.invalid",
        ):
            self.assertNotIn(private, json.dumps([first, second]))

    def test_requests_wrapper_never_follows_a_redirect_and_bounds_response_bytes(self):
        route = patch.object(PREFLIGHT, "request_proxy_selected", return_value=False)
        route.start()
        self.addCleanup(route.stop)
        for body in (
            b'{"tenant_access_token":"PRIVATE_TOKEN"}',
            b"X" * (PREFLIGHT.MAX_BYTES + 1),
        ):
            with self.subTest(oversize=len(body) > PREFLIGHT.MAX_BYTES):
                response = MagicMock(
                    status_code=302,
                    headers={"Location": "https://private.invalid/SECRET"},
                )
                response.__enter__.return_value = response
                response.iter_content.return_value = [body]
                proxies = {"https": "http://u:private-actual-proxy@proxy.invalid"}
                modules, session = fake_requests(lambda url: proxies)
                session.send.return_value = response
                with patch.dict(sys.modules, modules):
                    report = PREFLIGHT.requests_probe(snapshot())
                session.send.assert_called_once()
                session.request.assert_not_called()
                call = session.send.call_args
                self.assertEqual(
                    call.args[0].url, PREFLIGHT.API_ORIGIN + PREFLIGHT.TOKEN_PATH
                )
                self.assertFalse(call.kwargs["allow_redirects"])
                self.assertEqual(call.kwargs["timeout"], (5, 10))
                self.assertIs(call.kwargs["proxies"], proxies)
                self.assertTrue(report["request_proxy_selection"]["tenant_token"])
                self.assertEqual(
                    report["request_proxy_selection_basis"], "SENT_MERGED_SETTINGS"
                )
                session.merge_environment_settings.assert_called_once_with(
                    PREFLIGHT.API_ORIGIN + PREFLIGHT.TOKEN_PATH, {}, True, None, None
                )
                self.assertNotIn("PRIVATE_TOKEN", json.dumps(report))
                self.assertNotIn("private.invalid", json.dumps(report))
                self.assertNotIn("private-actual-proxy", json.dumps(report))


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    def test_whitelist_ip_formats_are_strictly_labeled_and_unique(self):
        for text in (
            "ip 203.0.113.10 is not allowed",
            "IP(203.0.113.10) is denied",
            "ip [203.0.113.10] is forbidden",
            "client_ip: 203.0.113.10",
            "IP is denied: 203.0.113.10",
            "source IP：203.0.113.10。",
            "denied IP address = '203.0.113.10'",
        ):
            with self.subTest(text=text):
                report = PREFLIGHT.response_status(400, {"code": 99991401, "msg": text})
                self.assertEqual(report.get("observed_rejected_ip"), "203.0.113.10")
                self.assertTrue(report["observed_ip_current_request_only"])
                self.assertFalse(report["stable_egress_verified"])
        for text in (
            "denied 203.0.113.10",
            "IP 999.0.0.1",
            "IP 203.0.113.10; client_ip 203.0.113.11",
            "ip https://private.invalid/203.0.113.10",
        ):
            with self.subTest(text=text):
                self.assertNotIn(
                    "observed_rejected_ip",
                    PREFLIGHT.response_status(400, {"code": 99991401, "msg": text}),
                )

    async def test_httpx_wrapper_is_direct_redirect_disabled_and_bounded(self):
        response = MagicMock(
            status_code=302, headers={"Location": "https://private.invalid/SECRET"}
        )
        response.__aenter__.return_value = response

        async def chunks():
            yield b'{"tenant_access_token":"PRIVATE_TOKEN"}'

        response.aiter_bytes = chunks
        client = MagicMock()
        client.__aenter__.return_value = client
        client.stream.return_value = response
        factory = Mock(return_value=client)
        module = types.SimpleNamespace(
            AsyncClient=factory, Timeout=lambda *args, **kwargs: "bounded"
        )
        with patch.dict(sys.modules, {"httpx": module}):
            report = await PREFLIGHT.httpx_probe(snapshot())
        self.assertFalse(factory.call_args.kwargs["trust_env"])
        self.assertFalse(factory.call_args.kwargs["follow_redirects"])
        client.stream.assert_called_once()
        self.assertFalse(client.stream.call_args.kwargs["follow_redirects"])
        self.assertEqual(
            client.stream.call_args.args[1], PREFLIGHT.API_ORIGIN + PREFLIGHT.TOKEN_PATH
        )
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(report))

    async def test_probe_is_fixed_origin_and_does_not_echo_any_credential_or_response(
        self,
    ):
        send = AsyncMock(
            side_effect=[
                (200, {"code": 0, "tenant_access_token": "PRIVATE_TENANT_TOKEN"}, {}),
                (
                    400,
                    {
                        "code": 99991401,
                        "msg": "client_ip: 10.63.151.54 is not allowed PRIVATE_BODY",
                        "error": {"log_id": "20261104LOGID1234"},
                    },
                    {},
                ),
            ]
        )
        report = await PREFLIGHT.run_probe(snapshot(), send)
        self.assertEqual(send.await_count, 2)
        self.assertEqual(
            send.await_args_list[0].args[:2], ("POST", PREFLIGHT.TOKEN_PATH)
        )
        self.assertEqual(send.await_args_list[1].args[:2], ("GET", PREFLIGHT.BOT_PATH))
        self.assertEqual(report["bot_info"]["observed_rejected_ip"], "10.63.151.54")
        self.assertEqual(report["bot_info"]["log_id"], "20261104LOGID1234")
        for private in (
            "PRIVATE_TENANT_TOKEN",
            "PRIVATE_BODY",
            "synthetic-secret",
            "Bearer",
        ):
            self.assertNotIn(private, json.dumps(report))

    async def test_probe_refuses_to_send_credentials_to_an_operator_mistyped_origin(
        self,
    ):
        value = snapshot()
        value.settings.api_url = "https://wrong.example.test"
        send = AsyncMock()
        report = await PREFLIGHT.run_probe(value, send)
        self.assertFalse(report["attempted"])
        send.assert_not_awaited()

    async def test_redirect_or_bad_token_response_never_continues_to_bot_info(self):
        for status, body in (
            (302, {"tenant_access_token": "PRIVATE"}),
            (200, []),
            (200, {"code": 1, "tenant_access_token": "PRIVATE"}),
        ):
            with self.subTest(status=status, body_type=type(body).__name__):
                send = AsyncMock(return_value=(status, body, {}))
                report = await PREFLIGHT.run_probe(snapshot(), send)
                self.assertEqual(send.await_count, 1)
                self.assertFalse(report["bot_info"]["attempted"])

    async def test_exceptions_and_non_whitelist_ip_mentions_are_not_exposed(self):
        report = await PREFLIGHT.run_probe(
            snapshot(), AsyncMock(side_effect=RuntimeError("PRIVATE_URL_TOKEN"))
        )
        self.assertTrue(report["transport_failed"])
        self.assertNotIn("PRIVATE_URL_TOKEN", json.dumps(report))
        self.assertNotIn(
            "observed_rejected_ip",
            PREFLIGHT.response_status(200, {"code": 0, "msg": "ip: 10.0.0.1"}),
        )
        self.assertNotIn(
            "observed_rejected_ip",
            PREFLIGHT.response_status(
                400,
                {
                    "code": 99991401,
                    "msg": "ip: 999.0.0.1",
                    "error": {"log_id": "https://private.invalid/token"},
                },
            ),
        )
        self.assertNotIn(
            "log_id",
            PREFLIGHT.response_status(
                400,
                {
                    "code": 99991401,
                    "error": {"log_id": "https://private.invalid/token"},
                },
            ),
        )


if __name__ == "__main__":
    unittest.main()
