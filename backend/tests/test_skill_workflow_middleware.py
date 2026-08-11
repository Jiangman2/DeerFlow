import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from deerflow.agents.middlewares.skill_workflow_middleware import (
    SkillWorkflowMiddleware,
    _failed_gate_call_allowed,
    _managed_artifact_violation,
    _render_workflow_state,
)


def _workflow(**updates):
    value = {
        "activation_id": "activation-1",
        "activation_message_id": "user-1",
        "skill_name": "ppt-master",
        "skill_path": "/mnt/skills/public/ppt-master/SKILL.md",
        "selected_authority": "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
        "status": "active",
        "completion_contract": "presentation_delivery",
        "required_capabilities": {"presentation": True},
        "evidence": [],
    }
    value.update(updates)
    if "loaded_authority_documents" not in updates:
        authority = value.get("selected_authority")
        documents = [authority] if authority else []
        value["loaded_authority_run_id"] = "run-1"
        value["loaded_authority_documents"] = documents
    return value


def _runtime(run_id="run-1"):
    return SimpleNamespace(context={"thread_id": "thread-1", "run_id": run_id})


@pytest.mark.asyncio
async def test_ppt_master_model_turn_wall_clock_timeout_returns_recoverable_wait():
    middleware = SkillWorkflowMiddleware()
    request = MagicMock()
    request.state = {
        "messages": [HumanMessage(content="继续")],
        "skill_workflow": _workflow(
            current_stage="project_manager.py:validate",
            resume_pointer="python3 project_manager.py validate /mnt/user-data/workspace/projects/demo",
        ),
    }
    request.runtime = _runtime()
    request.override.side_effect = lambda **kwargs: request

    async def stalled_handler(_request):
        await asyncio.sleep(1)
        return ModelResponse(result=[AIMessage(content="late")])

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._PPT_MASTER_MODEL_TURN_TIMEOUT_SECONDS",
        0.01,
    ):
        response = await middleware.awrap_model_call(request, stalled_handler)

    message = response.result[0]
    assert "单次模型回合已达到 0 秒上限" in message.content
    assert "project_manager.py:validate" in message.content
    assert message.additional_kwargs["deerflow_waiting_for_user"] is True
    assert message.additional_kwargs["deerflow_model_turn_timeout"] is True


@pytest.mark.asyncio
async def test_non_ppt_skill_model_turn_has_no_ppt_master_wall_clock_timeout():
    middleware = SkillWorkflowMiddleware()
    request = MagicMock()
    request.state = {
        "messages": [HumanMessage(content="继续")],
        "skill_workflow": _workflow(
            skill_name="other-skill",
            skill_path="/mnt/skills/public/other-skill/SKILL.md",
            completion_contract="other",
        ),
    }
    request.runtime = _runtime()
    request.override.side_effect = lambda **kwargs: request

    async def ordinary_handler(_request):
        await asyncio.sleep(0.02)
        return ModelResponse(result=[AIMessage(content="done")])

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._PPT_MASTER_MODEL_TURN_TIMEOUT_SECONDS",
        0.001,
    ):
        response = await middleware.awrap_model_call(request, ordinary_handler)

    assert response.result[0].content == "done"


def _tool_request(command: str, workflow: dict) -> ToolCallRequest:
    state = {
        "messages": [],
        "skill_workflow": workflow,
        "sandbox": {"sandbox_id": "sandbox-1"},
    }
    runtime = SimpleNamespace(
        context={"thread_id": "thread-1", "run_id": "run-1"},
        state=state,
    )
    return ToolCallRequest(
        tool_call={
            "id": "confirm-wait",
            "name": "bash",
            "args": {"command": command},
        },
        tool=None,
        state=state,
        runtime=runtime,
    )


def _file_tool_request(name: str, args: dict, workflow: dict) -> ToolCallRequest:
    state = {
        "messages": [],
        "skill_workflow": workflow,
        "sandbox": {"sandbox_id": "sandbox-1"},
    }
    runtime = SimpleNamespace(
        context={"thread_id": "thread-1", "run_id": "run-1"},
        state=state,
    )
    return ToolCallRequest(
        tool_call={"id": "template-contract", "name": name, "args": args},
        tool=None,
        state=state,
        runtime=runtime,
    )


def _template_contract_sandbox_files():
    project = "/mnt/user-data/workspace/projects/demo"
    prototype = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" '
        'data-pptx-master="master_01" data-pptx-layout="layout_cover">'
        '<rect id="layout-bg" width="1280" height="720" fill="#fff" data-pptx-layer="layout"/>'
        '<g id="slot-title" data-pptx-slot="title" data-pptx-frame="10 10 100 40" '
        'data-pptx-bounds="10 10 100 40" data-pptx-placeholder="title"><text>标题</text></g>'
        "</svg>"
    )
    return {
        f"{project}/confirm_ui/template_selection.json": json.dumps(
            {
                "status": "confirmed",
                "mode": "templates",
                "selection_sha256": "hash",
                "selections": [
                    {
                        "kind": "deck",
                        "id": "中国海油",
                        "workspace_root": "/mnt/skills/public/ppt-master/templates/decks/中国海油",
                    }
                ],
            }
        ),
        f"{project}/confirm_ui/template_handoff.json": json.dumps(
            {"status": "ready", "mode": "templates", "selection_sha256": "hash"}
        ),
        f"{project}/design_spec.md": (
            "| Page Count | 1 |\n"
            "- **Template Application**: P01 使用 01_cover.svg\n"
        ),
        f"{project}/spec_lock.md": (
            "## pptx_structure\n"
            "- mode: structured\n"
            "- template_reuse_scope: layout\n"
            "- template_adherence: adaptive\n\n"
            "## pptx_masters\n- master_01: CNOOC Master\n\n"
            "## pptx_layouts\n- layout_cover: Cover\n\n"
            "## page_pptx_layouts\n- P01: layout_cover\n\n"
            "## page_layouts\n- P01: 01_cover\n"
        ),
        f"{project}/templates/01_cover.svg": prototype,
    }


def _attach_template_manifest_execution(sandbox, files):
    """Return the fixed read-only manifest receipt used by template checks."""

    project = "/mnt/user-data/workspace/projects/demo"
    selection = json.loads(files[f"{project}/confirm_ui/template_selection.json"])
    source = selection["selections"][0]["workspace_root"].rstrip("/")

    def fingerprint(root):
        result = {}
        prefix = root.rstrip("/") + "/"
        for path, value in files.items():
            if not path.startswith(prefix):
                continue
            relative = path[len(prefix) :]
            if relative.split("/", 1)[0] not in {"templates", "images", "icons"}:
                continue
            payload = value if isinstance(value, bytes) else value.encode("utf-8")
            result[relative] = {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        return result

    receipt = "__DEERFLOW_PPT_TEMPLATE_MANIFEST_V1__" + json.dumps(
        {
            "ok": True,
            "roots": {
                "source": fingerprint(source),
                "target": fingerprint(project),
            },
        },
        sort_keys=True,
    )
    sandbox.execute_command.return_value = receipt


def test_failed_template_completion_gate_allows_official_read_only_workspace_validator():
    project = "/mnt/user-data/workspace/projects/demo"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        gate_owner_tool="bash",
        gate_owner_action=(
            "export SKILL_DIR='/mnt/skills/public/ppt-master' && "
            f"python3 ${{SKILL_DIR}}/scripts/confirm_ui/server.py {project} --complete-template-phase"
        ),
    )
    validator = {
        "name": "bash",
        "args": {
            "command": (
                "export SKILL_DIR='/mnt/skills/public/ppt-master' && "
                "python3 ${SKILL_DIR}/scripts/svg_quality_checker.py "
                '"/mnt/skills/public/ppt-master/templates/decks/中国海油/templates" --template-mode'
            )
        },
    }
    mutating_validator = {
        "name": "bash",
        "args": {
            "command": (
                "export SKILL_DIR='/mnt/skills/public/ppt-master' && "
                "python3 ${SKILL_DIR}/scripts/svg_quality_checker.py "
                '"/mnt/skills/public/ppt-master/templates/decks/中国海油/templates" --template-mode --fix'
            )
        },
    }

    assert _failed_gate_call_allowed(validator, workflow) is True
    assert _failed_gate_call_allowed(mutating_validator, workflow) is False


def test_records_selected_authority_and_design_requirements():
    middleware = SkillWorkflowMiddleware()
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "route-read",
                    "name": "read_file",
                    "args": {"path": "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"},
                },
                {
                    "id": "spec-read",
                    "name": "read_file",
                    "args": {"path": "/mnt/user-data/workspace/projects/demo/design_spec.md"},
                },
            ],
        ),
        ToolMessage(content="# Generate PPTX", tool_call_id="route-read"),
        ToolMessage(
            content=("| Speaker Notes | enabled |\n| Custom Animations | enabled |\n| Narration Audio | enabled |\n| AI Image Acquisition Path | api |\n- Native-ready: yes\n- **Template Application**: 中国海油 Deck 模板\n"),
            tool_call_id="spec-read",
        ),
    ]

    update = middleware.before_model(
        {"messages": messages, "skill_workflow": _workflow()},
        _runtime(),
    )

    assert update is not None
    workflow = update["skill_workflow"]
    assert workflow["selected_authority"].endswith("/workflows/generate-pptx.md")
    assert workflow["project_path"] == "/mnt/user-data/workspace/projects/demo"
    assert "/mnt/user-data/workspace/projects/demo/design_spec.md" in workflow["loaded_authority_documents"]
    assert workflow["required_capabilities"] == {
        "speaker_notes": True,
        "object_animations": True,
        "narration_audio": True,
        "ai_images": True,
        "native_charts": True,
        "template": True,
    }
    assert [entry["id"] for entry in workflow["evidence"]] == [
        "route-read",
        "spec-read",
    ]


def test_declared_artifact_authority_is_read_before_contract_write():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    design_reference = "/mnt/skills/public/ppt-master/templates/design_spec_reference.md"
    reference = "/mnt/skills/public/ppt-master/templates/spec_lock_reference.md"
    project = "/mnt/user-data/workspace/projects/demo"
    authority_messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "route-read", "name": "read_file", "args": {"path": route}}],
        ),
        ToolMessage(
            content=(
                "After final confirmation, author complete `design_spec.md` from "
                "`${SKILL_DIR}/templates/design_spec_reference.md`. After Gate 1, author `spec_lock.md` from "
                "`${SKILL_DIR}/templates/spec_lock_reference.md` plus approved Design Spec/context."
            ),
            tool_call_id="route-read",
        ),
    ]
    discovered = middleware.before_model(
        {"messages": authority_messages, "skill_workflow": _workflow(project_path=project)},
        _runtime(),
    )["skill_workflow"]
    workflow = {**_workflow(project_path=project), **discovered}

    assert workflow["artifact_authority_dependencies"] == {
        "design_spec.md": design_reference,
        "spec_lock.md": reference,
    }

    blocked_write = middleware.after_model(
        {
            "messages": [
                AIMessage(
                    id="premature-lock-write",
                    content="",
                    tool_calls=[
                        {
                            "id": "write-lock",
                            "name": "write_file",
                            "args": {"path": f"{project}/spec_lock.md", "content": "# Execution Lock"},
                        }
                    ],
                )
            ],
            "skill_workflow": workflow,
        },
        _runtime(),
    )

    assert blocked_write is not None
    replacement = blocked_write["messages"][0]
    assert isinstance(replacement, AIMessage)
    assert replacement.tool_calls[0]["name"] == "read_file"
    assert replacement.tool_calls[0]["args"] == {"path": reference}

    reference_update = middleware.before_model(
        {
            "messages": [
                replacement,
                ToolMessage(content="# Execution Lock Structure", tool_call_id=replacement.tool_calls[0]["id"]),
            ],
            "skill_workflow": workflow,
        },
        _runtime(),
    )["skill_workflow"]
    reloaded_workflow = {**workflow, **reference_update}
    assert reference in reloaded_workflow["loaded_authority_documents"]

    allowed_write = middleware.after_model(
        {
            "messages": [
                AIMessage(
                    id="authorized-lock-write",
                    content="",
                    tool_calls=[
                        {
                            "id": "write-lock-after-reference",
                            "name": "write_file",
                            "args": {"path": f"{project}/spec_lock.md", "content": "# Execution Lock"},
                        }
                    ],
                )
            ],
            "skill_workflow": reloaded_workflow,
        },
        _runtime(),
    )
    assert allowed_write is None


def test_ppt_master_spec_lock_projects_executor_closure_facts_and_dynamic_references():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    lock = f"{project}/spec_lock.md"
    content = """# Execution Lock

## mode
- mode: showcase

## visual_style
- visual_style: custom
- visual_style_references: dark-tech, editorial

## images
- hero: images/hero.png | source=ai | pattern=full-bleed | crop=slice

## page_charts
- P02: donut_chart

## pptx_structure
- mode: structured
"""
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "read-lock", "name": "read_file", "args": {"path": lock}}],
                ),
                ToolMessage(content=content, tool_call_id="read-lock"),
            ],
            "skill_workflow": _workflow(project_path=project),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["required_capabilities"] == {
        "images": True,
        "charts": True,
        "structured_pptx": True,
    }
    assert update["ppt_master_dynamic_authority_documents"] == [
        "/mnt/skills/public/ppt-master/references/modes/showcase.md",
        "/mnt/skills/public/ppt-master/references/visual-styles/dark-tech.md",
        "/mnt/skills/public/ppt-master/references/visual-styles/editorial.md",
    ]


def test_ppt_master_design_spec_persists_nonterminal_acquisition_rows():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    design = f"{project}/design_spec.md"
    content = """# Demo

## VIII. Image Resource List

| Filename | Acquire Via | Status |
| --- | --- | --- |
| pending.png | ai | Pending |
| retry.png | web | Failed |
| ready.png | ai | Generated |
| manual.png | ai | Needs-Manual |
| supplied.png | user | Existing |

## IX. Content Outline
"""
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "read-design", "name": "read_file", "args": {"path": design}}],
                ),
                ToolMessage(content=content, tool_call_id="read-design"),
            ],
            "skill_workflow": _workflow(project_path=project),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["ppt_master_nonterminal_resources"] == ["pending.png", "retry.png"]
    assert update["ppt_master_design_spec_refresh_required"] is False


def test_ppt_master_nonterminal_acquisition_blocks_executor_entry():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    blocked = middleware.after_model(
        {
            "messages": [
                AIMessage(
                    id="svg-before-acquisition",
                    content="",
                    tool_calls=[
                        {
                            "id": "write-svg",
                            "name": "write_file",
                            "args": {"path": f"{project}/svg_output/01_cover.svg", "content": "<svg/>"},
                        }
                    ],
                )
            ],
            "skill_workflow": _workflow(
                project_path=project,
                ppt_master_nonterminal_resources=["pending.png"],
            ),
        },
        _runtime(),
    )

    assert blocked is not None
    assert blocked["jump_to"] == "model"
    assert blocked["skill_workflow"]["gate_status"] == "ppt-master-acquisition-incomplete"
    assert blocked["skill_workflow"]["resume_pointer"] == f"{project}/design_spec.md"


