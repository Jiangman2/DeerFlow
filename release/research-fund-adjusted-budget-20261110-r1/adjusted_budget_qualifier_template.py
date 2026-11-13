"""Bound runner for byte-exact, synthetic behavior tests; no pytest installation."""
import base64, gzip, hashlib, importlib, inspect, io, itertools, json, math, re, sys, tempfile, time, types
from pathlib import Path

EXPECTED_CHECKS = None
CANDIDATE_TREE_SHA256 = None
TEST_SOURCE_MANIFEST_SHA256 = None
TEST_SOURCE_FILES = None
TEST_ARCHIVE = None
EXPECTED_COLLECTION = None
EXPECTED_CASES_SHA256 = None

def require(ok, code):
    if not ok:
        raise ValueError(code)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def manifest(root):
    require(root.is_dir() and not root.is_symlink(), 'SKILL_DIRECTORY_REQUIRED')
    rows = []
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'SKILL_SYMLINK_REFUSED')
        if path.is_dir() or '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        require(path.is_file() and path.stat().st_size <= 2097152, 'SKILL_REGULAR_FILE_BOUND')
        raw = path.read_bytes()
        rows.append({'path': path.relative_to(root).as_posix(), 'bytes': len(raw), 'sha256': sha(raw)})
    rows.sort(key=lambda row: row['path'])
    require(len(rows) == 31 and sum(row['bytes'] for row in rows) <= 8388608, 'SKILL_TOTAL_BOUND')
    return rows

class Raises:
    def __init__(self, expected, match=None):
        self.expected, self.match = expected, match
    def __enter__(self):
        return self
    def __exit__(self, kind, value, traceback):
        if kind is None:
            raise AssertionError('DID NOT RAISE ' + repr(self.expected))
        if not issubclass(kind, self.expected):
            return False
        if self.match is not None and re.search(self.match, str(value)) is None:
            raise AssertionError('Exception message did not match ' + repr(self.match))
        return True

class Approx:
    def __init__(self, expected, rel=1e-6, abs=1e-12):
        require(isinstance(expected, (int, float)), 'ONLY_SCALAR_APPROX_SUPPORTED')
        self.expected, self.rel, self.abs = expected, rel, abs
    def __eq__(self, actual):
        if actual == self.expected:
            return True
        if not math.isfinite(actual) or not math.isfinite(self.expected):
            return False
        return math.fabs(actual-self.expected) <= max(self.abs, self.rel*math.fabs(self.expected))

def parametrize(names, values, ids=None):
    names = tuple(n.strip() for n in names.split(',')) if isinstance(names, str) else tuple(names)
    require(names and len(set(names)) == len(names), 'PARAMETER_NAMES')
    values = list(values)
    require(values, 'EMPTY_PARAMETER_SET_REFUSED')
    if ids is not None:
        require(isinstance(ids, (list, tuple)) and len(ids) == len(values), 'PARAMETER_IDS')
    rows = []
    for value in values:
        row = (value,) if len(names) == 1 else tuple(value)
        require(len(row) == len(names), 'PARAMETER_ARITY')
        rows.append(dict(zip(names, row)))
    def decorate(fn):
        prior = list(getattr(fn, '_synthetic_parameters', []))
        require(not any(set(names) & set(row) for group in prior for row in group), 'DUPLICATE_PARAMETER')
        fn._synthetic_parameters = prior + [rows]
        return fn
    return decorate

def install_pytest_subset():
    # These are the only APIs used by the sealed tests. Unknown APIs fail closed.
    module = types.ModuleType('pytest')
    module.mark = types.SimpleNamespace(parametrize=parametrize)
    module.raises, module.approx = Raises, Approx
    sys.modules['pytest'] = module

def collect(modules):
    cases, counts = [], {}
    for module in modules:
        for name, fn in vars(module).items():
            if not name.startswith('test_') or not inspect.isfunction(fn) or fn.__module__ != module.__name__:
                continue
            groups = getattr(fn, '_synthetic_parameters', [])
            variants = itertools.product(*groups) if groups else [()]
            key = module.__name__ + '::' + name
            counts[key] = 0
            for variant in variants:
                args = {}
                for row in variant:
                    args.update(row)
                parameters = inspect.signature(fn).parameters
                require(all(p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD for p in parameters.values()), 'UNSUPPORTED_TEST_SIGNATURE')
                missing = set(parameters) - set(args)
                require(missing in (set(), {'tmp_path'}) and set(args) <= set(parameters), 'UNSUPPORTED_FIXTURE')
                cases.append((key, fn, args, 'tmp_path' in missing))
                counts[key] += 1
    return cases, counts

