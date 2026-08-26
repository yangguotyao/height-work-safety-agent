import asyncio

from mcp.client import Client

from backend.app.db import Database
from backend.app.mcp_server import build_mcp_server


def test_mcp_exposes_bounded_read_only_tools(test_settings):
    Database(test_settings.resolved_database_path).initialize()
    server = build_mcp_server(test_settings)

    async def exercise() -> None:
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert names == {
                "project_status",
                "search_project_knowledge",
                "search_safety_rules",
                "latest_dynamic_risk",
            }
            result = await client.call_tool("project_status", {})
            assert not result.is_error
            assert result.structured_content["project_name"] == test_settings.project_name

    asyncio.run(exercise())
