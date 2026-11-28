"""Build the two Gateway overlays from its mounted source, keeping other live edits."""
def _chat_aux_config(config, model_name):
    return config.model_copy(update={
        section: getattr(config, section).model_copy(update={"model_name": model_name})
        for section in ("title", "summarization")
    })


def retire_profile(config):
    # Called on the freshly loaded Gateway YAML dictionary, before resolving env.
    target = "deepseek-v4-flash"
    rows = config.get("models") or []
    retired = {str(r.get("name", "")).casefold() for r in rows
               if target in (str(r.get("name", "")).casefold(), str(r.get("model", "")).casefold())}
    retired.add(target)
    keep = [r for r in rows if str(r.get("name", "")).casefold() not in retired]
    if not keep:
        if rows:
            raise ValueError("Cannot retire the only configured model")
        return
    config["models"] = keep
    def redirect(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in {"model_name", "model", "moderation_model_name", "default_model"} and isinstance(v, str) and v.casefold() in retired:
                    obj[k] = keep[0]["name"]
                else:
                    redirect(v)
        elif isinstance(obj, list):
            for v in obj:
                redirect(v)
    # Leave separate systems' provider identities and credentials alone.
    for section in ("title", "summarization", "input_polish", "subagents", "skill_evolution", "suggestions"):
        redirect(config.get(section))


def overlay(text, module):
    import inspect
    if module == "app_config":
        anchor = "        config_data = cls.resolve_env_variables(config_data)"
        marker = "        retire_profile(config_data)"
        if marker not in text:
            if text.count(anchor) != 1 or text.count("class AppConfig(") != 1:
                raise ValueError("Gateway AppConfig loader differs from expected source")
            pos = text.index("class AppConfig(")
            if text[:pos].rstrip().splitlines()[-1].startswith("@"):
                raise ValueError("Unexpected decorated AppConfig")
            text = text[:pos] + inspect.getsource(retire_profile) + "\n\n" + text[pos:]
            text = text.replace(anchor, marker + "\n" + anchor)
    else:
        anchor = "    model_config = resolved_app_config.get_model_config(model_name)"
        marker = "    # Per-run chat helpers inherit the resolved conversation model."
        start = text.find("    model_name = _resolve_model_name(requested_model_name or agent_model_name,")
        if marker not in text:
            if start < 0 or text[start:].count(anchor + "\n") != 1:
                raise ValueError("Gateway lead-agent model resolution differs from expected source")
            insert = marker + "\n    resolved_app_config = _chat_aux_config(resolved_app_config, model_name)\n"
            text = text[:start] + text[start:].replace(anchor, insert + anchor)
            text += "\n\n" + inspect.getsource(_chat_aux_config)
        for call in ("TitleMiddleware(app_config=resolved_app_config)",
                     "_create_summarization_middleware(app_config=resolved_app_config)"):
            if call not in text:
                raise ValueError("Lead agent auxiliary model wiring changed")
    compile(text, module, "exec")
    return text


MODULES = {"app_config": "deerflow.config.app_config", "agent": "deerflow.agents.lead_agent.agent"}


def prepare(kub, data):
    code = ("import importlib.util,json,pathlib\nmods=" + repr(MODULES) +
            "\nout={}\nfor k,m in mods.items():\n p=pathlib.Path(importlib.util.find_spec(m).origin)\n"
            " out[k]=[str(p),p.read_text(encoding='utf-8')]\nprint(json.dumps(out))")
    result = kub("exec", "-i", "deployment/gateway", "-c", "gateway", "--", "/app/backend/.venv/bin/python",
                 "-B", "-", body=code.encode())
    for name, (path, text) in result.items():
        data[name + ".py"] = (path, overlay(text, name))


VERIFY = """
from deerflow.config import get_app_config
from deerflow.agents.lead_agent.agent import _chat_aux_config
from app.gateway.routers.models import list_models
import asyncio
cfg=get_app_config()
models=[(m.name,m.model) for m in cfg.models]
assert [(m.name,m.model) for m in asyncio.run(list_models(config=cfg)).models] == models
assert models and all(str(v).casefold() != 'deepseek-v4-flash' for pair in models for v in pair)
assert any('v4.1' in str(v).lower() for pair in models for v in pair)
assert any('glm' in str(v).lower() for pair in models for v in pair)
for section in ('title','summarization'):
    assert (getattr(cfg,section).model_name or '').casefold() != 'deepseek-v4-flash'
    original = getattr(cfg,section).model_name
    for name,_ in models:
        assert getattr(_chat_aux_config(cfg,name),section).model_name == name
    assert getattr(cfg,section).model_name == original
"""