def test_ppt_master_design_spec_replace_requires_full_read_before_executor():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    design = f"{project}/design_spec.md"
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "replace-status",
                            "name": "str_replace",
                            "args": {"path": design, "old": "Pending", "new": "Generated"},
                        }
                    ],
                ),
                ToolMessage(content="OK", tool_call_id="replace-status"),
            ],
            "skill_workflow": _workflow(project_path=project),
        },
        _runtime(),
    )["skill_workflow"]
    workflow = {**_workflow(project_path=project), **update}

    blocked = middleware.after_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "write-svg",
                            "name": "write_file",
                            "args": {"path": f"{project}/svg_output/01.svg", "content": "<svg/>"},
                        }
                    ],
                )
            ],
            "skill_workflow": workflow,
        },
        _runtime(),
    )

    assert blocked is not None
    assert blocked["messages"][0].tool_calls[0]["args"]["path"] == design


def test_ppt_master_svg_write_loads_complete_triggered_executor_closure():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    executor = "/mnt/skills/public/ppt-master/references/executor-base.md"
    dynamic = "/mnt/skills/public/ppt-master/references/modes/showcase.md"
    workflow = _workflow(
        project_path=project,
        required_capabilities={"images": True, "native_charts": True, "structured_pptx": True},
        ppt_master_dynamic_authority_documents=[dynamic],
        loaded_authority_run_id="run-1",
        loaded_authority_documents=[route, executor],
    )
    state = {
        "messages": [
            AIMessage(
                id="svg-before-closure",
                content="",
                tool_calls=[
                    {
                        "id": "write-svg",
                        "name": "write_file",
                        "args": {"path": f"{project}/svg_output/01_cover.svg", "content": "<svg/>"},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    blocked = middleware.after_model(state, _runtime())

    assert blocked is not None
    replacement = blocked["messages"][0]
    paths = [call["args"]["path"] for call in replacement.tool_calls]
    assert paths == [
        "/mnt/skills/public/ppt-master/references/shared-standards-core.md",
        "/mnt/skills/public/ppt-master/references/svg-effects.md",
        "/mnt/skills/public/ppt-master/references/native-shape-authoring.md",
        "/mnt/skills/public/ppt-master/references/semantic-svg.md",
        dynamic,
        "/mnt/skills/public/ppt-master/references/executor-image.md",
        "/mnt/skills/public/ppt-master/references/image-layout-spec.md",
        "/mnt/skills/public/ppt-master/references/image-layout-patterns.md",
        "/mnt/skills/public/ppt-master/references/svg-image-embedding.md",
        "/mnt/skills/public/ppt-master/references/executor-chart.md",
        "/mnt/skills/public/ppt-master/references/native-data-interface.md",
        "/mnt/skills/public/ppt-master/references/executor-structured.md",
        "/mnt/skills/public/ppt-master/references/pptx-structure-interface.md",
    ]


def test_ppt_master_svg_write_allows_complete_current_run_closure():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    closure = [
        "/mnt/skills/public/ppt-master/references/executor-base.md",
        "/mnt/skills/public/ppt-master/references/shared-standards-core.md",
        "/mnt/skills/public/ppt-master/references/svg-effects.md",
        "/mnt/skills/public/ppt-master/references/native-shape-authoring.md",
        "/mnt/skills/public/ppt-master/references/semantic-svg.md",
        "/mnt/skills/public/ppt-master/references/executor-image.md",
        "/mnt/skills/public/ppt-master/references/image-layout-spec.md",
        "/mnt/skills/public/ppt-master/references/image-layout-patterns.md",
        "/mnt/skills/public/ppt-master/references/svg-image-embedding.md",
    ]
    result = middleware.after_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "write-svg",
                            "name": "write_file",
                            "args": {"path": f"{project}/svg_output/01_cover.svg", "content": "<svg/>"},
                        }
                    ],
                )
            ],
            "skill_workflow": _workflow(
                project_path=project,
                required_capabilities={"images": True},
                loaded_authority_run_id="run-1",
                loaded_authority_documents=[route, *closure],
            ),
        },
        _runtime(),
    )

    assert result is None


def test_ppt_master_authority_remains_valid_across_same_run_compaction():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    closure = [
        "/mnt/skills/public/ppt-master/references/executor-base.md",
        "/mnt/skills/public/ppt-master/references/shared-standards-core.md",
        "/mnt/skills/public/ppt-master/references/svg-effects.md",
        "/mnt/skills/public/ppt-master/references/native-shape-authoring.md",
        "/mnt/skills/public/ppt-master/references/semantic-svg.md",
        "/mnt/skills/public/ppt-master/references/executor-chart.md",
        "/mnt/skills/public/ppt-master/references/native-data-interface.md",
    ]
    result = middleware.after_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "write-svg",
                            "name": "write_file",
                            "args": {"path": f"{project}/svg_output/01_cover.svg", "content": "<svg/>"},
                        }
                    ],
                )
            ],
            "summary_text": "new summary produced after the fresh authority read batch",
            "skill_workflow": _workflow(
                project_path=project,
                required_capabilities={"charts": True},
                loaded_authority_run_id="run-1",
                loaded_authority_documents=[route, *closure],
                loaded_authority_context_fingerprint="previous-summary",
            ),
        },
        _runtime(),
    )

    assert result is None


def test_other_skill_compaction_still_requires_authority_reload():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    result = middleware.after_model(
        {
            "messages": [AIMessage(content="Continue building.")],
            "summary_text": "a later compacted context",
            "skill_workflow": _workflow(
                skill_name="other-skill",
                skill_path="/mnt/skills/public/other-skill/SKILL.md",
                resume_documents=[route],
                loaded_authority_run_id="run-1",
                loaded_authority_documents=[route],
                loaded_authority_context_fingerprint="earlier-summary",
            ),
        },
        _runtime(),
    )

    assert result is not None
    assert result["messages"][0].tool_calls[0]["args"] == {"path": route}


def test_ppt_master_closure_does_not_affect_other_skills():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    result = middleware.after_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "write-svg",
                            "name": "write_file",
                            "args": {"path": f"{project}/svg_output/01.svg", "content": "<svg/>"},
                        }
                    ],
                )
            ],
            "skill_workflow": _workflow(
                skill_name="other-skill",
                skill_path="/mnt/skills/public/other-skill/SKILL.md",
                selected_authority="/mnt/skills/public/other-skill/workflows/generate.md",
                project_path=project,
            ),
        },
        _runtime(),
    )

    assert result is None


def test_skill_owner_command_drops_terminal_head_pipe_to_preserve_real_exit_status():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = (
        "export SKILL_DIR='/mnt/skills/public/ppt-master' && "
        f"cd {project} && python3 ${{SKILL_DIR}}/scripts/image_gen.py "
        "--manifest images/prompts.json --output images 2>&1 | head -50"
    )
    result = middleware.after_model(
        {
            "messages": [
                AIMessage(
                    id="piped-owner",
                    content="",
                    tool_calls=[{"id": "image-owner", "name": "bash", "args": {"command": command}}],
                )
            ],
            "skill_workflow": _workflow(project_path=project),
        },
        _runtime(),
    )

    assert result is not None
    replacement = result["messages"][0]
    assert isinstance(replacement, AIMessage)
    rewritten = replacement.tool_calls[0]["args"]["command"]
    assert rewritten.endswith("--manifest images/prompts.json --output images")
    assert "| head" not in rewritten


def test_non_skill_pipeline_is_not_rewritten():
    middleware = SkillWorkflowMiddleware()
    result = middleware.after_model(
        {
            "messages": [
                AIMessage(
                    id="ordinary-pipeline",
                    content="",
                    tool_calls=[{"id": "ordinary", "name": "bash", "args": {"command": "printf x | head -1"}}],
                )
            ],
            "skill_workflow": _workflow(),
        },
        _runtime(),
    )
    assert result is None


def test_project_manager_init_receipt_owns_project_root_and_research_file_cannot_overwrite():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/cnooc_intelligent_demo_ppt169_20260809"
    research = "/mnt/user-data/workspace/projects/cnooc_intelligent_demo_research.md"
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "init",
                    "name": "bash",
                    "args": {"command": ("export SKILL_DIR='/mnt/skills/public/ppt-master' && python3 \"$SKILL_DIR/scripts/project_manager.py\" init cnooc_intelligent_demo --format ppt169")},
                }
            ],
        ),
        ToolMessage(
            content=(f"Project created: {project}\nCanvas: PPT 16:9 (1280×720)\n[OK] Project initialized: {project}\n"),
            tool_call_id="init",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "research",
                    "name": "write_file",
                    "args": {"path": research, "content": "## Research Brief\n"},
                }
            ],
        ),
        ToolMessage(content="OK", tool_call_id="research"),
    ]

    workflow = middleware.before_model(
        {"messages": messages, "skill_workflow": _workflow()},
        _runtime(),
    )["skill_workflow"]

    assert workflow["project_path"] == project
    assert workflow["project_path_source"] == "project_manager_init_receipt"
    assert research not in workflow.get("resume_documents", [])


def test_fresh_project_does_not_require_unauthored_design_contracts():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/cnooc_intelligent_demo_ppt169_20260809"
    state = {
        "messages": [
            AIMessage(
                id="continue-template-selection",
                content="",
                tool_calls=[
                    {
                        "id": "inspect-template",
                        "name": "read_file",
                        "args": {"path": "/mnt/skills/public/ppt-master/templates/decks/decks_index.json"},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(project_path=project),
    }

    assert middleware.after_model(state, _runtime()) is None


def test_successful_project_contract_writes_become_later_run_authorities():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo_ppt169_20260809"
    design = f"{project}/design_spec.md"
    lock = f"{project}/spec_lock.md"
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "design-write",
                    "name": "write_file",
                    "args": {
                        "path": design,
                        "content": "| Speaker Notes | enabled |\n- Native-ready: yes\n",
                    },
                },
                {
                    "id": "lock-write",
                    "name": "write_file",
                    "args": {"path": lock, "content": "pptx_structure:\n  mode: structured\n"},
                },
            ],
        ),
        ToolMessage(content="OK", tool_call_id="design-write"),
        ToolMessage(content="OK", tool_call_id="lock-write"),
    ]

    workflow = middleware.before_model(
        {"messages": messages, "skill_workflow": _workflow(project_path=project)},
        _runtime(),
    )["skill_workflow"]

    assert workflow["resume_documents"][-2:] == [design, lock]
    assert design in workflow["loaded_authority_documents"]
    assert lock in workflow["loaded_authority_documents"]
    assert workflow["required_capabilities"] == {
        "speaker_notes": True,
        "native_charts": True,
    }


def test_stage_document_churn_does_not_evict_project_contracts():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo_ppt169_20260809"
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    design = f"{project}/design_spec.md"
    lock = f"{project}/spec_lock.md"
    stage_documents = [f"/mnt/skills/public/ppt-master/references/stage-{index}.md" for index in range(1, 7)]
    messages = []
    for index, path in enumerate(stage_documents, start=1):
        call_id = f"stage-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"id": call_id, "name": "read_file", "args": {"path": path}}],
                ),
                ToolMessage(content=f"# Stage {index}", tool_call_id=call_id),
            ]
        )

    workflow = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(
                project_path=project,
                resume_documents=[route, design, lock],
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert workflow["resume_documents"] == [
        route,
        *stage_documents,
        design,
        lock,
    ]


def test_failed_skill_script_invalidates_current_run_authority_and_keeps_detail():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    executor = "/mnt/skills/public/ppt-master/references/executor-structured.md"
    design = f"{project}/design_spec.md"
    lock = f"{project}/spec_lock.md"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py {project} --stage final --json"
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "quality", "name": "bash", "args": {"command": command}}],
        ),
        ToolMessage(
            content="Error: adaptive output reused a prototype Layout; assign a new key and name",
            tool_call_id="quality",
            status="error",
        ),
    ]

    update = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(
                project_path=project,
                resume_documents=[route, executor, design, lock],
                loaded_authority_run_id="run-1",
                loaded_authority_documents=[route, executor, design, lock],
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["status"] == "blocked"
    assert update["gate_status"] == "failed"
    assert update["loaded_authority_run_id"] == ""
    assert update["loaded_authority_documents"] == []
    rendered = _render_workflow_state({**_workflow(), **update})
    assert "failure_detail: Error: adaptive output reused" in rendered


def test_long_failed_owner_receipt_persists_both_diagnostic_head_and_blocker_tail():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py {project} --stage final --json"
    detail = "Error: checker failed\n" + ("warning line\n" * 180) + "BLOCKING SUMMARY: layout key mismatch"
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "quality-long", "name": "bash", "args": {"command": command}}],
        ),
        ToolMessage(
            content=detail,
            tool_call_id="quality-long",
            status="error",
        ),
    ]

    update = middleware.before_model(
        {"messages": messages, "skill_workflow": _workflow(project_path=project)},
        _runtime(),
    )["skill_workflow"]

    persisted = update["gate_detail"]
    assert persisted.startswith("Error: checker failed")
    assert "middle omitted by host checkpoint" in persisted
    assert persisted.endswith("BLOCKING SUMMARY: layout key mismatch")
    assert len(persisted) <= 1200
    assert update["evidence"][0]["detail"] == persisted


def test_ppt_master_context_compaction_does_not_reload_authority_in_same_run():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    state = {
        "messages": [AIMessage(id="continue", content="Continue building.")],
        "summary_text": "The earlier executor authority was compacted.",
        "skill_workflow": _workflow(
            selected_authority=route,
            resume_documents=[route],
            loaded_authority_run_id="run-1",
            loaded_authority_documents=[route],
            loaded_authority_context_fingerprint="",
        ),
    }

    reload_update = middleware.after_model(state, _runtime("run-1"))

    assert reload_update is None


def test_authority_reload_tool_ids_change_after_in_run_compaction():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    attempted_action = AIMessage(
        id="attempt",
        content="",
        tool_calls=[{"id": "bash", "name": "bash", "args": {"command": "echo skip"}}],
    )
    workflow = _workflow(
        skill_name="other-skill",
        skill_path="/mnt/skills/public/other-skill/SKILL.md",
        selected_authority=route,
        resume_documents=[route],
        loaded_authority_run_id="run-1",
        loaded_authority_documents=[route],
        loaded_authority_context_fingerprint="outdated",
    )

    first_reload = middleware.after_model(
        {
            "messages": [attempted_action],
            "summary_text": "first compacted context",
            "skill_workflow": workflow,
        },
        _runtime("run-1"),
    )
    second_reload = middleware.after_model(
        {
            "messages": [attempted_action],
            "summary_text": "second compacted context",
            "skill_workflow": workflow,
        },
        _runtime("run-1"),
    )

    assert first_reload is not None
    assert second_reload is not None
    first_id = first_reload["messages"][0].tool_calls[0]["id"]
    second_id = second_reload["messages"][0].tool_calls[0]["id"]
    assert first_id != second_id


