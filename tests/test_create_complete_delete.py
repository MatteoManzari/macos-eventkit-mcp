"""
Tests for create_reminder, complete_reminder, delete_reminder.

The three tools are tested in an integrated flow:
  create → verify → complete → verify → delete → verify

A unique title with a timestamp is used to avoid collisions with real reminders.
"""
import os
import time
import pytest
from conftest import mcp_session, parse_result

TARGET_LIST = os.getenv("TEST_REMINDERS_LIST", "")
pytestmark = pytest.mark.skipif(
    not TARGET_LIST, reason="Set TEST_REMINDERS_LIST to a writable reminder list"
)


# ---------------------------------------------------------------------------
# create_reminder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_reminder_minimal():
    """Create a reminder with only a title, then clean up."""
    async with mcp_session() as session:
        title = f"[TEST] Minimal {time.time():.0f}"

        result = await session.call_tool(
            "create_reminder",
            {"params": {"title": title, "list_name": TARGET_LIST}},
        )
        data = parse_result(result)
        assert data["status"] == "success", data.get("error")
        assert "reminder_id" in data
        assert data["title"] == title

        # Cleanup
        await session.call_tool(
            "delete_reminder",
            {"params": {"reminder_id": data["reminder_id"]}},
        )


@pytest.mark.asyncio
async def test_create_reminder_with_due_date():
    """Create a reminder with a due date and time, then clean up."""
    async with mcp_session() as session:
        title = f"[TEST] DueDate {time.time():.0f}"

        result = await session.call_tool(
            "create_reminder",
            {"params": {
                "title": title,
                "list_name": TARGET_LIST,
                "due_date": "2026-12-31",
                "due_time": "10:00",
            }},
        )
        data = parse_result(result)
        assert data["status"] == "success", data.get("error")
        reminder_id = data["reminder_id"]

        # Verify it appears in list
        list_result = await session.call_tool(
            "list_reminders",
            {"params": {"list_name": TARGET_LIST, "limit": 100}},
        )
        list_data = parse_result(list_result)
        ids = [r["id"] for r in list_data["reminders"]]
        assert reminder_id in ids, "Created reminder not found in list"

        # Cleanup
        await session.call_tool(
            "delete_reminder",
            {"params": {"reminder_id": reminder_id}},
        )


@pytest.mark.asyncio
async def test_create_reminder_with_body_and_priority():
    """Create a reminder with body and priority, then clean up."""
    async with mcp_session() as session:
        title = f"[TEST] Priority {time.time():.0f}"

        result = await session.call_tool(
            "create_reminder",
            {"params": {
                "title": title,
                "body": "Test reminder notes",
                "list_name": TARGET_LIST,
                "priority": 9,
            }},
        )
        data = parse_result(result)
        assert data["status"] == "success", data.get("error")

        # Cleanup
        await session.call_tool(
            "delete_reminder",
            {"params": {"reminder_id": data["reminder_id"]}},
        )


# ---------------------------------------------------------------------------
# complete_reminder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_reminder():
    """Create a reminder, mark it complete, verify, then delete."""
    async with mcp_session() as session:
        title = f"[TEST] Complete {time.time():.0f}"

        # Create
        create_data = parse_result(await session.call_tool(
            "create_reminder",
            {"params": {"title": title, "list_name": TARGET_LIST}},
        ))
        assert create_data["status"] == "success", create_data.get("error")
        reminder_id = create_data["reminder_id"]

        # Complete
        complete_data = parse_result(await session.call_tool(
            "complete_reminder",
            {"params": {"reminder_id": reminder_id}},
        ))
        assert complete_data["status"] == "success", complete_data.get("error")

        # Verify: should NOT appear in non-completed list
        list_data = parse_result(await session.call_tool(
            "list_reminders",
            {"params": {"list_name": TARGET_LIST, "limit": 100, "include_completed": False}},
        ))
        ids = [r["id"] for r in list_data["reminders"]]
        assert reminder_id not in ids, "Completed reminder still appears in active list"

        # Verify: SHOULD appear when including completed (use high limit to avoid cutoff)
        list_all_data = parse_result(await session.call_tool(
            "list_reminders",
            {"params": {"list_name": TARGET_LIST, "limit": 500, "include_completed": True}},
        ))
        ids_all = [r["id"] for r in list_all_data["reminders"]]
        assert reminder_id in ids_all, "Completed reminder not found in include_completed list"

        # Cleanup
        await session.call_tool(
            "delete_reminder",
            {"params": {"reminder_id": reminder_id}},
        )


@pytest.mark.asyncio
async def test_complete_nonexistent_reminder():
    """Completing a non-existent ID returns a failed status."""
    async with mcp_session() as session:
        result = await session.call_tool(
            "complete_reminder",
            {"params": {"reminder_id": "x-apple-reminder://00000000-0000-0000-0000-000000000000"}},
        )
        data = parse_result(result)
        assert data["status"] == "failed"


