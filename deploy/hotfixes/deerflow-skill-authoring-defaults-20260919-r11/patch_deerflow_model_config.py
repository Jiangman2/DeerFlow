#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


MODEL_NAME = "deepseek-v4-flash"
OLD_BASE_URL = "http://model.ai.cnooc/member1/deepseek-v4-flash-291b-1m/v1"
TARGET_BASE_URL = "https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1"

# Retained as a fixture for compatibility tests. Production has used more than
# one harmless field order and max_tokens value, so patch_text() does not depend
# on byte-for-byte equality with this block.
OLD_BLOCK = """- name: deepseek-v4-flash
  display_name: DeepSeek-V4-Flash
  use: deerflow.models.robust_openai:RobustChatOpenAI
  model: DeepSeek-V4-Flash
  api_key: $DeepSeek_V4_API_KEY
  base_url: http://model.ai.cnooc/member1/deepseek-v4-flash-291b-1m/v1
  cumulative_stream_usage: true
  max_tokens: 65536
  timeout: 600
  profile:
    max_input_tokens: 1048576
  reasoning_effort: high
  supports_thinking: true
  supports_vision: false
  when_thinking_enabled:
    extra_body:
      enable_thinking: true
      thinking_budget: 4096
"""

NEW_BLOCK = """- name: deepseek-v4-flash
  display_name: DeepSeek-V4-Flash
  use: deerflow.models.robust_openai:RobustChatOpenAI
  model: DeepSeek-V4-Flash
  api_key: $DeepSeek_V4_API_KEY
  base_url: https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1
  cumulative_stream_usage: true
  max_tokens: 65536
  timeout: 600
  temperature: 0.0
  profile:
    max_input_tokens: 1048576
  reasoning_effort: high
  supports_thinking: true
  supports_vision: false
  when_thinking_enabled:
    extra_body:
      enable_thinking: true
  when_thinking_disabled:
    extra_body:
      enable_thinking: false
"""

_ITEM = re.compile(r"^-\s+name:\s*([^#\s]+)\s*$")
_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*(?:#.*)?$")


def write_lf(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _target_span(lines):
    model_headers = [index for index, line in enumerate(lines) if line == "models:"]
    if len(model_headers) != 1:
        raise ValueError("expected one top-level models section, found %d" % len(model_headers))
    section_start = model_headers[0] + 1
    section_end = len(lines)
    for index in range(section_start, len(lines)):
        if _TOP_LEVEL_KEY.match(lines[index]):
            section_end = index
            break

    items = []
    for index in range(section_start, section_end):
        match = _ITEM.match(lines[index])
        if match:
            items.append((index, match.group(1)))
    targets = [position for position, (_, name) in enumerate(items) if name == MODEL_NAME]
    if len(targets) != 1:
        raise ValueError("expected one %s model item, found %d" % (MODEL_NAME, len(targets)))
    item_position = targets[0]
    start = items[item_position][0]
    end = items[item_position + 1][0] if item_position + 1 < len(items) else section_end
    return start, end


def _require_count(lines, value, expected=1):
    count = lines.count(value)
    if count != expected:
        raise ValueError("unexpected count for %r: %d" % (value, count))


def _validate_known_production_block(block):
    lines = [line.rstrip() for line in block if line.strip()]
    allowed = {
        "- name: deepseek-v4-flash",
        "  display_name: DeepSeek-V4-Flash",
        "  use: deerflow.models.robust_openai:RobustChatOpenAI",
        "  model: DeepSeek-V4-Flash",
        "  api_key: $DeepSeek_V4_API_KEY",
        "  base_url: " + OLD_BASE_URL,
        "  base_url: " + TARGET_BASE_URL,
        "  cumulative_stream_usage: true",
        "  max_tokens: 32768",
        "  max_tokens: 65536",
        "  timeout: 600",
        "  temperature: 0.0",
        "  profile:",
        "    max_input_tokens: 1048576",
        "  reasoning_effort: high",
        "  supports_thinking: true",
        "  supports_vision: false",
        "  when_thinking_enabled:",
        "  when_thinking_disabled:",
        "    extra_body:",
        "      enable_thinking: true",
        "      enable_thinking: false",
        "      thinking_budget: 4096",
    }
    unknown = [line for line in lines if line not in allowed]
    if unknown:
        raise ValueError("unknown production model line: " + repr(unknown[0]))

    required = (
        "- name: deepseek-v4-flash",
        "  display_name: DeepSeek-V4-Flash",
        "  use: deerflow.models.robust_openai:RobustChatOpenAI",
        "  model: DeepSeek-V4-Flash",
        "  api_key: $DeepSeek_V4_API_KEY",
        "  cumulative_stream_usage: true",
        "  timeout: 600",
        "  profile:",
        "    max_input_tokens: 1048576",
        "  reasoning_effort: high",
        "  supports_thinking: true",
        "  supports_vision: false",
        "  when_thinking_enabled:",
        "      enable_thinking: true",
    )
    for value in required:
        _require_count(lines, value)

    base_url_count = lines.count("  base_url: " + OLD_BASE_URL) + lines.count("  base_url: " + TARGET_BASE_URL)
    if base_url_count != 1:
        raise ValueError("expected exactly one recognized base_url")
    max_token_count = lines.count("  max_tokens: 32768") + lines.count("  max_tokens: 65536")
    if max_token_count != 1:
        raise ValueError("expected exactly one recognized max_tokens value")
    if lines.count("  temperature: 0.0") not in (0, 1):
        raise ValueError("temperature field is duplicated")
    if lines.count("      thinking_budget: 4096") not in (0, 1):
        raise ValueError("thinking_budget field is duplicated")

    disabled_count = lines.count("  when_thinking_disabled:")
    false_count = lines.count("      enable_thinking: false")
    if disabled_count not in (0, 1) or false_count != disabled_count:
        raise ValueError("incomplete when_thinking_disabled block")
    _require_count(lines, "    extra_body:", 1 + disabled_count)


def patch_text(source: str) -> str:
    newline = "\n" if source.endswith("\n") else ""
    lines = source.splitlines()
    start, end = _target_span(lines)
    current = lines[start:end]
    _validate_known_production_block(current)
    target = NEW_BLOCK.rstrip("\n").splitlines()
    if current == target:
        return source
    result = "\n".join(lines[:start] + target + lines[end:]) + newline
    if result.count("  base_url: " + TARGET_BASE_URL) != 1:
        raise ValueError("target model endpoint count is not one")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check == bool(args.output):
        raise SystemExit("select exactly one of --check or --output")
    source = args.input.read_text(encoding="utf-8")
    target = patch_text(source)
    if args.output:
        write_lf(args.output, target)
    print("deerflow_primary_model_config_patch=PASS")


if __name__ == "__main__":
    main()
