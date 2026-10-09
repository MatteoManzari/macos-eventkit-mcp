"""
Tests for list_reminders tool.

Covers:
- Listing reminders from every known list and measuring performance
- Default behaviour (All Reminders)
- Limit parameter
- Unknown list returns empty result
"""
import os
import time
import pytest
from conftest import mcp_session, parse_result


TARGET_LIST = os.getenv("TEST_REMINDERS_LIST", "")
KNOWN_LISTS = [
    name.strip()
    for name in os.getenv("TEST_REMINDERS_LISTS", TARGET_LIST).split(",")
    if name.strip()
]
pytestmark = pytest.mark.skipif(
    not TARGET_LIST, reason="Set TEST_REMINDERS_LIST to a writable reminder list"
)

PERF_THRESHOLD_SECONDS = 10.0


@pytest.mark.asyncio
async def test_tools_available():
    """Server exposes all expected tools."""
    async with mcp_session() as session:
        tools = await session.list_tools()
        names = [t.name for t in tools.tools]
        assert "list_reminders" in names
        assert "list_reminder_lists" in names
        assert "create_reminder" in names
        assert "complete_reminder" in names
        assert "delete_reminder" in names
        assert "get_reminder" in names
        assert "update_reminder" in names


@pytest.mark.asyncio
@pytest.mark.parametrize("list_name", KNOWN_LISTS)
async def test_list_reminders_per_list(list_name):
    """Fetch reminders for every known list. Asserts valid response and timing."""
    async with mcp_session() as session:
        start = time.perf_counter()
        result = await session.call_tool(
            "list_reminders",
            {"params": {"list_name": list_name, "limit": 50}},
        )
        elapsed = time.perf_counter() - start

        data = parse_result(result)
        assert "error" not in data, f"Error for list '{list_name}': {data.get('error')}"
        assert "reminders" in data
        assert isinstance(data["reminders"], list)

        for reminder in data["reminders"]:
            for key in ("id", "title", "body", "priority", "completed", "due_date", "list_name"):
                assert key in reminder

        print(f"\n  [{list_name}] {data['count']} reminders in {elapsed:.2f}s")
        assert elapsed < PERF_THRESHOLD_SECONDS, (
            f"list '{list_name}' took {elapsed:.2f}s (threshold: {PERF_THRESHOLD_SECONDS}s)"
        )


@pytest.mark.asyncio
async def test_list_reminders_all():
    """'All Reminders' aggregates across every list."""
    async with mcp_session() as session:
        start = time.perf_counter()
        result = await session.call_tool(
            "list_reminders",
            {"params": {"list_name": "All Reminders", "limit": 200}},
        )
        elapsed = time.perf_counter() - start
        data = parse_result(result)
        assert "error" not in data, data.get("error")
        assert isinstance(data["reminders"], list)
        print(f"\n  [All Reminders] {data['count']} reminders in {elapsed:.2f}s")


@pytest.mark.asyncio
async def test_list_reminders_limit():
    """Limit parameter is respected."""
    async with mcp_session() as session:
        result = await session.call_tool(
            "list_reminders",
            {"params": {"list_name": "All Reminders", "limit": 2}},
        )
        data = parse_result(result)
        assert "error" not in data, data.get("error")
        assert len(data["reminders"]) <= 2


@pytest.mark.asyncio
async def test_list_reminders_unknown_list():
    """Querying a non-existent list returns an error gracefully."""
    async with mcp_session() as session:
        result = await session.call_tool(
            "list_reminders",
            {"params": {"list_name": "No Such List for MCP Tests", "limit": 10}},
        )
        data = parse_result(result)
        # May return error or empty list — both are acceptable
        reminders = data.get("reminders", [])
        assert isinstance(reminders, list)


@pytest.mark.asyncio
async def test_list_reminders_include_completed():
    """include_completed=True should not raise an error."""
    async with mcp_session() as session:
        result = await session.call_tool(
            "list_reminders",
            {"params": {"list_name": TARGET_LIST, "limit": 50, "include_completed": True}},
        )
        data = parse_result(result)
        assert "error" not in data, data.get("error")
        assert isinstance(data["reminders"], list)


