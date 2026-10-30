"""Resolve an operator-selected team agent; never trust request scope flags."""

def resolve_staged_team_agent(stage_config, agent_name, *, user_id, app_config):
    if not stage_config.enabled or not agent_name or agent_name not in stage_config.team_agents:
        return None
    from deerflow.config.agents_config import resolve_agent_dir, validate_agent_name
    from deerflow.config.paths import get_paths

    name = validate_agent_name(agent_name)
    paths = get_paths()
    # Match the loader's team-first / legacy-shared fallback order.
    team = next((p.resolve() for p in (paths.team_agent_dir(name), paths.agent_dir(name))
                 if (p / "config.yaml").is_file()), None)
    if team is None:
        return None
    backend = app_config.agent_storage.backend
    if backend == "db":
        from deerflow.persistence.agents import get_agent_store
        if get_agent_store().exists(name, user_id=user_id):
            return None
    elif backend == "file":
        if resolve_agent_dir(name, user_id=user_id).resolve() != team:
            return None
    else:
        return None
    return name
