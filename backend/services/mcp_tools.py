"""Custom MCP server that works around the Pydantic validation bug in claude-agent-sdk."""

from __future__ import annotations

import logging

from mcp.server.lowlevel import Server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)


def create_trainable_mcp_server(tool_handlers: dict):
    """Create an MCP server with manually registered tools that bypass the SDK's buggy serialization."""
    server = Server("trainable")

    tools = []
    for name, info in tool_handlers.items():
        tools.append(
            Tool(
                name=name,
                description=info["description"],
                inputSchema=info["input_schema"],
            )
        )

    @server.list_tools()
    async def list_tools():
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        handler = tool_handlers.get(name)
        if not handler:
            return [TextContent(type="text", text=f"[ERROR] Unknown tool: {name}")]

        try:
            result = await handler["handler"](arguments)
        except Exception as e:
            logger.exception(f"Tool {name} error")
            return [TextContent(type="text", text=f"[ERROR] Tool {name} raised: {e}")]

        # Extract text from the result dict.
        texts = []
        if isinstance(result, dict) and "content" in result:
            for item in result["content"]:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))

        # Preserve the handler's is_error flag by prefixing the text. The MCP
        # SDK on this version doesn't propagate isError through a bare
        # list[TextContent] return, so we signal the error in-band so the LLM
        # recognises the failure and adapts.
        text = "\n".join(texts) or "(no output)"
        if isinstance(result, dict) and result.get("is_error"):
            text = f"[ERROR] {text}"

        return [TextContent(type="text", text=text)]

    return {"type": "sdk", "name": "trainable", "instance": server}
