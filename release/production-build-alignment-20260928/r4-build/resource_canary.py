"""RUN-start handshake before kernel sampling; no cgroup writes. Python 3.7+."""

import re


TOKEN = re.compile(r"ppt_runtime_resource_ready_[a-f0-9]{32}")


def canary_dockerfile(token):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError("Invalid owned canary token")
    # -I -S avoids user/site startup. The only command prints then sleeps, with
    # no workload/IO job dispatched while resource acceptance is pending.
    return (
        "FROM ppt-relay-gateway-base AS candidate\n"
        "RUN --network=none /app/backend/.venv/bin/python -I -S -c "
        '"import time; print(\'' + token + '\', flush=True); time.sleep(25)"\n'
    )


class ExecutorCanary:
    def __init__(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError("A fresh RUN-start token is required for the canary")
        # BuildKit --progress=plain prefixes actual stdout with vertex + time.
        # A Dockerfile RUN heading or echoed command MUST NOT unlock sampling.
        self.pattern = re.compile(
            rb"#[0-9]+[ \t]+[0-9]+(?:\.[0-9]+)?[ \t]+"
            + re.escape(token.encode("ascii"))
        )
        self.pending = b""
        self.dropping = False
        self.started = False
        self.consecutive = 0
        self.best = 0

    def feed(self, data):
        for index, fragment in enumerate(data.split(b"\n")):
            if index:
                if not self.dropping and self.pattern.fullmatch(self.pending.rstrip(b"\r")):
                    self.started = True
                self.pending = b""
                self.dropping = False
            if not self.dropping:
                if len(self.pending) + len(fragment) > 512:
                    self.pending = b""
                    self.dropping = True
                else:
                    self.pending += fragment

    def sample(self, reader, check):
        # Bounded incremental reads, including across arbitrary chunk splits.
        # No raw log/command/environment text is copied to public diagnostics.
        self.feed(reader.read(64 * 1024))
        if not self.started:
            return
        # Once real RUN stdout arrived, every missing/excessive limit remains an
        # immediate error. There is no time-based grace or exception swallowing.
        self.consecutive = self.consecutive + 1 if check() else 0
        self.best = max(self.best, self.consecutive)

    def verify(self):
        if not self.started:
            raise RuntimeError("Resource canary has no real RUN-start receipt")
        if self.best < 3:
            raise RuntimeError("Resource canary lacks three consecutive bounded RUN samples")


def bounded_leaf(value, ceiling, cgroup, device, direction, controller):
    # Only fixed kernel numeric metadata, never an arbitrary error/value dump.
    rendered = str(value).strip()
    valid_number = bool(re.fullmatch(r"[0-9]{1,20}", rendered))
    if valid_number and 0 < int(rendered) <= ceiling:
        return
    safe_value = rendered if valid_number or rendered in ("max", "missing") else "INVALID"
    raise RuntimeError(
        "Build leaf I/O invalid controller=%s direction=%s device=%s "
        "rate=%s ceiling=%s cgroup=%s"
        % (controller, direction, device, safe_value, ceiling, cgroup)
    )