def parameter_signature(value, module):
    if value is None or type(value) in (bool, int, str):
        return [type(value).__name__, value]
    if type(value) is float:
        return ['float', repr(value)]
    if type(value) in (tuple, list):
        return [type(value).__name__, [parameter_signature(v, module) for v in value]]
    if type(value) is dict:
        require(all(type(k) is str for k in value), 'PARAMETER_DICT_KEYS')
        return ['dict', {k: parameter_signature(v, module) for k, v in value.items()}]
    if inspect.isfunction(value):
        return ['function', value.__module__, value.__qualname__, value.__code__.co_firstlineno]
    constants = sorted(k for k, v in vars(module).items() if v is value and not k.startswith('__'))
    require(type(value) is object and constants, 'UNSUPPORTED_PARAMETER_VALUE')
    return ['module_sentinel', module.__name__, constants]


def case_signatures(cases):
    result = [{'case': key, 'parameters': parameter_signature(args, sys.modules[fn.__module__])}
              for key, fn, args, needs_tmp in cases]
    return sorted(result, key=canonical)


def run(skill):
    require(sys.flags.optimize == 0, 'ASSERTIONS_MUST_BE_ENABLED')
    require(isinstance(EXPECTED_CHECKS, int) and EXPECTED_CHECKS > 30, 'UNBOUND_QUALIFIER')
    require(sha(canonical(TEST_SOURCE_FILES)) == TEST_SOURCE_MANIFEST_SHA256, 'TEST_MANIFEST_CHANGED')
    rows = manifest(skill)
    require(sha(canonical(rows)) == CANDIDATE_TREE_SHA256, 'CANDIDATE_CHANGED')
    compressed = base64.b64decode(TEST_ARCHIVE, validate=True)
    require(len(compressed) <= 131072, 'TEST_ARCHIVE_BOUND')
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
        raw = archive.read(1048577)
    require(len(raw) <= 1048576, 'TEST_SOURCE_BOUND')
    sources = json.loads(raw)
    require(set(sources) == {row['path'] for row in TEST_SOURCE_FILES}, 'TEST_SOURCE_SET_CHANGED')
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='research-fund-adjusted-budget-qualification-') as folder:
        root = Path(folder)
        copied = root/'skills/public/research-fund-settlement'
        for row in rows:
            data = (skill/row['path']).read_bytes()
            require(len(data) == row['bytes'] and sha(data) == row['sha256'], 'CANDIDATE_CHANGED_DURING_COPY')
            target = copied/row['path']; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        for row in TEST_SOURCE_FILES:
            require(re.fullmatch(r'tests/skills/test_research_fund_[a-z0-9_]+\.py', row['path']) is not None, 'TEST_PATH_REFUSED')
            data = base64.b64decode(sources[row['path']], validate=True)
            require(len(data) == row['bytes'] and sha(data) == row['sha256'], 'TEST_SOURCE_CHANGED')
            target = root/row['path']; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        install_pytest_subset()
        sys.path.insert(0, str(root/'tests/skills'))
        modules = [importlib.import_module(Path(row['path']).stem) for row in TEST_SOURCE_FILES]
        cases, counts = collect(modules)
        require(counts == EXPECTED_COLLECTION and len(cases) == EXPECTED_CHECKS, 'PYTEST_COLLECTION_PARITY_FAILED')
        require(sha(canonical(case_signatures(cases))) == EXPECTED_CASES_SHA256, 'PYTEST_PARAMETER_PARITY_FAILED')
        passed = 0
        for index, (key, fn, args, needs_tmp) in enumerate(cases):
            args = dict(args)
            if needs_tmp:
                target = root/'outputs'/str(index); target.mkdir(parents=True)
                args['tmp_path'] = target
            fn(**args)
            passed += 1
        require(manifest(copied) == rows and manifest(skill) == rows, 'QUALIFICATION_CHANGED_SKILL')
    return {'schema': 'research-fund-adjusted-budget-synthetic-qualification-run.v1', 'status': 'PASS',
            'passed': passed, 'failed': 0, 'skipped': 0, 'collection': counts,
            'skill_tree_sha256': CANDIDATE_TREE_SHA256,
            'test_source_manifest_sha256': TEST_SOURCE_MANIFEST_SHA256,
            'pytest_cases_sha256': EXPECTED_CASES_SHA256,
            'test_source_files': TEST_SOURCE_FILES, 'assertions_enabled': True,
            'production_actions': 0, 'user_files_read': False, 'network_access': False,
            'provider_requests': 0, 'database_access': False,
            'elapsed_seconds': round(time.monotonic()-started, 3)}

if __name__ == '__main__':
    try:
        require(len(sys.argv) == 2, 'ONE_STAGED_SKILL_PATH_REQUIRED')
        result = run(Path(sys.argv[1]).resolve())
    except Exception as exc:
        print(json.dumps({'status': 'FAIL', 'error_type': type(exc).__name__, 'detail': str(exc)}, ensure_ascii=True))
        raise
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
