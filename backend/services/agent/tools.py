"""Thin shim — delegates to the skills package for MCP server building."""

from services.skills import build_mcp_server

from .events import save_and_publish


def create_mcp_server(
    session_id: str,
    experiment_id: str,
    stage: str,
    sandbox_config: dict | None = None,
    agent_type: str = "eda",
    depth: int = 0,
    instructions: str = "",
    model: str | None = None,
    agent_models: dict | None = None,
    agent_thinking: dict | None = None,
    agent_id: str = "root",
    parent_agent_id: str | None = None,
):
    """Create a per-call MCP server with capability skills determined by the agent's YAML.

    `agent_thinking` is accepted for caller-API symmetry but isn't forwarded to
    the MCP layer — reasoning level is resolved in `runner._drive_provider`
    against the model catalog before the provider call, not at skill-handler
    construction time.
    """
    del agent_thinking  # not used by the MCP server; symmetry only
    return build_mcp_server(
        agent_type=agent_type,
        session_id=session_id,
        experiment_id=experiment_id,
        stage=stage,
        depth=depth,
        publish_fn=save_and_publish,
        sandbox_config=sandbox_config or {},
        model=model,
        instructions=instructions,
        agent_models=agent_models or {},
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
    )
