import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("audit_security_baseline", ROOT / "scripts/audit_security_baseline.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class HostEvidenceTests(unittest.TestCase):
    def test_failures_are_unknown_not_success(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError()
        def hung(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 15)
        def denied(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 1, "", "permission denied")
        self.assertEqual(AUDIT.collect_command(["sshd", "-T"], missing)["status"], "unavailable")
        self.assertEqual(AUDIT.collect_command(["sshd", "-T"], hung)["status"], "timeout")
        self.assertEqual(AUDIT.collect_command(["sshd", "-T"], denied)["status"], "error")

    def test_ssh_evidence_filters_unrelated_configuration(self):
        def run(argv, **kwargs):
            self.assertEqual(kwargs["timeout"], 15)
            self.assertNotIn("shell", kwargs)
            return subprocess.CompletedProcess(argv, 0, "permitrootlogin yes\nhostkey /private/key\nciphers aes128-gcm@openssh.com\n", "")
        output = AUDIT.collect_command(["sshd", "-T"], run)["output"]
        self.assertIn("permitrootlogin yes", output)
        self.assertNotIn("hostkey", output)

    def test_policy_captures_dropins_and_pam_without_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "etc/security/pwquality.conf.d"
            directory.mkdir(parents=True)
            (directory / "99-policy.conf").write_text("# minlen = 6\nminlen = 12 # reviewed\nucredit = -1\n", encoding="utf-8")
            pam = root / "etc/pam.d"
            pam.mkdir()
            (pam / "system-auth").write_text("password requisite pam_pwquality.so minlen=14\n", encoding="utf-8")
            result = AUDIT.collect_policy(root)
            selected = [row for row in result if row["status"] == "collected"]
            self.assertEqual(len(selected), 2)
            values = [s["value"] for row in selected for s in row["settings"]]
            self.assertIn("minlen = 12", values)
            self.assertTrue(any("minlen=14" in v for v in values))
            self.assertFalse(any("minlen = 6" in v for v in values))
            self.assertTrue(any(row["status"] == "unavailable" for row in result))

    def test_output_truncation_is_visible(self):
        result = AUDIT.collect_command(["x"], lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "x" * 25000, ""))
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["output"]), 24000)


class NginxBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = (ROOT / "k8s/local-pvc/nginx.conf").read_text(encoding="utf-8")

    def test_tls12_never_negotiates_static_rsa_or_3des(self):
        line = next(line for line in self.config.splitlines() if line.strip().startswith("ssl_ciphers "))
        suites = line.split("'", 2)[1].split(":")
        self.assertTrue(all(s.startswith("ECDHE-") for s in suites))
        self.assertTrue(all("GCM" in s or "CHACHA20" in s for s in suites))
        self.assertIn("ssl_protocols       TLSv1.2 TLSv1.3;", self.config)

    def test_tls_loopback_does_not_double_count_connections(self):
        routing, tls = self.config.split("    # HTTPS edge", 1)
        self.assertEqual(routing.count("limit_conn deerflow_clients 256;"), 1)
        self.assertNotIn("limit_conn deerflow_clients", tls)
        self.assertIn("real_ip_header X-Real-IP;", routing)
        self.assertIn("set_real_ip_from 127.0.0.1;", routing)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr;", tls)

    def test_proxy_errors_and_long_tasks_keep_their_contracts(self):
        self.assertEqual(self.config.count("proxy_intercept_errors on;"), 1)
        self.assertIn("error_page 400 403 = @weknora_invalid_target_tenant;", self.config)
        self.assertIn("error_page 429 = @data_lake_callback_rate_limited;", self.config)
        for status in (400,403,404,408,413,414,429,500,502,503,504):
            self.assertEqual(self.config.count(f"error_page {status} ={status} /_nginx_error;"), 2)
        self.assertIn("proxy_read_timeout 660s;", self.config)
        upload = self.config.split("location ~ ^/api/threads/[^/]+/uploads {", 1)[1].split("}", 1)[0]
        self.assertIn("proxy_read_timeout 600s;", upload)
        self.assertNotIn("limit_rate ", self.config)


if __name__ == "__main__":
    unittest.main()
