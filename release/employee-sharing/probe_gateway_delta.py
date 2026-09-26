"""Run local tests with audited production modules plus the reviewed delta.

By default only recipe modules are replaced. With --audited-dependencies,
every available audited Python dependency also replaces the checkout version.
Uncaptured modules and new candidate modules still come from the local tree;
this is NOT an image-level or production acceptance test. No production
connection, source overwrite or rollout is performed.
"""

import argparse
import hashlib
import importlib.abc
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

from gateway_delta import RECIPES, compose


def module_name(relative):
    return (
        relative.removeprefix("packages/harness/")[:-3]
        .replace("/", ".")
        .removesuffix(".__init__")
    )


def read_verified(root, relative, expected):
    from source_baseline import _path

    _path(relative)
    path = root / relative
    if any(
        parent.is_symlink() or getattr(parent, "is_junction", lambda: False)()
        for parent in (path, *path.parents)
    ):
        raise ValueError("Linked production source")
    if not path.is_file() or path.stat().st_size > 2 * 1024**2:
        raise ValueError("Invalid production source file")
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != expected:
        raise ValueError("Production source checksum mismatch: " + relative)
    return source


def read_audited(baseline, relative, expected):
    return read_verified(baseline / "gateway", relative, expected)


def read_review():
    from source_baseline import _unique_object

    return json.loads(
        Path(__file__).with_name("gateway-review.json").read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
    )


def prepare(baseline, backend, *, audited_dependencies=False, reviewed_candidate=False):
    baseline, backend = Path(baseline), Path(backend)
    lock = json.loads((baseline / "SOURCE-BASELINE.json").read_text(encoding="utf-8"))
    modules, receipts = {}, []
    evidence = lock["components"]["gateway"]["files"]
    additions, reviewed = [], {}
    if reviewed_candidate:
        review = read_review()
        additions = review["additions"]
        reviewed = review["reviewed_candidates"]
        if (
            review.get("schema") != 1
            or review.get("scope") != "employee-sharing-only"
            or review.get("ready_for_rollout") is not False
            or len(additions) != len(set(additions))
            or set(additions) & set(RECIPES)
            or set(reviewed) != set(RECIPES) | set(additions)
        ):
            raise ValueError("Invalid candidate review scope")
        if (
            lock["components"]["gateway"]["image_id"].removeprefix("containerd://")
            != review["source_image_id"]
        ):
            raise ValueError("Candidate review production image differs")
        for relative in additions:
            if (
                relative not in lock["components"]["gateway"]["missing"]
                or relative in evidence
            ):
                raise ValueError(
                    "New candidate module requires explicit absence evidence: "
                    + relative
                )
        # Check all candidates before returning even an in-memory partial delta.
        for relative, checksum in reviewed.items():
            read_verified(backend, relative, checksum)
    if audited_dependencies:
        for relative, checksum in evidence.items():
            if relative.endswith(".py"):
                if not relative.startswith(("app/", "packages/harness/deerflow/")):
                    raise ValueError("Unexpected production module path")
                source = read_audited(baseline, relative, checksum)
                modules[module_name(relative)] = (source.decode("utf-8"), relative)
    for relative in RECIPES:
        source = read_audited(baseline, relative, evidence.get(relative))
        candidate = (
            read_verified(backend, relative, reviewed[relative])
            if reviewed_candidate
            else (backend / relative).read_bytes()
        )
        target, receipt = compose(relative, source, candidate)
        modules[module_name(relative)] = (target.decode("utf-8"), relative)
        receipts.append(receipt)
    for relative in additions:
        candidate = read_verified(backend, relative, reviewed[relative])
        compile(candidate, "employee-candidate:" + relative, "exec")
        modules[module_name(relative)] = (candidate.decode("utf-8"), relative)
        receipts.append(
            {
                "path": relative,
                "source_sha256": None,
                "candidate_sha256": reviewed[relative],
                "target_sha256": reviewed[relative],
                "production_absence": "VERIFIED",
            }
        )
    return modules, receipts


