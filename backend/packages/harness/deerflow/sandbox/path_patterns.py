"""Shared construction of the host→virtual output-masking regexes.

The boundary and tail are deliberately private: ``build_output_mask_pattern`` is
the only supported way to spell this rule, so a third site cannot import the
pieces and hand-roll a variant that drifts from the other two.

Two independent call sites rewrite host paths back to their virtual form in
text that flows to the model: ``LocalSandbox._reverse_output_patterns`` (bash
output) and ``sandbox.tools._compiled_mask_patterns`` (glob/grep/ls results).
They must agree on where a host base is allowed to end, because both feed the
same downstream contract — a match that stops short of a real segment boundary
is rewritten to a container path that forward resolution then refuses to map
back.

Keeping one copy of that rule per file is what let it drift: #4035 added the
segment boundary to the reverse patterns and missed the masking patterns, and
#4053 had to add the same boundary to the other copy. This module holds the
rule once so a third copy cannot silently disagree.

The two sites are *not* identical, and the difference is deliberate — see
``separator_agnostic``.
"""

from __future__ import annotations

import re
from re import _compiler

# Only match where a host base ends at a real path-segment boundary, so a mount
# root does not match inside a sibling that merely shares its prefix
# (``.../skills`` inside ``.../skills-extra``).
#
# The class is text-oriented, not shell-oriented (contrast
# ``LocalSandbox._command_pattern``): both callers run over arbitrary command
# output or file listings, where a root can legitimately be followed by ``,``
# ``:`` or ``\``, all of which a shell-oriented class would reject.
#
# ``$`` is load-bearing: output ending exactly at a mount root would otherwise
# fail the lookahead and be emitted as the raw host path.
_SEGMENT_BOUNDARY = r"(?=/|$|[^\w./-])"

# The path tail following the base. ``[/\\]`` keeps Windows-separated paths
# matching; the negated class stops at whitespace and shell punctuation so a
# path embedded in a larger line is not over-consumed. A colon ends a PATH-style
# entry: consuming it would swallow later host paths inside one match and leave
# them unmasked. A drive colon inside the already-matched Windows base is not
# affected, and any literal colon suffix stays in the surrounding output.
_PATH_TAIL = r"(?:[/\\][^\s\"';&|<>():]*)?"


def build_output_mask_pattern(base: str, *, separator_agnostic: bool = False) -> re.Pattern[str]:
    """Compile the matcher for one host ``base`` in model-visible output.

    Args:
        base: Host path root to match (already resolved by the caller).
        separator_agnostic: Accept either separator *inside* the base, so a
            base captured with ``\\`` still matches output that spells the same
            path with ``/``. ``sandbox.tools`` needs this because it derives its
            bases from ``_path_variants`` (which yields Windows-style spellings)
            and matches them against output whose separators it does not
            control. ``LocalSandbox`` does not: its bases come from
            ``Path.resolve()``, so they already carry the running platform's
            separator, and relaxing them would widen what it masks.

    Returns:
        A compiled pattern matching ``base`` at a segment boundary, plus an
        optional path tail.
    """
    escaped = re.escape(base)
    if separator_agnostic:
        escaped = escaped.replace(r"\\", r"[/\\]")
    # CPython's public re.compile caches even per-thread workspace paths in a
    # process-global table. Call its compiler directly: callers already cache
    # the returned Pattern for their own lifetime. Do not purge a shared cache
    # (which would evict unrelated patterns or race other requests).
    return _compiler.compile(escaped + _SEGMENT_BOUNDARY + _PATH_TAIL, 0)
