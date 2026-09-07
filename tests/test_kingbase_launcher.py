"""Execute the actual launcher with fake vendor binaries, including failure recovery."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
GIT_BASH = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
BASH = str(GIT_BASH) if GIT_BASH.is_file() else shutil.which("bash")


@unittest.skipUnless(BASH, "Bash is required for launcher behavior tests")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.data = self.root / "data"
        (self.root / "password").write_text("test-password", encoding="utf-8")
        (self.bin / "license.dat").write_text("test-license", encoding="utf-8")
        self.path = self.root.as_posix()
        if os.name == "nt":
            self.path = subprocess.check_output([BASH, "-c", 'cygpath -u "$1"', "_", self.path], text=True).strip()
        source = (ROOT / "k8s/local-pvc-kingbase/base/start.sh").read_text(encoding="utf-8")
        source = source.replace("/home/kingbase/install/kingbase/bin", self.path + "/bin").replace("/run/kingbase-auth/password", self.path + "/password")
        self.launcher = self.root / "start.sh"
        self.launcher.write_text(source, encoding="utf-8", newline="\n")
        self.write_binary("kingbase", '#!/bin/bash\nprintf started >> "$RECEIPTS/started"\n')
        self.write_binary("initdb", '''#!/bin/bash
set -eu
while [ "$#" -gt 0 ]; do
  if [ "$1" = -D ]; then shift; data=$1; fi
  shift
done
printf init >> "$RECEIPTS/initialized"
printf 9 > "$data/SYS_VERSION"
[ "${FAIL_INIT:-0}" = 0 ] || exit 23
printf configuration > "$data/kingbase.conf"
printf authentication > "$data/sys_hba.conf"
''')

    def write_binary(self, name, content):
        path = self.bin / name
        path.write_text(content, encoding="utf-8", newline="\n")
        path.chmod(0o700)

    def launch(self, fail=False):
        env = {**os.environ, "DATA_DIR": self.path + "/data", "DB_USER": "system", "RECEIPTS": self.path, "FAIL_INIT": "1" if fail else "0"}
        return subprocess.run([BASH, str(self.launcher)], env=env, capture_output=True, text=True, timeout=15)

    def test_successful_restart_does_not_reinitialize(self):
        first = self.launch()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue((self.data / ".deerflow-init-complete").is_file())
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual((self.root / "initialized").read_text(), "init")
        self.assertEqual((self.root / "started").read_text(), "startedstarted")
        self.assertEqual((self.data / "sys_hba.conf").read_text().count("0.0.0.0/0"), 1)

    def test_partial_initialization_never_starts(self):
        first = self.launch(fail=True)
        self.assertEqual(first.returncode, 23)
        self.assertTrue((self.data / "SYS_VERSION").exists())
        self.assertNotEqual(self.launch().returncode, 0)
        self.assertFalse((self.root / "started").exists())
        self.assertFalse((self.data / ".deerflow-init-complete").exists())

    def test_unrecognized_existing_data_is_preserved(self):
        self.data.mkdir()
        (self.data / "old-file").write_text("keep")
        self.assertNotEqual(self.launch().returncode, 0)
        self.assertEqual((self.data / "old-file").read_text(), "keep")
        self.assertFalse((self.root / "initialized").exists())

    def test_completed_marker_cannot_hide_missing_configuration(self):
        self.assertEqual(self.launch().returncode, 0)
        (self.data / "kingbase.conf").unlink()
        self.assertNotEqual(self.launch().returncode, 0)
        self.assertEqual((self.root / "started").read_text(), "started")


if __name__ == "__main__":
    unittest.main()
