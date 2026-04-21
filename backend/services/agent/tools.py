"""Thin shim — delegates to the tools/ package for all MCP server building."""

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
    agent_id: str = "root",
    parent_agent_id: str | None = None,
):
    """Create a per-call MCP server with tools determined by the agent's YAML config."""
    # Lazy import to avoid circular dependency
    from tools import build_mcp_server

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
