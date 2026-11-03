# Browserless unit-test dependency isolation

Applies only to the unfinished isolated dmdb merge after the BoxLite lifecycle
repair. HEAD `33688ae5`, incoming `523f1f0f`, branch
`fix/upload_test_env-enterprise-merge-20260930`. No merge commit or deployment.

The verbose full-suite run identified the previous wait near 8% at
`TestBrowserlessTools.test_web_fetch_tool_success`. The worker stack is in
`ReadabilityExtractor.extract_article` -> ReadabiliPy `have_node` ->
`run_npm_install` -> `subprocess.run/communicate/wait`. The async test awaits
this worker. Warm-pool checker threads are waiting normally and are not the
blocking stack shown by this diagnostic. The specific npm network/dependency
problem has not been established by this stack alone.

Existing tool tests mocked Browserless HTTP but left the third-party parser
real, allowing a supposed unit test to install dependencies. The same leak
exists in the explicit private-address opt-in success test. Public success,
HTTP-error and client-exception cases also depended on real DNS for example.com.

Patch only four existing test methods. Supply deterministic public DNS for
the three ordinary public fetch cases, retaining the real URL safety checker.
Supply a deterministic result at `deerflow.utils.readability`'s imported
`simple_json_from_html_string` boundary for the two success cases, retaining
the real extractor, Article, Markdown conversion, and async worker handoff.
Assert exact parser HTML/mode, requested URL and one fetch call; successful
extraction must execute off the caller/event-loop thread. HTTP errors and
client exceptions must not invoke parsing. Keep every original assertion and
all other test methods, especially private/metadata/DNS SSRF rejection, intact.
Do not skip tests or change production Browserless, URL-safety or parser code.

The separate existing `test_readability.py` still exercises parser fallback
and unexpected-exception behavior. Re-run it together with the complete
Browserless module and then resume the full suite. Unit isolation does not
fix or validate real Node/npm dependency readiness in a release image. A real
parser smoke test in a dependency-prepared environment remains a separate
release obligation; changing parser startup or dependency installation needs
its own reviewed runtime change, not an incidental full-suite workaround.

The helper verifies current index/worktree bytes of runtime, security,
performance and prior repairs, backs up replaced files, and leaves HEAD and
MERGE_HEAD unchanged. Local checks are dependency-light wiring/AST/patch
checks only, not a replacement for dependency-complete server pytest.