def test_failed_validator_persists_exact_resume_pointer():
    middleware = SkillWorkflowMiddleware()
    command = "python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py /mnt/user-data/workspace/projects/demo --stage final --json"
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "quality", "name": "bash", "args": {"command": command}}],
        ),
        ToolMessage(
            content="Error: 2 blocking issues",
            tool_call_id="quality",
            status="error",
        ),
    ]

    update = middleware.before_model(
        {"messages": messages, "skill_workflow": _workflow()},
        _runtime(),
    )["skill_workflow"]

    assert update["status"] == "blocked"
    assert update["gate_status"] == "failed"
    assert update["current_stage"] == "svg_quality_checker.py:final"
    assert update["resume_pointer"] == command
    assert update["gate_owner_receipt_id"] == "quality"
    assert update["gate_owner_tool"] == "bash"
    assert update["gate_owner_action"] == command
    assert update["gate_owner_stage"] == "svg_quality_checker.py:final"


def test_failed_owner_state_is_rebuilt_and_auxiliary_receipts_cannot_replace_it():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    lock = f"{project}/spec_lock.md"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py {project} --stage final --json"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        # Simulate a legacy/polluted checkpoint written before the owner fields
        # existed: the resume pointer survived, but an auxiliary read replaced
        # the visible stage and detail.
        current_stage="read:generate-pptx.md",
        gate_detail="Error invoking read_file: description field required",
        resume_pointer=command,
        evidence=[
            {
                "id": "failed-checker",
                "tool": "bash",
                "status": "failed",
                "action": command,
                "detail": "12 blocking SVG contract errors remain",
            }
        ],
    )
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "route-read", "name": "read_file", "args": {"path": route}},
                {
                    "id": "repair",
                    "name": "str_replace",
                    "args": {"path": lock, "old_str": "Pending", "new_str": "Generated"},
                },
                {"id": "diagnostic-read", "name": "read_file", "args": {"path": f"{project}/missing.md"}},
            ],
        ),
        ToolMessage(content="# Generate PPTX", tool_call_id="route-read"),
        ToolMessage(content="Updated the file.", tool_call_id="repair"),
        ToolMessage(
            content="Error: File not found",
            tool_call_id="diagnostic-read",
            status="error",
        ),
    ]

    update = middleware.before_model(
        {"messages": messages, "skill_workflow": workflow},
        _runtime(),
    )["skill_workflow"]

    assert update["status"] == "blocked"
    assert update["gate_status"] == "failed"
    assert update["current_stage"] == "svg_quality_checker.py:final"
    assert update["gate_detail"] == "12 blocking SVG contract errors remain"
    assert update["resume_pointer"] == command
    assert update["gate_owner_receipt_id"] == "failed-checker"
    assert update["gate_owner_tool"] == "bash"
    assert update["gate_owner_action"] == command
    assert update["gate_owner_stage"] == "svg_quality_checker.py:final"
    assert update["last_project_mutation_receipt_id"] == "repair"
    assert [entry["id"] for entry in update["evidence"]] == [
        "route-read",
        "repair",
        "diagnostic-read",
    ]


def test_failed_gate_blocks_unrelated_stage_actions_until_recovery():
    middleware = SkillWorkflowMiddleware()
    failed = "python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py /mnt/user-data/workspace/projects/demo --stage final --json"
    state = {
        "messages": [
            AIMessage(
                id="skip-failed-gate",
                content="",
                tool_calls=[
                    {
                        "id": "export",
                        "name": "bash",
                        "args": {"command": "python3 export_pptx.py demo"},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            status="blocked",
            gate_status="failed",
            resume_pointer=failed,
        ),
    }

    blocked = middleware.after_model(state, _runtime())

    assert blocked is not None
    assert blocked["jump_to"] == "model"
    assert blocked["skill_workflow"]["gate_status"] == "failed"
    assert blocked["skill_workflow"]["resume_pointer"] == failed


def test_failed_gate_allows_exact_retry_and_read_only_diagnosis():
    middleware = SkillWorkflowMiddleware()
    failed = "python3 checker.py /mnt/user-data/workspace/projects/demo"
    base = _workflow(status="blocked", gate_status="failed", resume_pointer=failed)

    retry_state = {
        "messages": [
            AIMessage(
                id="retry",
                content="",
                tool_calls=[{"id": "retry-call", "name": "bash", "args": {"command": failed}}],
            )
        ],
        "skill_workflow": base,
    }
    read_state = {
        "messages": [
            AIMessage(
                id="read",
                content="",
                tool_calls=[
                    {
                        "id": "read-call",
                        "name": "read_file",
                        "args": {"path": "/mnt/user-data/workspace/projects/demo/spec_lock.md"},
                    }
                ],
            )
        ],
        "skill_workflow": base,
    }

    assert middleware.after_model(retry_state, _runtime()) is None
    assert middleware.after_model(read_state, _runtime()) is None


def test_failed_gate_allows_read_only_diagnosis_in_selected_skill_root():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    failed = f"python3 /mnt/skills/public/ppt-master/scripts/icon_sync.py {project} tabler-outline"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        gate_owner_tool="bash",
        gate_owner_action=failed,
        resume_pointer=failed,
    )
    diagnostic = (
        "export SKILL_DIR='/mnt/skills/public/ppt-master' && "
        'rg --files "${SKILL_DIR}/templates/icons/tabler-outline" -g \'*database*.svg\''
    )
    state = {
        "messages": [
            AIMessage(
                id="skill-root-diagnostic",
                content="",
                tool_calls=[
                    {
                        "id": "skill-root-diagnostic-call",
                        "name": "bash",
                        "args": {"command": diagnostic},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    assert middleware.after_model(state, _runtime()) is None


def test_sequence_guard_attempts_reset_after_durable_failed_gate_progress():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    failed = f"python3 /mnt/skills/public/ppt-master/scripts/icon_sync.py {project} tabler-outline"
    invalid = AIMessage(
        id="invalid-recovery",
        content="",
        tool_calls=[{"id": "invalid-call", "name": "bash", "args": {"command": "pwd"}}],
    )
    before_progress = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        gate_owner_receipt_id="failed-owner-v1",
        gate_owner_tool="bash",
        gate_owner_action=failed,
        resume_pointer=failed,
        last_project_mutation_receipt_id="mutation-v1",
        failed_gate_attempt_mutation_receipt_id="mutation-v1",
        loaded_authority_run_id="same-run",
        loaded_authority_documents=["/mnt/skills/public/ppt-master/workflows/generate-pptx.md"],
    )
    runtime = _runtime("same-run")

    first = middleware.after_model({"messages": [invalid], "skill_workflow": before_progress}, runtime)
    second = middleware.after_model({"messages": [invalid], "skill_workflow": before_progress}, runtime)

    assert first is not None and first["jump_to"] == "model"
    assert second is not None and second["jump_to"] == "model"

    after_progress = {
        **before_progress,
        "gate_owner_receipt_id": "failed-owner-v2",
        "last_project_mutation_receipt_id": "mutation-v2",
        "failed_gate_attempt_mutation_receipt_id": "mutation-v2",
    }
    fresh_boundary = middleware.after_model(
        {"messages": [invalid], "skill_workflow": after_progress},
        runtime,
    )

    assert fresh_boundary is not None and fresh_boundary["jump_to"] == "model"


def test_repeated_unchanged_owner_failure_keeps_reloaded_authority():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    design = f"{project}/design_spec.md"
    lock = f"{project}/spec_lock.md"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py {project} --stage final --json"
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "unchanged-retry", "name": "bash", "args": {"command": command}}],
        ),
        ToolMessage(
            content="Error: the same 16 blocking issues remain",
            tool_call_id="unchanged-retry",
            status="error",
        ),
    ]

    update = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(
                project_path=project,
                status="blocked",
                gate_status="failed",
                resume_pointer=command,
                loaded_authority_run_id="run-1",
                loaded_authority_documents=[route, design, lock],
                last_project_mutation_receipt_id="repair-before-first-failure",
                failed_gate_attempt_mutation_receipt_id="repair-before-first-failure",
                failed_gate_repeat_count=1,
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["failed_gate_repeat_count"] == 2
    assert update["loaded_authority_run_id"] == "run-1"
    assert update["loaded_authority_documents"] == [route, design, lock]


def test_second_unchanged_owner_retry_requires_project_mutation():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py {project} --stage final --json"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        resume_pointer=command,
        last_project_mutation_receipt_id="repair-marker",
        failed_gate_attempt_mutation_receipt_id="repair-marker",
        failed_gate_repeat_count=2,
    )
    retry_state = {
        "messages": [
            AIMessage(
                id="unchanged-owner-retry",
                content="",
                tool_calls=[{"id": "retry-call", "name": "bash", "args": {"command": command}}],
            )
        ],
        "skill_workflow": workflow,
    }

    blocked = middleware.after_model(retry_state, _runtime())

    assert blocked is not None
    assert blocked["jump_to"] == "model"
    assert "without a durable project mutation" in blocked["skill_workflow"]["guard_detail"]

    repair_path = f"{project}/spec_lock.md"
    repair_messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "durable-repair",
                    "name": "str_replace",
                    "args": {"path": repair_path, "old": "Pending", "new": "Generated"},
                }
            ],
        ),
        ToolMessage(content="Updated the file.", tool_call_id="durable-repair"),
    ]
    repair_update = middleware.before_model(
        {"messages": repair_messages, "skill_workflow": workflow},
        _runtime(),
    )["skill_workflow"]
    repaired_workflow = {**workflow, **repair_update}

    assert repaired_workflow["last_project_mutation_receipt_id"] == "durable-repair"
    second_repair_state = {
        "messages": [
            AIMessage(
                id="second-repair-from-stale-report",
                content="",
                tool_calls=[
                    {
                        "id": "second-repair",
                        "name": "str_replace",
                        "args": {
                            "path": f"{project}/another.svg",
                            "old": "old",
                            "new": "new",
                        },
                    }
                ],
            )
        ],
        "skill_workflow": repaired_workflow,
    }
    stale_block = middleware.after_model(second_repair_state, _runtime())

    assert stale_block is not None
    assert stale_block["jump_to"] == "model"
    assert "generated reports are stale" in stale_block["skill_workflow"]["guard_detail"]
    assert (
        middleware.after_model(
            {**retry_state, "skill_workflow": repaired_workflow},
            _runtime(),
        )
        is None
    )


def test_failed_gate_stale_snapshot_is_rendered_and_owner_retry_is_prompted():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/example/checker.py {project} --stage final"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        gate_detail="old validation report",
        gate_owner_tool="bash",
        gate_owner_action=command,
        resume_pointer=command,
        failed_gate_attempt_mutation_receipt_id="mutation-at-failure",
        last_project_mutation_receipt_id="repair-after-failure",
    )

    rendered = _render_workflow_state(workflow)
    assert "gate_failure_snapshot_stale: true" in rendered

    request = MagicMock()
    request.runtime = _runtime()
    request.state = {"skill_workflow": workflow}
    request.messages = []
    request.override.side_effect = lambda **kwargs: kwargs

    augmented = middleware._augment_request(request)
    reminders = {message.name: message.content for message in augmented["messages"]}
    assert "older project version" in reminders["skill_failed_gate"]
    assert "Rerun the exact resume_pointer now" in reminders["skill_failed_gate"]


def test_ppt_master_final_svg_gate_allows_consolidated_multi_file_repair():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py {project} --stage final --json"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        gate_detail="complete final SVG issue batch",
        gate_owner_receipt_id="failed-final-check",
        gate_owner_tool="bash",
        gate_owner_action=command,
        gate_owner_stage="svg_quality_checker.py:final",
        resume_pointer=command,
        failed_gate_attempt_mutation_receipt_id="mutation-at-failure",
        last_project_mutation_receipt_id="first-file-repair",
        loaded_authority_run_id="run-1",
        loaded_authority_documents=[
            "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
            "/mnt/skills/public/ppt-master/references/executor-base.md",
            "/mnt/skills/public/ppt-master/references/shared-standards-core.md",
            "/mnt/skills/public/ppt-master/references/svg-effects.md",
            "/mnt/skills/public/ppt-master/references/native-shape-authoring.md",
            "/mnt/skills/public/ppt-master/references/semantic-svg.md",
        ],
    )
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "read-next-svg",
                        "name": "read_file",
                        "args": {"path": f"{project}/svg_output/04_platform.svg"},
                    },
                    {
                        "id": "repair-next-svg",
                        "name": "str_replace",
                        "args": {
                            "path": f"{project}/svg_output/05_ending.svg",
                            "old": "bad",
                            "new": "good",
                        },
                    },
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    assert middleware.after_model(state, _runtime()) is None


def test_ppt_master_consolidated_repair_cannot_reread_stale_validation_report():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py {project} --stage final --json"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        gate_detail="complete final SVG issue batch",
        gate_owner_receipt_id="failed-final-check",
        gate_owner_tool="bash",
        gate_owner_action=command,
        gate_owner_stage="svg_quality_checker.py:final",
        resume_pointer=command,
        failed_gate_attempt_mutation_receipt_id="mutation-at-failure",
        last_project_mutation_receipt_id="first-file-repair",
    )
    blocked = middleware.after_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "stale-report",
                            "name": "read_file",
                            "args": {"path": f"{project}/validation/svg_quality_report.json"},
                        }
                    ],
                )
            ],
            "skill_workflow": workflow,
        },
        _runtime(),
    )

    assert blocked is not None
    replacement = blocked["messages"][0]
    assert replacement.tool_calls[0]["name"] == "bash"
    assert replacement.tool_calls[0]["args"]["command"] == command


