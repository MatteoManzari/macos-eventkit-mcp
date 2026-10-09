"""
Shared helpers for reminders_mcp and calendar_mcp tests.
"""
import json
from contextlib import asynccontextmanager
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


SERVER_PARAMS = StdioServerParameters(
    command="env/bin/python",
    args=["reminders_mcp.py"],
)

CALENDAR_SERVER_PARAMS = StdioServerParameters(
    command="env/bin/python",
    args=["calendar_mcp.py"],
)


@asynccontextmanager
async def mcp_session():
    """Context manager that starts the Reminders MCP server and yields an initialized session.

    Using a plain context manager (not a pytest fixture) avoids the
    anyio 'exit cancel scope in a different task' teardown error,
    because enter and exit happen in the same coroutine/task.
    """
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as sess:
            await sess.initialize()
            yield sess


@asynccontextmanager
async def mcp_calendar_session():
    """Context manager that starts the Calendar MCP server and yields an initialized session."""
    async with stdio_client(CALENDAR_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as sess:
            await sess.initialize()
            yield sess


def parse_result(result) -> dict:
    """Parse the JSON payload returned by a tool call."""
    return json.loads(result.content[0].text)