# ---------------------------------------------------------------------------
# delete_reminder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_reminder():
    """Create a reminder and delete it; verify it no longer appears."""
    async with mcp_session() as session:
        title = f"[TEST] Delete {time.time():.0f}"

        # Create
        create_data = parse_result(await session.call_tool(
            "create_reminder",
            {"params": {"title": title, "list_name": TARGET_LIST}},
        ))
        assert create_data["status"] == "success", create_data.get("error")
        reminder_id = create_data["reminder_id"]

        # Delete
        delete_data = parse_result(await session.call_tool(
            "delete_reminder",
            {"params": {"reminder_id": reminder_id}},
        ))
        assert delete_data["status"] == "success", delete_data.get("error")

        # Verify: should not appear anymore
        list_data = parse_result(await session.call_tool(
            "list_reminders",
            {"params": {"list_name": TARGET_LIST, "limit": 100}},
        ))
        ids = [r["id"] for r in list_data["reminders"]]
        assert reminder_id not in ids, "Deleted reminder still appears in list"


@pytest.mark.asyncio
async def test_delete_nonexistent_reminder():
    """Deleting a non-existent ID returns a failed status."""
    async with mcp_session() as session:
        result = await session.call_tool(
            "delete_reminder",
            {"params": {"reminder_id": "x-apple-reminder://00000000-0000-0000-0000-000000000000"}},
        )
        data = parse_result(result)
        assert data["status"] == "failed"


# ---------------------------------------------------------------------------
# update_reminder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_reminder_title():
    """Create a reminder, update its title, verify via get_reminder, then delete."""
    async with mcp_session() as session:
        original_title = f"[TEST] Update-before {time.time():.0f}"
        updated_title = f"[TEST] Update-after {time.time():.0f}"

        # Create
        create_data = parse_result(await session.call_tool(
            "create_reminder",
            {"params": {"title": original_title, "list_name": TARGET_LIST}},
        ))
        assert create_data["status"] == "success", create_data.get("error")
        reminder_id = create_data["reminder_id"]

        try:
            # Update title
            update_data = parse_result(await session.call_tool(
                "update_reminder",
                {"params": {"reminder_id": reminder_id, "title": updated_title}},
            ))
            assert update_data["status"] == "success", update_data.get("error")

            # Verify via get_reminder
            get_data = parse_result(await session.call_tool(
                "get_reminder",
                {"params": {"reminder_id": reminder_id}},
            ))
            assert get_data["title"] == updated_title
        finally:
            await session.call_tool(
                "delete_reminder",
                {"params": {"reminder_id": reminder_id}},
            )


@pytest.mark.asyncio
async def test_update_reminder_body_and_priority():
    """Update body and priority of an existing reminder."""
    async with mcp_session() as session:
        title = f"[TEST] UpdateBody {time.time():.0f}"

        create_data = parse_result(await session.call_tool(
            "create_reminder",
            {"params": {"title": title, "list_name": TARGET_LIST}},
        ))
        assert create_data["status"] == "success", create_data.get("error")
        reminder_id = create_data["reminder_id"]

        try:
            update_data = parse_result(await session.call_tool(
                "update_reminder",
                {
                    "params": {
                        "reminder_id": reminder_id,
                        "body": "Corpo aggiornato",
                        "priority": 5,
                    }
                },
            ))
            assert update_data["status"] == "success", update_data.get("error")

            get_data = parse_result(await session.call_tool(
                "get_reminder",
                {"params": {"reminder_id": reminder_id}},
            ))
            assert get_data["body"] == "Corpo aggiornato"
            assert get_data["priority"] == "5"
        finally:
            await session.call_tool(
                "delete_reminder",
                {"params": {"reminder_id": reminder_id}},
            )


@pytest.mark.asyncio
async def test_update_reminder_due_date():
    """Update the due date of a reminder."""
    async with mcp_session() as session:
        title = f"[TEST] UpdateDue {time.time():.0f}"

        create_data = parse_result(await session.call_tool(
            "create_reminder",
            {"params": {"title": title, "list_name": TARGET_LIST}},
        ))
        assert create_data["status"] == "success", create_data.get("error")
        reminder_id = create_data["reminder_id"]

        try:
            update_data = parse_result(await session.call_tool(
                "update_reminder",
                {
                    "params": {
                        "reminder_id": reminder_id,
                        "due_date": "2026-12-31",
                        "due_time": "09:00",
                    }
                },
            ))
            assert update_data["status"] == "success", update_data.get("error")

            get_data = parse_result(await session.call_tool(
                "get_reminder",
                {"params": {"reminder_id": reminder_id}},
            ))
            assert "2026" in get_data["due_date"]
            assert "December" in get_data["due_date"] or "31" in get_data["due_date"]
        finally:
            await session.call_tool(
                "delete_reminder",
                {"params": {"reminder_id": reminder_id}},
            )


@pytest.mark.asyncio
async def test_update_nonexistent_reminder():
    """Updating a non-existent ID returns a failed status."""
    async with mcp_session() as session:
        result = await session.call_tool(
            "update_reminder",
            {
                "params": {
                    "reminder_id": "x-apple-reminder://00000000-0000-0000-0000-000000000000",
                    "title": "Should fail",
                }
            },
        )
        data = parse_result(result)
        assert data["status"] == "failed"