def test_stale_failed_gate_replays_checkpointed_read_only_validator():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = (
        "export SKILL_DIR='/mnt/skills/public/ppt-master' && "
        f"python3 ${{SKILL_DIR}}/scripts/project_manager.py validate {project}"
    )
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        gate_detail="old validation report",
        gate_owner_tool="bash",
        gate_owner_action=command,
        gate_owner_receipt_id="failed-validation",
        resume_pointer=command,
        failed_gate_attempt_mutation_receipt_id="mutation-at-failure",
        last_project_mutation_receipt_id="repair-after-failure",
    )
    state = {
        "messages": [
            AIMessage(
                id="wrong-next-action",
                content="",
                tool_calls=[
                    {
                        "id": "another-write",
                        "name": "write_file",
                        "args": {"path": f"{project}/spec_lock.md", "content": "stale repair"},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    replay = middleware.after_model(state, _runtime())

    assert replay is not None
    assert "jump_to" not in replay
    replacement = replay["messages"][0]
    assert isinstance(replacement, AIMessage)
    assert replacement.tool_calls == [
        {
            "id": replacement.tool_calls[0]["id"],
            "name": "bash",
            "args": {"command": command},
        }
    ]
    assert replacement.tool_calls[0]["id"].startswith("skill-owner-validation-replay-")
    assert replay["skill_workflow"]["gate_status"] == "failed"
    assert replay["skill_workflow"]["resume_pointer"] == command


def test_stale_failed_gate_does_not_replay_side_effecting_owner_action():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/image_gen.py --project {project} --prompt demo"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        gate_detail="provider request failed",
        gate_owner_tool="bash",
        gate_owner_action=command,
        gate_owner_receipt_id="failed-image-generation",
        resume_pointer=command,
        failed_gate_attempt_mutation_receipt_id="mutation-at-failure",
        last_project_mutation_receipt_id="repair-after-failure",
    )
    state = {
        "messages": [
            AIMessage(
                id="unsafe-owner-diversion",
                content="",
                tool_calls=[
                    {
                        "id": "another-write",
                        "name": "write_file",
                        "args": {"path": f"{project}/design_spec.md", "content": "repair"},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    blocked = middleware.after_model(state, _runtime())

    assert blocked is not None
    assert blocked["jump_to"] == "model"
    assert "generated reports are stale" in blocked["skill_workflow"]["guard_detail"]


def test_failed_delivery_gate_allows_project_repair_and_delivery_retry():
    middleware = SkillWorkflowMiddleware()
    failed_artifact = "/mnt/user-data/outputs/demo.pptx"
    workflow = _workflow(
        status="blocked",
        gate_status="failed",
        resume_pointer=failed_artifact,
        project_path="/mnt/user-data/workspace/projects/demo",
        evidence=[
            {
                "id": "failed-delivery",
                "tool": "present_files",
                "status": "failed",
                "action": failed_artifact,
                "detail": "missing narration audio",
            }
        ],
    )
    repair_state = {
        "messages": [
            AIMessage(
                id="repair",
                content="",
                tool_calls=[
                    {
                        "id": "repair-call",
                        "name": "bash",
                        "args": {"command": ("python3 /mnt/skills/public/ppt-master/scripts/notes_to_audio.py /mnt/user-data/workspace/projects/demo")},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }
    delivery_state = {
        "messages": [
            AIMessage(
                id="delivery",
                content="",
                tool_calls=[
                    {
                        "id": "delivery-call",
                        "name": "present_files",
                        "args": {"filepaths": ["/mnt/user-data/outputs/demo-repaired.pptx"]},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    assert middleware.after_model(repair_state, _runtime()) is None
    assert middleware.after_model(delivery_state, _runtime()) is None


def test_failed_delivery_gate_still_blocks_unrelated_bash():
    middleware = SkillWorkflowMiddleware()
    failed_artifact = "/mnt/user-data/outputs/demo.pptx"
    state = {
        "messages": [
            AIMessage(
                id="unrelated",
                content="",
                tool_calls=[
                    {
                        "id": "unrelated-call",
                        "name": "bash",
                        "args": {"command": "python3 /tmp/unrelated.py /tmp/other-project"},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            status="blocked",
            gate_status="failed",
            resume_pointer=failed_artifact,
            project_path="/mnt/user-data/workspace/projects/demo",
            evidence=[
                {
                    "id": "failed-delivery",
                    "tool": "present_files",
                    "status": "failed",
                    "action": failed_artifact,
                    "detail": "missing narration audio",
                }
            ],
        ),
    }

    blocked = middleware.after_model(state, _runtime())

    assert blocked is not None
    assert blocked["jump_to"] == "model"


def test_successful_delivery_retry_clears_failed_delivery_gate():
    middleware = SkillWorkflowMiddleware()
    failed_artifact = "/mnt/user-data/outputs/demo.pptx"
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "repaired-delivery",
                    "name": "present_files",
                    "args": {"filepaths": ["/mnt/user-data/outputs/demo-repaired.pptx"]},
                }
            ],
        ),
        ToolMessage(content="Published", tool_call_id="repaired-delivery"),
    ]

    update = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(
                status="blocked",
                gate_status="failed",
                resume_pointer=failed_artifact,
                project_path="/mnt/user-data/workspace/projects/demo",
                evidence=[
                    {
                        "id": "failed-delivery",
                        "tool": "present_files",
                        "status": "failed",
                        "action": failed_artifact,
                        "detail": "missing narration audio",
                    }
                ],
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["status"] == "completed"
    assert update["gate_status"] == "passed"
    assert update["resume_pointer"] == ""
    assert update["gate_owner_receipt_id"] == ""
    assert update["gate_owner_tool"] == ""
    assert update["gate_owner_action"] == ""
    assert update["gate_owner_stage"] == ""


def test_chat_confirmation_cannot_supersede_an_unresolved_failed_owner():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py {project} --stage final --json"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        gate_detail="quality failed",
        resume_pointer=command,
        evidence=[
            {
                "id": "quality",
                "tool": "bash",
                "status": "failed",
                "action": command,
                "detail": "quality failed",
            }
        ],
    )

    update = middleware.before_model(
        {
            "messages": [HumanMessage(content="confirm this template")],
            "skill_workflow": workflow,
        },
        _runtime(),
    )["skill_workflow"]

    assert update["gate_status"] == "failed"
    assert update["gate_detail"] == "quality failed"
    assert update["resume_pointer"] == command
    assert "template_selection_chat_confirmed" not in update


def test_explicit_chat_confirmation_clears_managed_gate_after_page_timeout():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(content="Please confirm the selected Deck template."),
                HumanMessage(content="confirm this template"),
            ],
            "skill_workflow": _workflow(
                project_path=project,
                managed_artifacts=["confirm_ui/template_selection.json"],
                status="blocked",
                gate_status="managed-artifact-owner-required",
                gate_detail="managed confirmation receipt required",
                resume_pointer=f"{project}/confirm_ui/template_selection.json",
                confirmation_surface="page",
                confirmation_surface_switch_allowed=True,
                confirmation_wait_timed_out=True,
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["template_selection_confirmed"] is True
    assert update["template_selection_chat_confirmed"] is True
    assert update["confirmation_surface"] == "chat"
    assert update["confirmation_surface_switch_allowed"] is False
    assert update["confirmation_wait_timed_out"] is False
    assert update["status"] == "active"
    assert update["gate_status"] == "passed"
    assert update["resume_pointer"] == ""


def test_chat_confirmation_recovers_timeout_permission_from_waiting_receipt():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    wait_command = f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --wait-only --wait-stage template"
    daemon_command = f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --daemon --no-browser"
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(content="Please confirm the selected Deck template."),
                HumanMessage(content="confirm this template"),
            ],
            "skill_workflow": _workflow(
                project_path=project,
                managed_artifacts=["confirm_ui/template_selection.json"],
                status="blocked",
                gate_status="managed-artifact-owner-required",
                resume_pointer=f"{project}/confirm_ui/template_selection.json",
                confirmation_surface="page",
                confirmation_surface_switch_allowed=False,
                confirmation_wait_timed_out=False,
                evidence=[
                    {"id": "wait-timeout", "tool": "bash", "status": "waiting", "action": wait_command},
                    {"id": "daemon-restart", "tool": "bash", "status": "passed", "action": daemon_command},
                ],
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["template_selection_chat_confirmed"] is True
    assert update["confirmation_surface"] == "chat"
    assert update["confirmation_wait_timed_out"] is False
    assert update["gate_status"] == "passed"


def test_confirm_ui_daemon_restart_preserves_timeout_fallback_permission():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    daemon_command = f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --daemon --no-browser"
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(content="", tool_calls=[{"id": "daemon", "name": "bash", "args": {"command": daemon_command}}]),
                ToolMessage(content="started", tool_call_id="daemon"),
            ],
            "skill_workflow": _workflow(
                project_path=project,
                status="waiting",
                gate_status="waiting-for-user",
                confirmation_surface="page",
                confirmation_surface_switch_allowed=True,
                confirmation_wait_timed_out=True,
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["confirmation_surface_switch_allowed"] is True
    assert update["confirmation_wait_timed_out"] is True


def test_clarification_wait_during_failed_gate_preserves_owner_transaction():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py {project} --stage final --json"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        gate_detail="quality failed",
        resume_pointer=command,
        confirmation_surface_switch_allowed=True,
        evidence=[
            {
                "id": "quality",
                "tool": "bash",
                "status": "failed",
                "action": command,
                "detail": "quality failed",
            }
        ],
    )
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "clarify",
                    "name": "ask_clarification",
                    "args": {"question": "Please confirm the repair choice"},
                }
            ],
        ),
        ToolMessage(content="Waiting for user response", tool_call_id="clarify"),
    ]

    update = middleware.before_model(
        {"messages": messages, "skill_workflow": workflow},
        _runtime(),
    )["skill_workflow"]

    assert update["status"] == "waiting"
    assert update["gate_status"] == "failed"
    assert update["gate_detail"] == "quality failed"
    assert update["resume_pointer"] == command
    assert update["gate_owner_action"] == command


def test_missing_top_level_route_runs_integrity_gate_before_route_document_read():
    middleware = SkillWorkflowMiddleware()
    workflow = _workflow(selected_authority="")
    build_state = {
        "messages": [
            AIMessage(
                id="build-before-route",
                content="",
                tool_calls=[
                    {
                        "id": "build",
                        "name": "bash",
                        "args": {"command": "python3 build.py /mnt/user-data/workspace/projects/demo"},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }
    route_state = {
        "messages": [
            AIMessage(
                id="read-route",
                content="",
                tool_calls=[
                    {
                        "id": "routing",
                        "name": "read_file",
                        "args": {"path": "/mnt/skills/public/ppt-master/workflows/routing.md"},
                    },
                    {
                        "id": "generate-route",
                        "name": "read_file",
                        "args": {"path": "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"},
                    },
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    blocked = middleware.after_model(build_state, _runtime())

    assert blocked is not None
    assert blocked["skill_workflow"]["gate_status"] == "route-selection-required"
    assert blocked["messages"][0].tool_calls[0]["name"] == "bash"
    assert "attribution_guard.py" in blocked["messages"][0].tool_calls[0]["args"]["command"]

    blocked_route = middleware.after_model(route_state, _runtime())
    assert blocked_route is not None
    assert blocked_route["messages"][0].tool_calls[0]["name"] == "bash"


def test_route_selection_serializes_integrity_routing_and_selected_authority():
    middleware = SkillWorkflowMiddleware()
    skill_root = "/mnt/skills/public/ppt-master"
    integrity_action = f"cd {skill_root} && python3 scripts/attribution_guard.py"
    integrity_receipt = {
        "id": "integrity",
        "tool": "bash",
        "status": "passed",
        "action": integrity_action,
        "detail": "Attribution check passed",
    }

    routing_batch = {
        "messages": [
            AIMessage(
                id="routing-with-integrity",
                content="",
                tool_calls=[
                    {"id": "integrity-again", "name": "bash", "args": {"command": integrity_action}},
                    {"id": "routing", "name": "read_file", "args": {"path": f"{skill_root}/workflows/routing.md"}},
                ],
            )
        ],
        "skill_workflow": _workflow(selected_authority="", evidence=[integrity_receipt]),
    }
    routing_update = middleware.after_model(routing_batch, _runtime())
    assert routing_update is not None
    assert [call["id"] for call in routing_update["messages"][0].tool_calls] == ["routing"]

    selected_batch = {
        "messages": [
            AIMessage(
                id="selected-with-integrity",
                content="",
                tool_calls=[
                    {"id": "integrity-third", "name": "bash", "args": {"command": integrity_action}},
                    {"id": "generate", "name": "read_file", "args": {"path": f"{skill_root}/workflows/generate-pptx.md"}},
                ],
            )
        ],
        "skill_workflow": _workflow(
            selected_authority="",
            evidence=[integrity_receipt],
            loaded_authority_run_id="run-1",
            loaded_authority_documents=[f"{skill_root}/workflows/routing.md"],
        ),
    }
    selected_update = middleware.after_model(selected_batch, _runtime())
    assert selected_update is not None
    assert [call["id"] for call in selected_update["messages"][0].tool_calls] == ["generate"]


def test_route_selection_strips_parallel_read_only_context_from_selected_route():
    middleware = SkillWorkflowMiddleware()
    workflow = _workflow(
        selected_authority="",
        evidence=[
            {
                "id": "integrity",
                "tool": "bash",
                "status": "passed",
                "action": "cd /mnt/skills/public/ppt-master && python3 scripts/attribution_guard.py",
                "detail": "Attribution check passed",
            }
        ],
        loaded_authority_run_id="run-1",
        loaded_authority_documents=["/mnt/skills/public/ppt-master/workflows/routing.md"],
    )
    state = {
        "messages": [
            AIMessage(
                id="route-with-context",
                content="",
                tool_calls=[
                    {
                        "id": "generate-route",
                        "name": "read_file",
                        "args": {"path": "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"},
                    },
                    {
                        "id": "deck-index",
                        "name": "read_file",
                        "args": {"path": "/mnt/skills/public/ppt-master/templates/decks/decks_index.json"},
                    },
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    update = middleware.after_model(state, _runtime())
    assert update is not None
    assert [call["id"] for call in update["messages"][0].tool_calls] == ["generate-route"]


def test_selected_top_level_route_rejects_competing_route_read():
    middleware = SkillWorkflowMiddleware()
    selected_route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    state = {
        "messages": [
            AIMessage(
                id="competing-route",
                content="",
                tool_calls=[
                    {
                        "id": "create-template-route",
                        "name": "read_file",
                        "args": {
                            "path": "/mnt/skills/public/ppt-master/workflows/create-template.md",
                        },
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(selected_authority=selected_route),
    }

    blocked = middleware.after_model(state, _runtime())

    assert blocked is not None
    assert blocked["jump_to"] == "model"
    assert blocked["skill_workflow"]["gate_status"] == "top-level-route-locked"
    assert blocked["skill_workflow"]["resume_pointer"] == selected_route


def test_route_selection_rejects_read_only_context_without_route_document():
    middleware = SkillWorkflowMiddleware()
    state = {
        "messages": [
            AIMessage(
                id="context-without-route",
                content="",
                tool_calls=[
                    {
                        "id": "deck-index",
                        "name": "read_file",
                        "args": {"path": "/mnt/skills/public/ppt-master/templates/decks/decks_index.json"},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            selected_authority="",
            evidence=[
                {
                    "id": "integrity",
                    "tool": "bash",
                    "status": "passed",
                    "action": "cd /mnt/skills/public/ppt-master && python3 scripts/attribution_guard.py",
                    "detail": "Attribution check passed",
                }
            ],
            loaded_authority_run_id="run-1",
            loaded_authority_documents=["/mnt/skills/public/ppt-master/workflows/routing.md"],
        ),
    }

    blocked = middleware.after_model(state, _runtime())

    assert blocked is not None
    assert blocked["skill_workflow"]["gate_status"] == "route-selection-required"


def test_default_generate_rejects_implicit_quick_generate_mutation():
    middleware = SkillWorkflowMiddleware()
    state = {
        "messages": [
            AIMessage(
                id="implicit-quick",
                content="",
                tool_calls=[
                    {
                        "id": "quick-init",
                        "name": "bash",
                        "args": {"command": ("python3 /mnt/skills/public/ppt-master/scripts/project_manager.py init demo --format ppt169 --quick-generate")},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            task_text="创建 5 页高质量中国海油智能化能力展示PPT",
        ),
    }

    blocked = middleware.after_model(state, _runtime())

    assert blocked is not None
    assert blocked["jump_to"] == "model"
    assert blocked["skill_workflow"]["gate_status"] == "profile-selection-required"


def test_reading_quick_profile_does_not_select_it_without_explicit_user_intent():
    middleware = SkillWorkflowMiddleware()
    quick = "/mnt/skills/public/ppt-master/workflows/profiles/quick-generate.md"
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "quick-profile", "name": "read_file", "args": {"path": quick}}],
        ),
        ToolMessage(content="# Quick Generate Profile", tool_call_id="quick-profile"),
    ]

    workflow = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(
                task_text="创建 5 页高质量中国海油智能化能力展示PPT",
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert "selected_profile" not in workflow
    assert quick not in workflow.get("resume_documents", [])


def test_explicit_quick_generate_selects_profile_and_allows_quick_flag():
    middleware = SkillWorkflowMiddleware()
    quick = "/mnt/skills/public/ppt-master/workflows/profiles/quick-generate.md"
    workflow = middleware.before_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "quick-profile", "name": "read_file", "args": {"path": quick}}],
                ),
                ToolMessage(content="# Quick Generate Profile", tool_call_id="quick-profile"),
            ],
            "skill_workflow": _workflow(task_text="请快速生成并跳过策略确认"),
        },
        _runtime(),
    )["skill_workflow"]
    state = {
        "messages": [
            AIMessage(
                id="explicit-quick",
                content="",
                tool_calls=[
                    {
                        "id": "quick-init",
                        "name": "bash",
                        "args": {"command": ("python3 /mnt/skills/public/ppt-master/scripts/project_manager.py init demo --format ppt169 --quick-generate")},
                    }
                ],
            )
        ],
        "skill_workflow": {**_workflow(task_text="请快速生成并跳过策略确认"), **workflow},
    }

    assert workflow["selected_profile"] == quick
    assert middleware.after_model(state, _runtime()) is None


def test_initial_template_request_is_not_itself_chat_confirmation():
    middleware = SkillWorkflowMiddleware()
    update = middleware.before_model(
        {
            "messages": [HumanMessage(content="请使用中国海油模板创建5页PPT")],
            "skill_workflow": _workflow(
                managed_artifacts=["confirm_ui/template_selection.json"],
            ),
        },
        _runtime(),
    )

    assert update is None


def test_explicit_chat_reply_checkpoints_template_confirmation():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(content="## 模板选择确认\n是否确认使用此模板？请回复‘确认’或‘继续’。"),
                HumanMessage(content="确认使用此模板"),
            ],
            "skill_workflow": _workflow(
                project_path=project,
                managed_artifacts=["confirm_ui/template_selection.json"],
                status="waiting",
                gate_status="waiting-for-user",
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["template_selection_chat_confirmed"] is True
    assert update["confirmation_surface"] == "chat"
    assert update["status"] == "active"
    assert update["gate_status"] == "passed"

    install_state = {
        "messages": [
            AIMessage(
                id="install-after-chat-confirm",
                content="",
                tool_calls=[
                    {
                        "id": "install",
                        "name": "bash",
                        "args": {"command": (f"cp -r /mnt/skills/public/ppt-master/templates/decks/中国海油/templates {project}/templates")},
                    }
                ],
            )
        ],
        "skill_workflow": {
            **_workflow(project_path=project),
            **update,
            "loaded_authority_run_id": "run-1",
            "loaded_authority_documents": [
                "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
                "/mnt/skills/public/ppt-master/scripts/docs/confirm_ui.md",
            ],
        },
    }
    assert middleware.after_model(install_state, _runtime()) is None


def test_chat_template_confirmation_survives_transparent_host_blocker_message():
    middleware = SkillWorkflowMiddleware()
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(content="## 模板选择确认\n请选择‘中国海油’ Deck 并回复‘继续’。"),
                AIMessage(content="Skill 工作流已停在官方确认边界，等待恢复。"),
                HumanMessage(content="继续"),
            ],
            "skill_workflow": _workflow(
                managed_artifacts=["confirm_ui/template_selection.json"],
                confirmation_wait_timed_out=True,
                status="waiting",
                gate_status="waiting-for-user",
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["template_selection_confirmed"] is True
    assert update["template_selection_chat_confirmed"] is True
    assert update["confirmation_surface"] == "chat"
    assert update["confirmation_wait_timed_out"] is False


def test_route_selection_precedes_stale_stage_reload_without_guard_oscillation():
    middleware = SkillWorkflowMiddleware()
    workflow = _workflow(
        selected_authority="",
        evidence=[
            {
                "id": "integrity",
                "tool": "bash",
                "status": "passed",
                "action": "cd /mnt/skills/public/ppt-master && python3 scripts/attribution_guard.py",
                "detail": "Attribution check passed",
            }
        ],
        resume_documents=[
            "/mnt/skills/public/ppt-master/workflows/stages/customize-animations.md",
            "/mnt/skills/public/ppt-master/workflows/stages/generate-audio.md",
        ],
        loaded_authority_run_id="previous-run",
        loaded_authority_documents=[
            "/mnt/skills/public/ppt-master/workflows/stages/customize-animations.md",
            "/mnt/skills/public/ppt-master/workflows/stages/generate-audio.md",
        ],
    )
    state = {
        "messages": [
            AIMessage(
                id="routing-first",
                content="",
                tool_calls=[
                    {
                        "id": "routing",
                        "name": "read_file",
                        "args": {"path": "/mnt/skills/public/ppt-master/workflows/routing.md"},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    routing_update = middleware.after_model(state, _runtime("new-run"))
    assert routing_update is not None
    assert routing_update["messages"][0].tool_calls[0]["args"]["path"].endswith("/routing.md")

    request = MagicMock()
    request.runtime = _runtime("new-run")
    request.state = {"skill_workflow": workflow}
    request.messages = []
    request.override.side_effect = lambda **kwargs: kwargs
    augmented = middleware._augment_request(request)
    reminder_names = [message.name for message in augmented["messages"]]

    assert reminder_names == ["skill_route_selection"]


def test_sequence_guard_reports_blocker_after_three_no_progress_attempts():
    middleware = SkillWorkflowMiddleware()
    state = {
        "messages": [AIMessage(id="no-route", content="I will build it now.")],
        "skill_workflow": _workflow(
            selected_authority="",
            evidence=[
                {
                    "id": "integrity",
                    "tool": "bash",
                    "status": "passed",
                    "action": "cd /mnt/skills/public/ppt-master && python3 scripts/attribution_guard.py",
                    "detail": "Attribution check passed",
                }
            ],
            loaded_authority_run_id="run-no-progress",
            loaded_authority_documents=["/mnt/skills/public/ppt-master/workflows/routing.md"],
        ),
    }
    runtime = _runtime("run-no-progress")

    first = middleware.after_model(state, runtime)
    second = middleware.after_model(state, runtime)
    third = middleware.after_model(state, runtime)

    assert first is not None and first["jump_to"] == "model"
    assert second is not None and second["jump_to"] == "model"
    assert third is not None and "jump_to" not in third
    assert "连续 3 次" in third["messages"][0].content


def test_active_page_confirmation_rejects_plain_chat_fallback():
    middleware = SkillWorkflowMiddleware()
    state = {
        "messages": [
            AIMessage(
                id="chat-fallback",
                content="如果以上方案符合您的预期，请回复‘确认’，我将开始生成。",
            )
        ],
        "skill_workflow": _workflow(
            confirmation_surface="page",
            confirmation_surface_switch_allowed=False,
            confirmation_ui_active=True,
        ),
    }

    update = middleware.after_model(state, _runtime())

    assert update is not None
    assert update["jump_to"] == "model"
    assert update["skill_workflow"]["gate_status"] == "confirmation-surface-locked"
    assert update["skill_workflow"]["resume_pointer"] == "continue with the checkpointed page confirmation surface"


def test_active_page_confirmation_blocks_unrequested_owner_shutdown():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    state = {
        "messages": [
            HumanMessage(content="继续"),
            AIMessage(
                id="shutdown-owner",
                content="",
                tool_calls=[
                    {
                        "id": "shutdown",
                        "name": "bash",
                        "args": {
                            "command": (
                                "python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py "
                                f"{project} --shutdown"
                            )
                        },
                    }
                ],
            ),
        ],
        "skill_workflow": _workflow(
            project_path=project,
            confirmation_surface="page",
            confirmation_surface_switch_allowed=False,
            confirmation_ui_active=True,
        ),
    }

    update = middleware.after_model(state, _runtime())

    assert update is not None
    assert update["jump_to"] == "model"
    assert update["skill_workflow"]["gate_status"] == "confirmation-surface-locked"
    assert update["skill_workflow"]["resume_pointer"] == "continue with the checkpointed page confirmation surface"


def test_active_page_confirmation_allows_non_waiting_progress_text():
    middleware = SkillWorkflowMiddleware()
    state = {
        "messages": [AIMessage(id="progress", content="模板安装完成，正在准备下一阶段方案。")],
        "skill_workflow": _workflow(
            confirmation_surface="page",
            confirmation_surface_switch_allowed=False,
            confirmation_ui_active=True,
        ),
    }

    assert middleware.after_model(state, _runtime()) is None


def test_generate_route_reloads_persisted_design_and_structure_contracts_on_resume():
    project = "/mnt/user-data/workspace/projects/demo"
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    workflow = _workflow(
        project_path=project,
        resume_documents=[
            route,
            f"{project}/design_spec.md",
            f"{project}/spec_lock.md",
        ],
    )
    middleware = SkillWorkflowMiddleware()
    request = MagicMock()
    request.runtime = _runtime("new-run")
    request.state = {"skill_workflow": workflow}
    request.messages = []
    request.override.side_effect = lambda **kwargs: kwargs

    augmented = middleware._augment_request(request)
    rendered = "\n".join(message.content for message in augmented["messages"])

    assert "/mnt/user-data/workspace/projects/demo/design_spec.md" in rendered
    assert "/mnt/user-data/workspace/projects/demo/spec_lock.md" in rendered


def test_read_project_contracts_closes_generate_resume_barrier():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    design = f"{project}/design_spec.md"
    spec_lock = f"{project}/spec_lock.md"
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    workflow = _workflow(
        project_path=project,
        resume_documents=[route],
        loaded_authority_run_id="new-run",
        loaded_authority_documents=[route],
    )
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "design", "name": "read_file", "args": {"path": design}},
                {"id": "lock", "name": "read_file", "args": {"path": spec_lock}},
            ],
        ),
        ToolMessage(content="| Speaker Notes | enabled |", tool_call_id="design"),
        ToolMessage(content="## pptx_structure\n- mode: structured", tool_call_id="lock"),
    ]

    update = middleware.before_model(
        {"messages": messages, "skill_workflow": workflow},
        _runtime("new-run"),
    )["skill_workflow"]

    assert update["loaded_authority_run_id"] == "new-run"
    assert design in update["loaded_authority_documents"]
    assert spec_lock in update["loaded_authority_documents"]


def test_successful_semantic_retry_clears_failed_script_gate():
    """Comments and harmless shell formatting must not keep a recovered Skill gate blocked."""
    middleware = SkillWorkflowMiddleware()
    failed = "python3 /mnt/skills/public/ppt-master/scripts/total_md_split.py /mnt/user-data/workspace/projects/demo"
    retry = "export SKILL_DIR=/mnt/skills/public/ppt-master\n# retry after notes are ready\npython3 ${SKILL_DIR}/scripts/total_md_split.py /mnt/user-data/workspace/projects/demo"
    messages = [
        AIMessage(content="", tool_calls=[{"id": "retry", "name": "bash", "args": {"command": retry}}]),
        ToolMessage(content="[Done] Successfully generated 5/5 file(s)", tool_call_id="retry"),
    ]

    update = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(status="blocked", gate_status="failed", resume_pointer=failed),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["status"] == "active"
    assert update["gate_status"] == "passed"
    assert update["resume_pointer"] == ""


def test_successful_same_script_for_different_project_does_not_clear_failed_gate():
    middleware = SkillWorkflowMiddleware()
    failed = "python3 /mnt/skills/public/ppt-master/scripts/total_md_split.py /mnt/user-data/workspace/projects/demo-a"
    wrong_project = "python3 /mnt/skills/public/ppt-master/scripts/total_md_split.py /mnt/user-data/workspace/projects/demo-b"
    messages = [
        AIMessage(content="", tool_calls=[{"id": "retry", "name": "bash", "args": {"command": wrong_project}}]),
        ToolMessage(content="[Done] Successfully generated 5/5 file(s)", tool_call_id="retry"),
    ]

    update = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(status="blocked", gate_status="failed", resume_pointer=failed),
        },
        _runtime(),
    )["skill_workflow"]

    assert update.get("gate_status") != "passed"
    assert update.get("resume_pointer") != ""


def test_managed_receipt_failure_allows_only_owning_confirmation_server():
    middleware = SkillWorkflowMiddleware()
    workflow = _workflow(
        status="blocked",
        gate_status="managed-artifact-owner-required",
        resume_pointer="/mnt/user-data/workspace/projects/demo/confirm_ui/template_selection.json",
    )
    server_state = {
        "messages": [
            AIMessage(
                id="server",
                content="",
                tool_calls=[
                    {
                        "id": "server-call",
                        "name": "bash",
                        "args": {"command": ("python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py /mnt/user-data/workspace/projects/demo --daemon")},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }
    install_state = {
        "messages": [
            AIMessage(
                id="install",
                content="",
                tool_calls=[
                    {
                        "id": "install-call",
                        "name": "bash",
                        "args": {"command": "cp -r template /mnt/user-data/workspace/projects/demo/templates"},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    assert middleware.after_model(server_state, _runtime()) is None
    assert middleware.after_model(install_state, _runtime()) is not None


def test_successful_confirmation_wait_clears_managed_receipt_failure():
    middleware = SkillWorkflowMiddleware()
    command = "python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py /mnt/user-data/workspace/projects/demo --wait-only --wait-stage template"
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "wait", "name": "bash", "args": {"command": command}}],
        ),
        ToolMessage(content="Template confirmed", tool_call_id="wait"),
    ]

    update = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(
                status="blocked",
                gate_status="managed-artifact-owner-required",
                resume_pointer="/mnt/user-data/workspace/projects/demo/confirm_ui/template_selection.json",
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["status"] == "active"
    assert update["gate_status"] == "passed"
    assert update["resume_pointer"] == ""


def test_managed_receipt_gate_is_sticky_across_owner_server_setup_failure():
    middleware = SkillWorkflowMiddleware()
    receipt = "/mnt/user-data/workspace/projects/demo/confirm_ui/template_selection.json"
    command = "python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py /mnt/user-data/workspace/projects/demo --daemon"
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "server", "name": "bash", "args": {"command": command}}],
        ),
        ToolMessage(
            content="Error: template_options.json not found",
            tool_call_id="server",
            status="error",
        ),
    ]

    update = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(
                status="blocked",
                gate_status="managed-artifact-owner-required",
                resume_pointer=receipt,
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["status"] == "blocked"
    assert update["gate_status"] == "managed-artifact-owner-required"
    assert update["resume_pointer"] == receipt


def test_managed_receipt_gate_transfers_to_failed_owner_wait_and_allows_project_repair():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    receipt = f"{project}/confirm_ui/result.json"
    command = (
        "python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py "
        f"{project} --wait-only --wait-stage stage1 --timeout 600"
    )
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "owner-wait", "name": "bash", "args": {"command": command}}],
                ),
                ToolMessage(
                    content=(
                        "Error: [PPT_TEMPLATE_CONTRACT] the confirmed template workspace has not been "
                        "atomically installed into project-local templates/images/icons"
                    ),
                    tool_call_id="owner-wait",
                    status="error",
                ),
            ],
            "skill_workflow": _workflow(
                project_path=project,
                status="blocked",
                gate_status="managed-artifact-owner-required",
                resume_pointer=receipt,
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["status"] == "blocked"
    assert update["gate_status"] == "failed"
    assert update["gate_owner_tool"] == "bash"
    assert update["gate_owner_action"] == command
    assert update["resume_pointer"] == command
    assert update.get("confirmation_surface_switch_allowed", False) is False

    repair_call = {
        "id": "install-workspace",
        "name": "bash",
        "args": {
            "command": (
                "cp -R /mnt/skills/public/ppt-master/templates/decks/demo/templates/. "
                f"{project}/templates/"
            )
        },
    }

    assert _failed_gate_call_allowed(
        repair_call,
        {**_workflow(project_path=project), **update},
    ) is True


def test_non_timeout_confirmation_failure_does_not_enable_chat_fallback():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = (
        "python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py "
        f"{project} --wait-only --wait-stage stage1 --timeout 600"
    )
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "owner-wait", "name": "bash", "args": {"command": command}}],
                ),
                ToolMessage(
                    content="Error: [PPT_TEMPLATE_CONTRACT] images/image9.png is missing",
                    tool_call_id="owner-wait",
                    status="error",
                ),
            ],
            "skill_workflow": _workflow(
                project_path=project,
                confirmation_surface="page",
                confirmation_ui_active=True,
                confirmation_surface_switch_allowed=False,
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["gate_status"] == "failed"
    assert update.get("confirmation_surface_switch_allowed", False) is False
    assert update.get("confirmation_wait_timed_out", False) is False


def test_successful_template_copy_is_a_durable_project_mutation_receipt():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    owner = (
        "python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py "
        f"{project} --wait-only --wait-stage stage1 --timeout 600"
    )
    copy_command = (
        "cp /mnt/skills/public/ppt-master/templates/decks/demo/images/image9.png "
        f"{project}/images/"
    )
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "copy-image", "name": "bash", "args": {"command": copy_command}}],
                ),
                ToolMessage(content="copied", tool_call_id="copy-image"),
            ],
            "skill_workflow": _workflow(
                project_path=project,
                status="blocked",
                gate_status="failed",
                gate_owner_tool="bash",
                gate_owner_action=owner,
                gate_owner_stage="confirm_ui:stage1",
                resume_pointer=owner,
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["last_project_mutation_receipt_id"] == "copy-image"
    assert update.get("gate_status") != "passed"


def test_successful_confirmation_daemon_does_not_clear_managed_receipt_gate():
    middleware = SkillWorkflowMiddleware()
    receipt = "/mnt/user-data/workspace/projects/demo/confirm_ui/template_selection.json"
    command = "python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py /mnt/user-data/workspace/projects/demo --daemon"
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "server", "name": "bash", "args": {"command": command}}],
        ),
        ToolMessage(content="started confirm UI in background", tool_call_id="server"),
    ]

    update = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(
                status="blocked",
                gate_status="managed-artifact-owner-required",
                resume_pointer=receipt,
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert "status" not in update
    assert "gate_status" not in update
    assert "resume_pointer" not in update
    assert update["confirmation_surface"] == "page"


def test_ordinary_tool_failure_does_not_freeze_skill_workflow():
    middleware = SkillWorkflowMiddleware()
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "bad-todos",
                    "name": "todos",
                    "args": {"todos": "[]"},
                }
            ],
        ),
        ToolMessage(
            content="Error: todos is not a valid tool, try write_todos",
            tool_call_id="bad-todos",
            status="error",
        ),
    ]

    update = middleware.before_model(
        {"messages": messages, "skill_workflow": _workflow()},
        _runtime(),
    )["skill_workflow"]

    assert "status" not in update
    assert "gate_status" not in update
    assert "resume_pointer" not in update
    assert update["evidence"][0]["status"] == "failed"


def test_stale_ordinary_tool_gate_is_released_instead_of_looping():
    middleware = SkillWorkflowMiddleware()
    failed_action = "{'todos': '[]'}"
    workflow = _workflow(
        status="blocked",
        gate_status="failed",
        resume_pointer=failed_action,
        evidence=[
            {
                "id": "bad-todos",
                "tool": "todos",
                "status": "failed",
                "action": failed_action,
            }
        ],
    )
    state = {
        "messages": [
            AIMessage(
                id="project-init",
                content="",
                tool_calls=[
                    {
                        "id": "init",
                        "name": "bash",
                        "args": {"command": ("python3 /mnt/skills/public/ppt-master/scripts/project_manager.py init demo --format ppt169")},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    assert middleware.after_model(state, _runtime()) is None


def test_default_generate_blocks_template_install_without_confirm_receipt():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"PROJECT_PATH='{project}'\nDECK_ROOT='/mnt/skills/public/ppt-master/templates/decks/中国海油'\ncp \"${{DECK_ROOT}}/templates\"/*.svg \"${{PROJECT_PATH}}/templates/\""
    state = {
        "messages": [
            AIMessage(
                id="premature-install",
                content="",
                tool_calls=[{"id": "install", "name": "bash", "args": {"command": command}}],
            )
        ],
        "skill_workflow": _workflow(
            project_path=project,
            selected_authority="/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
            managed_artifacts=["confirm_ui/template_selection.json"],
            loaded_authority_run_id="run-1",
            loaded_authority_documents=[
                "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
                "/mnt/skills/public/ppt-master/scripts/docs/confirm_ui.md",
                f"{project}/design_spec.md",
                f"{project}/spec_lock.md",
            ],
        ),
    }

    blocked = middleware.after_model(state, _runtime())

    assert blocked is not None
    assert blocked["skill_workflow"]["status"] == "blocked"
    assert blocked["skill_workflow"]["gate_status"] == "managed-artifact-owner-required"
    assert blocked["skill_workflow"]["resume_pointer"] == (f"{project}/confirm_ui/template_selection.json")


def test_template_validation_is_allowed_before_default_generate_confirmation():
    middleware = SkillWorkflowMiddleware()
    state = {
        "messages": [
            AIMessage(
                id="validate-template",
                content="",
                tool_calls=[
                    {
                        "id": "validate",
                        "name": "bash",
                        "args": {"command": ("python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py '/mnt/skills/public/ppt-master/templates/decks/中国海油/templates' --template-mode")},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            project_path="/mnt/user-data/workspace/projects/demo",
            selected_authority="/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
            managed_artifacts=["confirm_ui/template_selection.json"],
            loaded_authority_run_id="run-1",
            loaded_authority_documents=[
                "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
                "/mnt/skills/public/ppt-master/scripts/docs/confirm_ui.md",
                "/mnt/user-data/workspace/projects/demo/design_spec.md",
                "/mnt/user-data/workspace/projects/demo/spec_lock.md",
            ],
        ),
    }

    assert middleware.after_model(state, _runtime()) is None


def test_quick_generate_exact_root_branch_does_not_require_confirm_receipt():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    state = {
        "messages": [
            AIMessage(
                id="quick-install",
                content="",
                tool_calls=[
                    {
                        "id": "install",
                        "name": "bash",
                        "args": {"command": (f"cp /mnt/skills/public/ppt-master/templates/decks/中国海油/templates/*.svg {project}/templates/")},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            project_path=project,
            selected_authority="/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
            selected_profile="/mnt/skills/public/ppt-master/workflows/profiles/quick-generate.md",
            managed_artifacts=["confirm_ui/template_selection.json"],
            loaded_authority_run_id="run-1",
            loaded_authority_documents=[
                "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
                "/mnt/skills/public/ppt-master/workflows/profiles/quick-generate.md",
            ],
        ),
    }

    assert middleware.after_model(state, _runtime()) is None


def test_confirm_wait_receipt_allows_default_generate_template_install():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    wait_command = f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --wait-only --wait-stage template"
    state = {
        "messages": [
            AIMessage(
                id="install-after-confirm",
                content="",
                tool_calls=[
                    {
                        "id": "install",
                        "name": "bash",
                        "args": {"command": (f"cp /mnt/skills/public/ppt-master/templates/decks/中国海油/templates/*.svg {project}/templates/")},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            project_path=project,
            selected_authority="/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
            managed_artifacts=["confirm_ui/template_selection.json"],
            loaded_authority_run_id="run-1",
            loaded_authority_documents=[
                "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
                "/mnt/skills/public/ppt-master/scripts/docs/confirm_ui.md",
                f"{project}/design_spec.md",
                f"{project}/spec_lock.md",
            ],
            evidence=[
                {
                    "id": "wait",
                    "tool": "bash",
                    "status": "passed",
                    "action": wait_command,
                }
            ],
        ),
    }

    assert middleware.after_model(state, _runtime()) is None


def test_successful_page_template_confirmation_persists_durable_milestone():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    wait_command = f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --wait-only --wait-stage template"
    update = middleware.before_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "wait-template",
                            "name": "bash",
                            "args": {"command": wait_command},
                        }
                    ],
                ),
                ToolMessage(
                    content="Template selection confirmed",
                    tool_call_id="wait-template",
                ),
            ],
            "skill_workflow": _workflow(project_path=project),
        },
        _runtime(),
    )["skill_workflow"]

    assert update["template_selection_confirmed"] is True
    assert update["confirmation_surface"] == "page"

    install_state = {
        "messages": [
            AIMessage(
                id="install-after-eviction",
                content="",
                tool_calls=[
                    {
                        "id": "install",
                        "name": "bash",
                        "args": {"command": (f"cp -r /mnt/skills/public/ppt-master/templates/decks/cnooc/templates {project}/templates")},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            project_path=project,
            managed_artifacts=["confirm_ui/template_selection.json"],
            template_selection_confirmed=True,
            evidence=[],
            loaded_authority_run_id="run-1",
            loaded_authority_documents=[
                "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
                "/mnt/skills/public/ppt-master/scripts/docs/confirm_ui.md",
            ],
        ),
    }

    assert middleware.after_model(install_state, _runtime()) is None


def test_failed_skill_gate_allows_project_repair_then_requires_owner_retry():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    failed = f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --complete-template-phase"
    workflow = _workflow(
        project_path=project,
        status="blocked",
        gate_status="failed",
        resume_pointer=failed,
        template_selection_confirmed=True,
        evidence=[
            {
                "id": "complete-template",
                "tool": "bash",
                "status": "failed",
                "action": failed,
                "detail": f"missing {project}/templates/design_spec.md",
            }
        ],
    )
    repair_state = {
        "messages": [
            AIMessage(
                id="repair-template",
                content="",
                tool_calls=[
                    {
                        "id": "repair",
                        "name": "bash",
                        "args": {"command": (f"mkdir -p {project}/templates && cp -r /mnt/skills/public/ppt-master/templates/decks/cnooc/templates/. {project}/templates/")},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }

    assert middleware.after_model(repair_state, _runtime()) is None

    repair_update = middleware.before_model(
        {
            "messages": [
                repair_state["messages"][0],
                ToolMessage(content="installed", tool_call_id="repair"),
            ],
            "skill_workflow": workflow,
        },
        _runtime(),
    )["skill_workflow"]
    assert repair_update["gate_status"] == "failed"
    assert repair_update["gate_detail"] == f"missing {project}/templates/design_spec.md"
    assert repair_update["resume_pointer"] == failed
    assert repair_update["gate_owner_action"] == failed

    wrong_owner_operation = {
        "messages": [
            AIMessage(
                id="wrong-owner-operation",
                content="",
                tool_calls=[
                    {
                        "id": "wait-instead",
                        "name": "bash",
                        "args": {"command": (f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --wait-only --wait-stage template")},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }
    blocked = middleware.after_model(wrong_owner_operation, _runtime())
    assert blocked is not None
    assert blocked["skill_workflow"]["resume_pointer"] == failed

    exact_retry = {
        "messages": [
            AIMessage(
                id="retry-complete-template",
                content="",
                tool_calls=[
                    {
                        "id": "retry-complete",
                        "name": "bash",
                        "args": {"command": failed},
                    }
                ],
            )
        ],
        "skill_workflow": workflow,
    }
    assert middleware.after_model(exact_retry, _runtime()) is None


def test_durable_render_includes_resume_and_recent_evidence():
    rendered = _render_workflow_state(
        _workflow(
            status="blocked",
            current_stage="svg_quality_checker.py:final",
            gate_detail="repair the locked project before retrying",
            resume_pointer="rerun final checker",
            evidence=[
                {
                    "id": "quality",
                    "tool": "bash",
                    "status": "failed",
                    "action": "svg_quality_checker.py --stage final",
                }
            ],
        )
    )

    assert "status: blocked" in rendered
    assert "gate_detail: repair the locked project before retrying" in rendered
    assert "resume_pointer: rerun final checker" in rendered
    assert "failed bash" in rendered


def test_tracks_route_and_stage_authority_documents_for_the_current_run():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    confirm = "/mnt/skills/public/ppt-master/scripts/docs/confirm_ui.md"
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "route", "name": "read_file", "args": {"path": route}},
                {"id": "confirm", "name": "read_file", "args": {"path": confirm}},
            ],
        ),
        ToolMessage(
            content=("# Generate PPTX\nUser-owned `confirm_ui/template_selection.json`.\n`confirm_ui/template_handoff.json` must not be written manually.\n`confirm_ui/result.json` is persisted user-confirmed input evidence."),
            tool_call_id="route",
        ),
        ToolMessage(content="# Confirm UI", tool_call_id="confirm"),
    ]

    workflow = middleware.before_model(
        {"messages": messages, "skill_workflow": _workflow()},
        _runtime("run-authority"),
    )["skill_workflow"]

    assert workflow["resume_documents"] == [route, confirm]
    assert workflow["loaded_authority_run_id"] == "run-authority"
    assert workflow["loaded_authority_documents"] == [route, confirm]
    assert workflow["managed_artifacts"] == [
        "confirm_ui/template_selection.json",
        "confirm_ui/template_handoff.json",
        "confirm_ui/result.json",
    ]


def test_new_run_replaces_non_read_work_with_real_authority_reads():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    state = {
        "messages": [
            AIMessage(
                id="attempt",
                content="",
                tool_calls=[{"id": "bash", "name": "bash", "args": {"command": "echo skip"}}],
            )
        ],
        "skill_workflow": _workflow(
            selected_authority=route,
            resume_documents=[route],
            loaded_authority_run_id="previous-run",
            loaded_authority_documents=[route],
        ),
    }

    reload_update = middleware.after_model(state, _runtime("new-run"))

    assert reload_update is not None
    assert "jump_to" not in reload_update
    assert reload_update["skill_workflow"]["gate_status"] == "authority-reload-required"
    replacement = reload_update["messages"][0]
    assert replacement.tool_calls == [
        {
            "id": replacement.tool_calls[0]["id"],
            "name": "read_file",
            "args": {"path": route},
        }
    ]


def test_authority_reload_tool_ids_change_for_a_new_recovery_boundary():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    attempted_action = AIMessage(
        id="attempt",
        content="",
        tool_calls=[{"id": "bash", "name": "bash", "args": {"command": "echo skip"}}],
    )
    initial_reload = middleware.after_model(
        {
            "messages": [attempted_action],
            "skill_workflow": _workflow(
                selected_authority=route,
                resume_documents=[route],
                loaded_authority_run_id="previous-run",
                loaded_authority_documents=[route],
            ),
        },
        _runtime("run-1"),
    )
    failed_gate_reload = middleware.after_model(
        {
            "messages": [attempted_action],
            "skill_workflow": _workflow(
                selected_authority=route,
                resume_documents=[route],
                loaded_authority_run_id="",
                loaded_authority_documents=[],
                status="blocked",
                gate_status="failed",
                gate_detail="project validation failed",
                gate_owner_receipt_id="failed-validator",
                gate_owner_tool="bash",
                gate_owner_action="python3 validator.py /mnt/user-data/workspace/projects/demo",
                gate_owner_stage="validator.py",
                resume_pointer="python3 validator.py /mnt/user-data/workspace/projects/demo",
            ),
        },
        _runtime("run-1"),
    )

    assert initial_reload is not None
    assert failed_gate_reload is not None
    initial_id = initial_reload["messages"][0].tool_calls[0]["id"]
    failed_gate_id = failed_gate_reload["messages"][0].tool_calls[0]["id"]
    assert initial_id != failed_gate_id


def test_new_run_allows_required_authority_reads():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    state = {
        "messages": [
            AIMessage(
                id="reload",
                content="",
                tool_calls=[{"id": "read", "name": "read_file", "args": {"path": route}}],
            )
        ],
        "skill_workflow": _workflow(
            selected_authority=route,
            resume_documents=[route],
            loaded_authority_run_id="previous-run",
            loaded_authority_documents=[route],
        ),
    }

    assert middleware.after_model(state, _runtime("new-run")) is None


def test_failed_gate_authority_reload_preserves_failed_owner_and_pointer():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/svg_quality_checker.py {project} --stage final --json"
    state = {
        "messages": [
            AIMessage(
                id="premature-repair",
                content="",
                tool_calls=[
                    {
                        "id": "write-before-reload",
                        "name": "str_replace",
                        "args": {"path": f"{project}/spec_lock.md", "old": "Pending", "new": "Generated"},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            project_path=project,
            status="blocked",
            gate_status="failed",
            gate_detail="16 blocking errors remain",
            resume_pointer=command,
            resume_documents=[route],
            loaded_authority_run_id="",
            loaded_authority_documents=[],
            evidence=[
                {
                    "id": "failed-checker",
                    "tool": "bash",
                    "status": "failed",
                    "action": command,
                    "detail": "16 blocking errors remain",
                }
            ],
        ),
    }

    reload_update = middleware.after_model(state, _runtime())

    assert reload_update is not None
    assert reload_update["skill_workflow"] == {
        "activation_id": "activation-1",
        "status": "blocked",
        "gate_status": "failed",
        "gate_detail": "16 blocking errors remain",
        "resume_pointer": command,
    }
    replacement = reload_update["messages"][0]
    assert replacement.tool_calls[0]["name"] == "read_file"
    assert replacement.tool_calls[0]["args"] == {"path": route}


def test_new_run_does_not_relabel_historical_reads_as_current_run_receipts():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    old_ai = AIMessage(
        id="old-ai",
        content="",
        tool_calls=[{"id": "old-read", "name": "read_file", "args": {"path": route}}],
    )
    old_tool = ToolMessage(id="old-tool", content="# Generate PPTX", tool_call_id="old-read")
    runtime = SimpleNamespace(
        context={
            "thread_id": "thread-1",
            "run_id": "new-run",
            "__deerflow_pre_run_message_ids": ["old-ai", "old-tool"],
        }
    )
    state = {
        "messages": [old_ai, old_tool],
        "skill_workflow": _workflow(
            selected_authority=route,
            resume_documents=[route],
            loaded_authority_run_id="previous-run",
            loaded_authority_documents=[route],
        ),
    }

    assert middleware.before_model(state, runtime) is None


def test_new_run_authority_barrier_allows_read_only_path_diagnosis():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    state = {
        "messages": [
            AIMessage(
                id="diagnose",
                content="",
                tool_calls=[
                    {
                        "id": "ls-project",
                        "name": "ls",
                        "args": {"path": "/mnt/user-data/workspace/projects/demo"},
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            selected_authority=route,
            resume_documents=[route],
            loaded_authority_run_id="previous-run",
            loaded_authority_documents=[route],
        ),
    }

    assert middleware.after_model(state, _runtime("new-run")) is None


def test_ppt_confirm_ui_owner_contract_is_required_when_receipts_are_managed():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    confirm = "/mnt/skills/public/ppt-master/scripts/docs/confirm_ui.md"
    state = {
        "messages": [
            AIMessage(
                id="attempt",
                content="",
                tool_calls=[
                    {
                        "id": "write-stage1",
                        "name": "write_file",
                        "args": {
                            "path": "/mnt/user-data/workspace/projects/demo/confirm_ui/recommendations.stage1.json",
                            "content": "{}",
                        },
                    }
                ],
            )
        ],
        "skill_workflow": _workflow(
            selected_authority=route,
            resume_documents=[route],
            managed_artifacts=["confirm_ui/result.json"],
            loaded_authority_run_id="current-run",
            loaded_authority_documents=[route],
        ),
    }

    reload_update = middleware.after_model(state, _runtime("current-run"))

    assert reload_update is not None
    assert "jump_to" not in reload_update
    assert reload_update["skill_workflow"]["gate_status"] == "authority-reload-required"
    assert reload_update["skill_workflow"]["resume_pointer"] == confirm
    assert reload_update["messages"][0].tool_calls[0]["args"] == {"path": confirm}


def test_ppt_confirm_ui_owner_contract_read_closes_authority_barrier():
    middleware = SkillWorkflowMiddleware()
    route = "/mnt/skills/public/ppt-master/workflows/generate-pptx.md"
    confirm = "/mnt/skills/public/ppt-master/scripts/docs/confirm_ui.md"
    state = {
        "messages": [
            AIMessage(
                id="reload",
                content="",
                tool_calls=[{"id": "read", "name": "read_file", "args": {"path": confirm}}],
            )
        ],
        "skill_workflow": _workflow(
            selected_authority=route,
            resume_documents=[route],
            managed_artifacts=["confirm_ui/result.json"],
            loaded_authority_run_id="current-run",
            loaded_authority_documents=[route],
        ),
    }

    assert middleware.after_model(state, _runtime("current-run")) is None


def test_confirm_ui_owner_preflight_rejects_invalid_stage_before_long_wait():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --wait-only --wait-stage stage2"
    request = _tool_request(
        command,
        _workflow(
            project_path=project,
            confirmation_surface="page",
            managed_artifacts=["confirm_ui/result.json"],
        ),
    )
    sandbox = MagicMock()
    sandbox.execute_command.return_value = 'DEERFLOW_CONFIRM_UI_PREFLIGHT={"status":"invalid","error":"custom_candidates.image_strategy.rendering must be custom"}'
    handler = MagicMock(return_value=ToolMessage(content="waited", tool_call_id="confirm-wait"))

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._resolve_active_sandbox",
        return_value=sandbox,
    ):
        result = middleware.wrap_tool_call(request, handler)

    assert result.status == "error"
    assert "rendering must be custom" in str(result.content)
    assert "did not duplicate or weaken" in str(result.content)
    handler.assert_not_called()
    preflight = sandbox.execute_command.call_args.args[0]
    assert "/api/session" in preflight
    assert "/api/recommendations" in preflight
    assert 'target = "stage2"' in preflight


def test_confirm_ui_owner_preflight_allows_valid_stage_and_template_wait_is_unchanged():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    workflow = _workflow(
        project_path=project,
        confirmation_surface="page",
        managed_artifacts=["confirm_ui/result.json"],
    )
    sandbox = MagicMock()
    sandbox.execute_command.return_value = 'DEERFLOW_CONFIRM_UI_PREFLIGHT={"status":"valid"}'
    handler = MagicMock(return_value=ToolMessage(content="waited", tool_call_id="confirm-wait"))
    stage_request = _tool_request(
        f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --wait-only --wait-stage stage1",
        workflow,
    )

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._resolve_active_sandbox",
        return_value=sandbox,
    ):
        result = middleware.wrap_tool_call(stage_request, handler)

    assert result.content == "waited"
    handler.assert_called_once()
    sandbox.execute_command.assert_called_once()

    sandbox.reset_mock()
    handler.reset_mock()
    template_request = _tool_request(
        f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --wait-only --wait-stage template",
        workflow,
    )
    result = middleware.wrap_tool_call(template_request, handler)

    assert result.content == "waited"
    sandbox.execute_command.assert_not_called()
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_confirm_ui_owner_preflight_async_path_returns_owner_error():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    request = _tool_request(
        f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --wait-only",
        _workflow(
            project_path=project,
            confirmation_surface="page",
            managed_artifacts=["confirm_ui/result.json"],
        ),
    )
    sandbox = MagicMock()
    sandbox.execute_command.return_value = 'DEERFLOW_CONFIRM_UI_PREFLIGHT={"status":"invalid","error":"stage3 production recommendation is incomplete"}'
    handler = AsyncMock(return_value=ToolMessage(content="waited", tool_call_id="confirm-wait"))

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._resolve_active_sandbox",
        return_value=sandbox,
    ):
        result = await middleware.awrap_tool_call(request, handler)

    assert result.status == "error"
    assert "stage3 production recommendation is incomplete" in str(result.content)
    handler.assert_not_awaited()


def test_template_contract_blocks_flat_spec_lock_that_discards_confirmed_prototypes():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    workflow = _workflow(
        project_path=project,
        required_capabilities={"presentation": True, "template": True},
        loaded_authority_documents=[
            "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
            "/mnt/skills/public/ppt-master/references/strategist-template.md",
            "/mnt/skills/public/ppt-master/templates/spec_lock_reference.md",
        ],
    )
    request = _file_tool_request(
        "write_file",
        {
            "path": f"{project}/spec_lock.md",
            "content": "## pptx_structure\n- mode: flat\n- template_reuse_scope: style\n",
        },
        workflow,
    )
    files = _template_contract_sandbox_files()
    sandbox = MagicMock()
    sandbox.read_file.side_effect = lambda path: files[path]
    handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="template-contract"))

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._resolve_active_sandbox",
        return_value=sandbox,
    ):
        result = middleware.wrap_tool_call(request, handler)

    assert result.status == "error"
    assert "PPT_TEMPLATE_CONTRACT" in str(result.content)
    assert "不得把结构模板降级成仿制风格" in str(result.content)
    handler.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        (
            "export SKILL_DIR='/mnt/skills/public/ppt-master' && "
            "python3 ${SKILL_DIR}/scripts/confirm_ui/server.py "
            "/mnt/user-data/workspace/projects/demo --complete-template-phase"
        ),
        (
            "export SKILL_DIR='/mnt/skills/public/ppt-master' && "
            "python3 ${SKILL_DIR}/scripts/confirm_ui/server.py "
            "/mnt/user-data/workspace/projects/demo --wait-only --wait-stage stage1"
        ),
    ],
)
def test_template_completion_and_stage1_block_partial_single_workspace_install(command):
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    source = "/mnt/skills/public/ppt-master/templates/decks/中国海油"
    workflow = _workflow(project_path=project)
    request = _tool_request(command, workflow)
    files = _template_contract_sandbox_files()
    files[f"{source}/templates/design_spec.md"] = "deck spec"
    files[f"{source}/templates/01_cover.svg"] = "<svg/>"
    files[f"{source}/images/image9.png"] = b"png"
    files[f"{project}/templates/design_spec.md"] = "deck spec"
    sandbox = MagicMock()
    _attach_template_manifest_execution(sandbox, files)
    sandbox.read_file.side_effect = lambda path: files[path]
    sandbox.list_dir.side_effect = lambda path, max_depth=2: sorted(
        key for key in files if key.startswith(path.rstrip("/") + "/")
    )

    def download(path):
        if path not in files:
            raise OSError(path)
        value = files[path]
        return value if isinstance(value, bytes) else value.encode("utf-8")

    sandbox.download_file.side_effect = download
    handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="template-contract"))

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._resolve_active_sandbox",
        return_value=sandbox,
    ):
        result = middleware.wrap_tool_call(request, handler)

    assert result.status == "error"
    assert "templates/01_cover.svg" in str(result.content)
    assert "images/image9.png" in str(result.content)
    handler.assert_not_called()


def test_template_contract_authoring_blocks_partial_single_workspace_install():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    files = _template_contract_sandbox_files()
    selection = json.loads(files[f"{project}/confirm_ui/template_selection.json"])
    source = selection["selections"][0]["workspace_root"]
    files[f"{source}/templates/design_spec.md"] = "deck spec"
    files[f"{source}/templates/01_cover.svg"] = "<svg/>"
    files[f"{source}/images/image9.png"] = b"png"
    files[f"{project}/templates/design_spec.md"] = "deck spec"
    sandbox = MagicMock()
    _attach_template_manifest_execution(sandbox, files)
    sandbox.read_file.side_effect = lambda path: files[path]
    sandbox.list_dir.side_effect = lambda path, max_depth=2: sorted(
        key for key in files if key.startswith(path.rstrip("/") + "/")
    )

    def download(path):
        if path not in files:
            raise OSError(path)
        value = files[path]
        return value if isinstance(value, bytes) else value.encode("utf-8")

    sandbox.download_file.side_effect = download
    request = _file_tool_request(
        "write_file",
        {"path": f"{project}/design_spec.md", "content": "# Design Specification"},
        _workflow(project_path=project),
    )
    handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="template-contract"))

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._resolve_active_sandbox",
        return_value=sandbox,
    ):
        result = middleware.wrap_tool_call(request, handler)

    assert result.status == "error"
    assert "templates/01_cover.svg" in str(result.content)
    assert "images/image9.png" in str(result.content)
    handler.assert_not_called()


def test_partial_template_copy_reports_remaining_install_files():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    files = _template_contract_sandbox_files()
    selection = json.loads(files[f"{project}/confirm_ui/template_selection.json"])
    source = selection["selections"][0]["workspace_root"]
    files[f"{source}/templates/design_spec.md"] = "deck spec"
    files[f"{source}/templates/01_cover.svg"] = "<svg/>"
    files[f"{source}/images/image9.png"] = b"png"
    files[f"{project}/templates/design_spec.md"] = "deck spec"
    files[f"{project}/templates/01_cover.svg"] = "<svg/>"
    sandbox = MagicMock()
    _attach_template_manifest_execution(sandbox, files)
    sandbox.read_file.side_effect = lambda path: files[path]
    sandbox.list_dir.side_effect = lambda path, max_depth=2: sorted(
        key for key in files if key.startswith(path.rstrip("/") + "/")
    )

    def download(path):
        if path not in files:
            raise OSError(path)
        value = files[path]
        return value if isinstance(value, bytes) else value.encode("utf-8")

    sandbox.download_file.side_effect = download
    request = _tool_request(
        f"cp {source}/templates/01_cover.svg {project}/templates/",
        _workflow(project_path=project),
    )
    handler = MagicMock(return_value=ToolMessage(content="copied", tool_call_id="confirm-wait"))

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._resolve_active_sandbox",
        return_value=sandbox,
    ):
        result = middleware.wrap_tool_call(request, handler)

    assert result.status == "error"
    assert "images/image9.png" in str(result.content)
    handler.assert_called_once()


def test_template_contract_blocks_direct_svg_that_drops_inherited_deck_structure():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    workflow = _workflow(
        project_path=project,
        required_capabilities={"presentation": True, "template": True},
        loaded_authority_documents=[
            "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
            "/mnt/skills/public/ppt-master/references/strategist-template.md",
            "/mnt/skills/public/ppt-master/templates/spec_lock_reference.md",
        ],
    )
    request = _file_tool_request(
        "write_file",
        {
            "path": f"{project}/svg_output/01_cover.svg",
            "content": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#1F497D"/></svg>',
        },
        workflow,
    )
    files = _template_contract_sandbox_files()
    selection = json.loads(files[f"{project}/confirm_ui/template_selection.json"])
    source = selection["selections"][0]["workspace_root"]
    files[f"{source}/templates/01_cover.svg"] = files[f"{project}/templates/01_cover.svg"]
    sandbox = MagicMock()
    _attach_template_manifest_execution(sandbox, files)
    sandbox.read_file.side_effect = lambda path: files[path]
    sandbox.list_dir.side_effect = lambda path, max_depth=2: sorted(
        key for key in files if key.startswith(path.rstrip("/") + "/")
    )
    sandbox.download_file.side_effect = lambda path: files[path].encode("utf-8")
    handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="template-contract"))

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._resolve_active_sandbox",
        return_value=sandbox,
    ):
        result = middleware.wrap_tool_call(request, handler)

    assert result.status == "error"
    assert "data-pptx-master" in str(result.content)
    assert "#layout-bg" in str(result.content)
    handler.assert_not_called()


def test_template_contract_allows_slot_content_change_while_preserving_deck_atoms():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    workflow = _workflow(
        project_path=project,
        required_capabilities={"presentation": True, "template": True},
        loaded_authority_documents=[
            "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
            "/mnt/skills/public/ppt-master/references/strategist-template.md",
            "/mnt/skills/public/ppt-master/templates/spec_lock_reference.md",
        ],
    )
    files = _template_contract_sandbox_files()
    page = files[f"{project}/templates/01_cover.svg"].replace(">标题</text>", ">中国海油智能化</text>")
    request = _file_tool_request(
        "write_file",
        {"path": f"{project}/svg_output/01_cover.svg", "content": page},
        workflow,
    )
    sandbox = MagicMock()
    selection = json.loads(files[f"{project}/confirm_ui/template_selection.json"])
    source = selection["selections"][0]["workspace_root"]
    files[f"{source}/templates/01_cover.svg"] = files[f"{project}/templates/01_cover.svg"]
    _attach_template_manifest_execution(sandbox, files)
    sandbox.read_file.side_effect = lambda path: files[path]
    sandbox.list_dir.side_effect = lambda path, max_depth=2: sorted(
        key for key in files if key.startswith(path.rstrip("/") + "/")
    )
    sandbox.download_file.side_effect = lambda path: files[path].encode("utf-8")
    handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="template-contract"))

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._resolve_active_sandbox",
        return_value=sandbox,
    ):
        result = middleware.wrap_tool_call(request, handler)

    assert result.content == "OK"
    handler.assert_called_once()


def test_template_contract_postflight_rejects_shell_written_structural_imitation():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    workflow = _workflow(
        project_path=project,
        required_capabilities={"presentation": True, "template": True},
        loaded_authority_documents=[
            "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
            "/mnt/skills/public/ppt-master/references/strategist-template.md",
            "/mnt/skills/public/ppt-master/templates/spec_lock_reference.md",
        ],
    )
    target = f"{project}/svg_output/01_cover.svg"
    request = _file_tool_request(
        "bash",
        {"command": f"python3 -c \"open('{target}','w').write('<svg/>')\""},
        workflow,
    )
    files = _template_contract_sandbox_files()
    sandbox = MagicMock()
    sandbox.read_file.side_effect = lambda path: files[path]

    def handler(_request):
        files[target] = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"/>'
        return ToolMessage(content="OK", tool_call_id="template-contract")

    with patch(
        "deerflow.agents.middlewares.skill_workflow_middleware._resolve_active_sandbox",
        return_value=sandbox,
    ):
        result = middleware.wrap_tool_call(request, handler)

    assert result.status == "error"
    assert "PPT_TEMPLATE_CONTRACT" in str(result.content)


def test_managed_confirmation_receipts_cannot_be_fabricated_by_file_tools():
    workflow = _workflow(
        project_path="/mnt/user-data/workspace/projects/demo",
        managed_artifacts=["confirm_ui/template_selection.json"],
    )

    violation = _managed_artifact_violation(
        "write_file",
        {
            "path": "/mnt/user-data/workspace/projects/demo/confirm_ui/template_selection.json",
            "content": "{}",
        },
        workflow,
    )

    assert violation == "confirm_ui/template_selection.json"


def test_confirmation_page_surface_is_checkpointed_from_successful_daemon_start():
    middleware = SkillWorkflowMiddleware()
    command = "python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py /mnt/user-data/workspace/projects/demo --daemon"
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "confirm-ui", "name": "bash", "args": {"command": command}}],
        ),
        ToolMessage(content="Live at http://127.0.0.1:5050", tool_call_id="confirm-ui"),
    ]

    workflow = middleware.before_model(
        {"messages": messages, "skill_workflow": _workflow()},
        _runtime(),
    )["skill_workflow"]

    assert workflow["confirmation_surface"] == "page"
    assert workflow["confirmation_surface_switch_allowed"] is False
    assert workflow["confirmation_ui_active"] is True
    assert workflow["confirmation_url"] == "http://127.0.0.1:5050"


def test_confirmation_page_handoff_is_injected_while_owner_server_is_active():
    middleware = SkillWorkflowMiddleware()
    workflow = _workflow(
        confirmation_surface="page",
        confirmation_ui_active=True,
        confirmation_url="http://127.0.0.1:5050",
    )
    request = MagicMock()
    request.runtime = _runtime()
    request.state = {"skill_workflow": workflow}
    request.messages = []
    request.override.side_effect = lambda **kwargs: kwargs

    augmented = middleware._augment_request(request)
    reminders = {message.name: message.content for message in augmented["messages"]}

    assert "skill_confirmation_page_handoff" in reminders
    assert "http://127.0.0.1:5050" in reminders["skill_confirmation_page_handoff"]
    assert "Do not shorten" in reminders["skill_confirmation_page_handoff"]
    assert "never run a package installer" in reminders["skill_confirmation_page_handoff"]


def test_bundled_ppt_skill_blocks_runtime_dependency_installation():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    request = _tool_request(
        f"pip install flask -q && python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --daemon",
        _workflow(project_path=project),
    )
    handler = MagicMock(return_value=ToolMessage(content="started", tool_call_id="confirm-wait"))

    result = middleware.wrap_tool_call(request, handler)

    assert result.status == "error"
    assert "preinstalled in the release Sandbox image" in str(result.content)
    handler.assert_not_called()


def test_masked_confirm_ui_error_is_not_checkpointed_as_success():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --wait-only --wait-stage template --wait-timeout 10 2>&1 | tail -20"
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "masked-wait", "name": "bash", "args": {"command": command}}],
        ),
        ToolMessage(
            content=("[23:27:42] [INFO] confirm_ui: waiting for browser template selection...\n[23:27:52] [ERROR] confirm_ui: timed out waiting for template selection"),
            tool_call_id="masked-wait",
        ),
    ]

    workflow = middleware.before_model(
        {
            "messages": messages,
            "skill_workflow": _workflow(
                project_path=project,
                confirmation_surface="page",
                confirmation_ui_active=True,
                confirmation_url="http://127.0.0.1:5050",
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert workflow["status"] == "waiting"
    assert workflow["gate_status"] == "waiting-for-user"
    assert workflow["confirmation_surface_switch_allowed"] is True
    assert workflow["confirmation_wait_timed_out"] is True
    assert workflow["evidence"][0]["status"] == "waiting"


def test_confirm_ui_wait_timeout_clears_prior_managed_artifact_gate():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --wait-only --wait-stage template"
    workflow = middleware.before_model(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "wait-timeout", "name": "bash", "args": {"command": command}}],
                ),
                ToolMessage(
                    content=("Error: Bash command exited with code 124. Std Error: [12:00:00] [ERROR] confirm_ui: timed out waiting for template selection"),
                    tool_call_id="wait-timeout",
                    status="error",
                ),
            ],
            "skill_workflow": _workflow(
                project_path=project,
                managed_artifacts=["confirm_ui/template_selection.json"],
                confirmation_surface="page",
                confirmation_ui_active=True,
                status="blocked",
                gate_status="managed-artifact-owner-required",
                resume_pointer=f"{project}/confirm_ui/template_selection.json",
            ),
        },
        _runtime(),
    )["skill_workflow"]

    assert workflow["status"] == "waiting"
    assert workflow["gate_status"] == "waiting-for-user"
    assert workflow["gate_owner_tool"] == ""
    assert workflow["confirmation_wait_timed_out"] is True


def test_confirmation_timeout_blocks_unrequested_receipt_reset():
    middleware = SkillWorkflowMiddleware()
    project = "/mnt/user-data/workspace/projects/demo"
    command = f"python3 /mnt/skills/public/ppt-master/scripts/confirm_ui/server.py {project} --reset-template-phase"
    state = {
        "messages": [
            HumanMessage(content="继续"),
            AIMessage(
                id="reset-after-timeout",
                content="",
                tool_calls=[{"id": "reset", "name": "bash", "args": {"command": command}}],
            ),
        ],
        "skill_workflow": _workflow(
            project_path=project,
            confirmation_surface="page",
            confirmation_ui_active=True,
            confirmation_wait_timed_out=True,
            status="waiting",
            gate_status="waiting-for-user",
            resume_pointer=f"{project}/confirm_ui/template_selection.json",
        ),
    }

    result = middleware.after_model(state, _runtime())

    assert result is not None
    assert result["jump_to"] == "model"
    assert result["skill_workflow"]["gate_status"] == "waiting-for-user"
    assert "Do not reset" in result["skill_workflow"]["gate_detail"]


def test_premature_completion_claim_gets_one_bounded_retry():
    middleware = SkillWorkflowMiddleware()
    state = {
        "messages": [AIMessage(content="PPT 已生成完成，可以下载。")],
        "skill_workflow": _workflow(current_stage="svg export"),
    }

    first = middleware.after_model(state, _runtime())
    second = middleware.after_model(state, _runtime())

    assert first == {"jump_to": "model"}
    assert second is not None
    assert second["skill_workflow"]["status"] == "blocked"
    assert "尚未通过最终交付验收" in second["messages"][0].content


def test_tool_progress_text_is_not_hidden_by_workflow_adapter():
    middleware = SkillWorkflowMiddleware()
    message = AIMessage(
        id="premature-with-tools",
        content="Completed and delivered. I will now inspect the package.",
        tool_calls=[
            {
                "id": "inspect-package",
                "name": "read_file",
                "args": {"path": "/mnt/user-data/workspace/projects/demo/report.json"},
            }
        ],
    )

    result = middleware.after_model(
        {
            "messages": [message],
            "skill_workflow": _workflow(current_stage="delivery acceptance"),
        },
        _runtime(),
    )

    assert result is None


def test_loop_hard_stop_remains_terminal_even_when_route_selection_is_pending():
    middleware = SkillWorkflowMiddleware()
    message = AIMessage(
        id="loop-stop",
        content=("[FORCED STOP] Repeated tool calls exceeded the safety limit. Producing final answer with results collected so far."),
        additional_kwargs={
            "deerflow_terminal_stop": True,
            "error_reason": "loop_capped",
        },
    )
    workflow = _workflow(selected_authority="", loaded_authority_documents=[])
    runtime = _runtime()
    runtime.context["stop_reason"] = "loop_capped"

    assert (
        middleware.after_model(
            {"messages": [message], "skill_workflow": workflow},
            runtime,
        )
        is None
    )


@pytest.mark.asyncio
async def test_agent_cleanup_hooks_do_not_reset_bounded_sequence_history():
    middleware = SkillWorkflowMiddleware()
    runtime = _runtime()
    key = middleware._key(runtime)
    sequence_key = ("thread-1", "run-1", "template-selection|project|pending")
    middleware._pending_recovery[key] = "pending"
    middleware._retry_counts[key] = 1
    middleware._sequence_retry_counts[sequence_key] = 2

    assert middleware.after_agent({"messages": []}, runtime) is None
    assert key not in middleware._pending_recovery
    assert key not in middleware._retry_counts
    assert middleware._sequence_retry_counts[sequence_key] == 2

    middleware._pending_recovery[key] = "pending"
    middleware._retry_counts[key] = 1
    assert await middleware.aafter_agent({"messages": []}, runtime) is None
    assert key not in middleware._pending_recovery
    assert key not in middleware._retry_counts
    assert middleware._sequence_retry_counts[sequence_key] == 2