def stage_runtime(backend, modules, target):
    """Stage bounded test sources on local tmpfs, never a deployment directory.

    Overlay the audited/composed bytes on disk as well as in the parent loader:
    multiprocessing spawn must not silently reload checkout versions. Uncaptured
    Python dependencies still come from the checkout, explicitly NOT an image test.
    No environment, runtime data, caches or installed dependencies are copied.
    """
    from source_baseline import _path

    backend, target = Path(backend).absolute(), Path(target).absolute()
    if target.exists() or target.is_symlink():
        raise ValueError("Test staging destination already exists")
    for path in (backend, *backend.parents, *target.parents):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Linked test staging path")
    overlays = {}
    for source, relative in modules.values():
        _path(relative)
        if not relative.endswith(".py") or not relative.startswith(
            ("app/", "packages/harness/deerflow/")
        ):
            raise ValueError("Unsafe test module path")
        overlays[relative] = source.encode("utf-8")
    files, total = {}, 0

    def include(path):
        nonlocal total
        if (
            path.is_symlink()
            or getattr(path, "is_junction", lambda: False)()
            or not path.is_file()
            or path.stat().st_size > 2 * 1024**2
        ):
            raise ValueError("Unsafe test source")
        relative = path.relative_to(backend).as_posix()
        content = overlays.get(relative)
        if content is None:
            content = path.read_bytes()
        total += len(content)
        if total > 64 * 1024**2 or len(files) >= 10000:
            raise ValueError("Test source size limit exceeded")
        files[relative] = content

    for relative in ("app", "packages/harness/deerflow", "tests"):
        root = backend / relative
        for parent in (root, *root.parents):
            if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
                raise ValueError("Linked test source directory")
        for directory, children, names in os.walk(root, followlinks=False):
            children[:] = [
                n for n in children if not n.startswith(".") and n != "__pycache__"
            ]
            for child in children:
                path = Path(directory) / child
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    raise ValueError("Linked test source directory")
            for name in names:
                if name.endswith(".py"):
                    include(Path(directory) / name)
    if (backend / "pyproject.toml").exists():
        include(backend / "pyproject.toml")
    files.update(overlays)
    if sum(map(len, files.values())) > 64 * 1024**2:
        raise ValueError("Test source size limit exceeded")
    target.mkdir(mode=0o700)
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
    return target


class CandidateFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, modules):
        self.modules = modules
        self.loaded = set()

    def install(self):
        if any(name in sys.modules for name in self.modules):
            raise RuntimeError(
                "Candidate module already imported; use a fresh test process"
            )
        sys.meta_path.insert(0, self)

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.modules:
            package = self.modules[fullname][1].endswith("/__init__.py")
            spec = importlib.util.spec_from_loader(
                fullname,
                self,
                origin="employee-candidate:" + self.modules[fullname][1],
                is_package=package,
            )
            if package:
                # Child modules remain explicit local dependencies of this
                # partial test overlay; never pretend the closure is complete.
                for entry in sys.path:
                    directory = Path(entry).joinpath(*fullname.split("."))
                    if (directory / "__init__.py").is_file():
                        spec.submodule_search_locations = [str(directory)]
                        break
                else:
                    raise ImportError(
                        "Candidate package dependency root not found: " + fullname
                    )
            return spec
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source, relative = self.modules[module.__name__]
        module.__file__ = "employee-candidate:" + relative
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        self.loaded.add(module.__name__)


class CoverageGate:
    def __init__(self, finder, *, required=None):
        self.finder = finder
        self.required = set(finder.modules) if required is None else set(required)

    def pytest_sessionfinish(self, session, exitstatus):
        missing = sorted(self.required - self.finder.loaded)
        unchecked = sorted(
            set(self.finder.modules) - self.required - self.finder.loaded
        )
        print("employee_delta_loaded=" + ",".join(sorted(self.finder.loaded)))
        print("employee_delta_dependency_unloaded=" + ",".join(unchecked))
        if missing:
            print("employee_delta_probe=FAIL unloaded=" + ",".join(missing))
            session.exitstatus = 1
        else:
            scope = (
                "composed_modules_partial_audited_dependencies"
                if set(self.finder.modules) != self.required
                else "composed_modules_local_dependencies"
            )
            print(
                "employee_delta_probe="
                + ("PASS" if exitstatus == 0 else "FAIL")
                + " scope="
                + scope
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--audited-dependencies", action="store_true")
    parser.add_argument(
        "--stage-in-tmp",
        action="store_true",
        help="Run from a fresh temporary source tree; spawn uses composed bytes too",
    )
    parser.add_argument(
        "--reviewed-candidate",
        action="store_true",
        help="Freeze recipes and new modules to the reviewed candidate hashes",
    )
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    modules, receipts = prepare(
        args.baseline,
        args.backend,
        audited_dependencies=args.audited_dependencies,
        reviewed_candidate=args.reviewed_candidate,
    )
    if args.stage_in_tmp:
        with tempfile.TemporaryDirectory(prefix="employee-probe-") as directory:
            backend = stage_runtime(args.backend, modules, Path(directory) / "backend")
            original = Path.cwd()
            try:
                os.chdir(backend)
                return run_tests(backend, modules, receipts, args.pytest_args)
            finally:
                os.chdir(original)
    return run_tests(args.backend, modules, receipts, args.pytest_args)


def run_tests(backend, modules, receipts, pytest_args):
    sys.path[:0] = [
        str(backend.resolve()),
        str((backend / "packages/harness").resolve()),
        str((backend / "tests").resolve()),
    ]
    finder = CandidateFinder(modules)
    finder.install()
    for receipt in receipts:
        print("employee_delta_source=" + json.dumps(receipt, sort_keys=True))
    import pytest

    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    if not pytest_args:
        raise ValueError(
            "Explicit test paths required; no implicit full-suite execution"
        )
    try:
        return int(
            pytest.main(
                pytest_args,
                plugins=[
                    CoverageGate(
                        finder,
                        required={module_name(item["path"]) for item in receipts},
                    )
                ],
            )
        )
    finally:
        sys.meta_path.remove(finder)


if __name__ == "__main__":
    raise SystemExit(main())