@pytest.mark.asyncio
async def test_list_reminder_lists():
    """list_reminder_lists returns all available lists with counts."""
    async with mcp_session() as session:
        result = await session.call_tool("list_reminder_lists", {})
        data = parse_result(result)
        assert "error" not in data, data.get("error")
        assert "lists" in data
        assert "count" in data
        assert isinstance(data["lists"], list)
        assert data["count"] == len(data["lists"])

        names = [lst["name"] for lst in data["lists"]]
        # At least the lists used in test_list_reminders_per_list should be present
        # (KNOWN_LISTS may lag behind; check only lists that actually appear)
        actually_present = [k for k in KNOWN_LISTS if k in names]
        assert len(actually_present) > 0, (
            f"None of the KNOWN_LISTS found in {names}"
        )
        # Always require the TARGET_LIST to exist (used in create/update/delete tests)
        assert TARGET_LIST in names, f"'{TARGET_LIST}' not found in {names}"

        for lst in data["lists"]:
            assert "name" in lst
            assert "incomplete_count" in lst
            assert isinstance(lst["incomplete_count"], int)


@pytest.mark.asyncio
async def test_list_reminders_search_query():
    """search_query filters results case-insensitively on title."""
    async with mcp_session() as session:
        # Create a reminder with a unique searchable title
        unique_token = f"SEARCHTEST{int(time.time())}"
        create_result = await session.call_tool(
            "create_reminder",
            {"params": {"title": f"[TEST] {unique_token}", "list_name": TARGET_LIST}},
        )
        create_data = parse_result(create_result)
        assert create_data["status"] == "success", create_data.get("error")
        reminder_id = create_data["reminder_id"]

        try:
            # Search with the unique token — should find exactly this reminder
            search_result = await session.call_tool(
                "list_reminders",
                {"params": {"list_name": "All Reminders", "search_query": unique_token}},
            )
            search_data = parse_result(search_result)
            assert "error" not in search_data, search_data.get("error")
            ids = [r["id"] for r in search_data["reminders"]]
            assert reminder_id in ids, "Created reminder not found by search_query"

            # Search with a garbage query — should return no results
            no_result = await session.call_tool(
                "list_reminders",
                {"params": {"list_name": "All Reminders", "search_query": "XYZZY_NOMATCH_99999"}},
            )
            no_data = parse_result(no_result)
            assert no_data.get("count", 0) == 0 or no_data.get("reminders") == []
        finally:
            await session.call_tool(
                "delete_reminder",
                {"params": {"reminder_id": reminder_id}},
            )


@pytest.mark.asyncio
async def test_get_reminder():
    """get_reminder returns full details for a known ID."""
    async with mcp_session() as session:
        # Create a reminder to look up
        title = f"[TEST] GetById {int(time.time())}"
        create_result = await session.call_tool(
            "create_reminder",
            {"params": {"title": title, "list_name": TARGET_LIST, "body": "Test notes"}},
        )
        create_data = parse_result(create_result)
        assert create_data["status"] == "success", create_data.get("error")
        reminder_id = create_data["reminder_id"]

        try:
            get_result = await session.call_tool(
                "get_reminder",
                {"params": {"reminder_id": reminder_id}},
            )
            get_data = parse_result(get_result)
            assert "error" not in get_data, get_data.get("error")
            assert get_data["id"] == reminder_id
            assert get_data["title"] == title
            assert "body" in get_data
            assert "priority" in get_data
            assert "completed" in get_data
            assert "due_date" in get_data
            assert "list_name" in get_data
        finally:
            await session.call_tool(
                "delete_reminder",
                {"params": {"reminder_id": reminder_id}},
            )


@pytest.mark.asyncio
async def test_get_reminder_nonexistent():
    """get_reminder with a non-existent ID returns failed status."""
    async with mcp_session() as session:
        result = await session.call_tool(
            "get_reminder",
            {"params": {"reminder_id": "x-apple-reminder://00000000-0000-0000-0000-000000000000"}},
        )
        data = parse_result(result)
        assert data["status"] == "failed"
