import os
import re
import shutil
import subprocess

from deerflow.sandbox.local.list_dir import list_dir
from deerflow.sandbox.sandbox import Sandbox


class LocalSandbox(Sandbox):
    def __init__(self, id: str):
        """
        Initialize local sandbox.

        Args:
            id: Sandbox identifier
        """
        super().__init__(id)

    @staticmethod
    def _get_shell() -> tuple[str, str]:
        def _is_windows_wsl_bash(path: str) -> bool:
            p = os.path.normcase(os.path.normpath(path))
            return p.endswith(os.path.normcase(os.path.join("windows", "system32", "bash.exe")))

        if os.name == "nt":
            prefer_posix = (os.getenv("DEERFLOW_PREFER_POSIX_SHELL") or "").strip().lower() in {"1", "true", "yes", "y"}

            if not prefer_posix:
                pwsh = shutil.which("pwsh")
                if pwsh is not None:
                    return ("powershell", pwsh)
                powershell = shutil.which("powershell")
                if powershell is not None:
                    return ("powershell", powershell)

            candidates: list[str] = []
            for root in filter(None, [os.getenv("ProgramFiles"), os.getenv("ProgramFiles(x86)"), os.getenv("LocalAppData")]):
                candidates.extend(
                    [
                        os.path.join(root, "Git", "bin", "bash.exe"),
                        os.path.join(root, "Git", "usr", "bin", "bash.exe"),
                    ]
                )
            for c in candidates:
                if os.path.isfile(c) and os.access(c, os.X_OK):
                    return ("posix", c)

            for name in ("bash", "sh"):
                shell_from_path = shutil.which(name)
                if shell_from_path is None:
                    continue
                if not prefer_posix and _is_windows_wsl_bash(shell_from_path):
                    continue
                return ("posix", shell_from_path)

            if prefer_posix:
                raise RuntimeError("No suitable POSIX shell executable found. Install Git Bash (bash/sh) or disable DEERFLOW_PREFER_POSIX_SHELL.")

            raise RuntimeError("No suitable shell executable found. Install PowerShell or Git Bash, or switch to the Docker-based sandbox.")

        for shell in ("/bin/zsh", "/bin/bash", "/bin/sh"):
            if os.path.isfile(shell) and os.access(shell, os.X_OK):
                return ("posix", shell)

        for name in ("bash", "sh"):
            shell_from_path = shutil.which(name)
            if shell_from_path is not None:
                return ("posix", shell_from_path)

        raise RuntimeError("No suitable shell executable found. Install Git Bash (bash/sh) or switch to the Docker-based sandbox.")

    def execute_command(self, command: str) -> str:
        shell_type, shell_path = self._get_shell()
        if shell_type == "posix" and os.name == "nt":
            command = command.replace("\\", "/")
            command = re.sub(r"(?i)\b([a-z]):/", lambda m: f"/{m.group(1).lower()}/", command)
        if shell_type == "powershell":
            args = [
                shell_path,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ]
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=600,
            )
        else:
            result = subprocess.run(
                [shell_path, "-lc", command],
                capture_output=True,
                text=True,
                timeout=600,
            )
        output = result.stdout
        if result.stderr:
            output += f"\nStd Error:\n{result.stderr}" if output else result.stderr
        if result.returncode != 0:
            output += f"\nExit Code: {result.returncode}"

        return output if output else "(no output)"

    def list_dir(self, path: str, max_depth=2) -> list[str]:
        return list_dir(path, max_depth)

    def read_file(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        mode = "a" if append else "w"
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)

    def update_file(self, path: str, content: bytes) -> None:
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
