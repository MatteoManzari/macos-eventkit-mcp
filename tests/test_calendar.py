"""
Integration tests for calendar_mcp.py.

These tests launch the real MCP server and interact with the actual macOS Calendar app.
They create/update/delete real events (prefixed with [TEST]).

Set TEST_CALENDAR_NAME to a writable calendar before running these tests.
NOTE: macOS Calendar access must be granted to Python/Terminal in
      System Settings > Privacy & Security > Calendars before running.
"""
import os
import time
import pytest
from conftest import mcp_calendar_session, parse_result

TARGET_CALENDAR = os.getenv("TEST_CALENDAR_NAME", "")
pytestmark = pytest.mark.skipif(
    not TARGET_CALENDAR, reason="Set TEST_CALENDAR_NAME to a writable calendar"
)

# Date range used for listing tests.
START_DATE = "2026-03-20"
END_DATE = "2026-03-27"

PERF_THRESHOLD_SECONDS = 15.0


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_available():
    async with mcp_calendar_session() as session:
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert "list_events" in names
        assert "list_calendars" in names
        assert "create_event" in names
        assert "update_event" in names
        assert "delete_event" in names
        assert "get_event" in names


# ---------------------------------------------------------------------------
# list_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_events_all_calendars():
    async with mcp_calendar_session() as session:
        t0 = time.time()
        result = await session.call_tool(
            "list_events",
            {"params": {"start_date": START_DATE, "end_date": END_DATE}},
        )
        elapsed = time.time() - t0
        data = parse_result(result)

        assert "events" in data
        assert "count" in data
        assert isinstance(data["events"], list)
        assert elapsed < PERF_THRESHOLD_SECONDS, f"list_events took {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_list_events_specific_calendar():
    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "list_events",
            {
                "params": {
                    "start_date": START_DATE,
                    "end_date": END_DATE,
                    "calendar_name": TARGET_CALENDAR,
                }
            },
        )
        data = parse_result(result)
        assert "events" in data
        for event in data["events"]:
            assert event["calendar_name"] == TARGET_CALENDAR


@pytest.mark.asyncio
async def test_list_events_limit():
    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "list_events",
            {"params": {"start_date": START_DATE, "end_date": END_DATE, "limit": 2}},
        )
        data = parse_result(result)
        assert len(data["events"]) <= 2


@pytest.mark.asyncio
async def test_list_events_unknown_calendar():
    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "list_events",
            {
                "params": {
                    "start_date": START_DATE,
                    "end_date": END_DATE,
                    "calendar_name": "ThisCalendarDefinitelyDoesNotExist_XYZ",
                }
            },
        )
        data = parse_result(result)
        assert data["count"] == 0
        assert data["events"] == []


# ---------------------------------------------------------------------------
# create / update / delete flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_all_day_event():
    ts = int(time.time())
    title = f"[TEST] All-day {ts}"

    async with mcp_calendar_session() as session:
        # Create
        result = await session.call_tool(
            "create_event",
            {
                "params": {
                    "title": title,
                    "start_date": "2026-03-25",
                    "end_date": "2026-03-25",
                    "calendar_name": TARGET_CALENDAR,
                    "is_all_day": True,
                }
            },
        )
        data = parse_result(result)
        assert data["status"] == "success", data
        event_id = data["event_id"]
        assert event_id.startswith("x-apple-event://")

        # Verify appears in listing
        list_result = await session.call_tool(
            "list_events",
            {
                "params": {
                    "start_date": "2026-03-25",
                    "end_date": "2026-03-25",
                    "calendar_name": TARGET_CALENDAR,
                }
            },
        )
        list_data = parse_result(list_result)
        ids = [e["id"] for e in list_data["events"]]
        assert event_id in ids

        # Cleanup
        del_result = await session.call_tool(
            "delete_event", {"params": {"event_id": event_id}}
        )
        assert parse_result(del_result)["status"] == "success"


@pytest.mark.asyncio
async def test_create_timed_event():
    ts = int(time.time())
    title = f"[TEST] Timed {ts}"

    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "create_event",
            {
                "params": {
                    "title": title,
                    "start_date": "2026-03-26",
                    "end_date": "2026-03-26",
                    "start_time": "10:00",
                    "end_time": "11:00",
                    "calendar_name": TARGET_CALENDAR,
                }
            },
        )
        data = parse_result(result)
        assert data["status"] == "success", data
        event_id = data["event_id"]

        # Cleanup
        del_result = await session.call_tool(
            "delete_event", {"params": {"event_id": event_id}}
        )
        assert parse_result(del_result)["status"] == "success"


@pytest.mark.asyncio
async def test_create_event_with_notes_and_location():
    ts = int(time.time())
    title = f"[TEST] Notes+Loc {ts}"

    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "create_event",
            {
                "params": {
                    "title": title,
                    "start_date": "2026-03-26",
                    "end_date": "2026-03-26",
                    "start_time": "14:00",
                    "end_time": "15:00",
                    "calendar_name": TARGET_CALENDAR,
                    "notes": "Test notes content",
                    "location": "Test Location, Milan",
                }
            },
        )
        data = parse_result(result)
        assert data["status"] == "success", data
        event_id = data["event_id"]

        # Cleanup
        await session.call_tool("delete_event", {"params": {"event_id": event_id}})


@pytest.mark.asyncio
async def test_update_event_title():
    ts = int(time.time())
    original_title = f"[TEST] Update-before {ts}"
    updated_title = f"[TEST] Update-after {ts}"

    async with mcp_calendar_session() as session:
        # Create
        create_result = await session.call_tool(
            "create_event",
            {
                "params": {
                    "title": original_title,
                    "start_date": "2026-03-26",
                    "end_date": "2026-03-26",
                    "start_time": "16:00",
                    "end_time": "17:00",
                    "calendar_name": TARGET_CALENDAR,
                }
            },
        )
        create_data = parse_result(create_result)
        assert create_data["status"] == "success", create_data
        event_id = create_data["event_id"]

        # Update title
        update_result = await session.call_tool(
            "update_event",
            {"params": {"event_id": event_id, "title": updated_title}},
        )
        update_data = parse_result(update_result)
        assert update_data["status"] == "success", update_data

        # Cleanup
        await session.call_tool("delete_event", {"params": {"event_id": event_id}})


@pytest.mark.asyncio
async def test_delete_event():
    ts = int(time.time())
    title = f"[TEST] Delete {ts}"

    async with mcp_calendar_session() as session:
        # Create
        create_result = await session.call_tool(
            "create_event",
            {
                "params": {
                    "title": title,
                    "start_date": "2026-03-26",
                    "end_date": "2026-03-26",
                    "start_time": "18:00",
                    "end_time": "19:00",
                    "calendar_name": TARGET_CALENDAR,
                }
            },
        )
        event_id = parse_result(create_result)["event_id"]

        # Delete
        del_result = await session.call_tool(
            "delete_event", {"params": {"event_id": event_id}}
        )
        assert parse_result(del_result)["status"] == "success"

        # Verify gone
        list_result = await session.call_tool(
            "list_events",
            {
                "params": {
                    "start_date": "2026-03-26",
                    "end_date": "2026-03-26",
                    "calendar_name": TARGET_CALENDAR,
                }
            },
        )
        ids = [e["id"] for e in parse_result(list_result)["events"]]
        assert event_id not in ids


@pytest.mark.asyncio
async def test_delete_nonexistent_event():
    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "delete_event",
            {"params": {"event_id": "x-apple-event://00000000-0000-0000-0000-000000000000"}},
        )
        data = parse_result(result)
        assert data["status"] == "failed"


@pytest.mark.asyncio
async def test_update_nonexistent_event():
    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "update_event",
            {
                "params": {
                    "event_id": "x-apple-event://00000000-0000-0000-0000-000000000000",
                    "title": "Should fail",
                }
            },
        )
        data = parse_result(result)
        assert data["status"] == "failed"


# ---------------------------------------------------------------------------
# list_calendars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_calendars():
    async with mcp_calendar_session() as session:
        result = await session.call_tool("list_calendars", {})
        data = parse_result(result)
        assert "error" not in data, data.get("error")
        assert "calendars" in data
        assert "count" in data
        assert isinstance(data["calendars"], list)
        assert data["count"] == len(data["calendars"])

        # TARGET_CALENDAR must appear
        names = [c["name"] for c in data["calendars"]]
        assert TARGET_CALENDAR in names, f"'{TARGET_CALENDAR}' not found in {names}"

        for cal in data["calendars"]:
            assert "name" in cal
            assert "source" in cal


# ---------------------------------------------------------------------------
# search_query in list_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_events_search_query():
    """search_query filters events by title match."""
    ts = int(time.time())
    unique_token = f"SEARCHTEST{ts}"
    title = f"[TEST] {unique_token}"

    async with mcp_calendar_session() as session:
        # Create an event with a unique title
        create_result = await session.call_tool(
            "create_event",
            {
                "params": {
                    "title": title,
                    "start_date": "2026-03-26",
                    "end_date": "2026-03-26",
                    "start_time": "08:00",
                    "end_time": "09:00",
                    "calendar_name": TARGET_CALENDAR,
                }
            },
        )
        create_data = parse_result(create_result)
        assert create_data["status"] == "success", create_data
        event_id = create_data["event_id"]

        try:
            # Search with the unique token — should find this event
            search_result = await session.call_tool(
                "list_events",
                {
                    "params": {
                        "start_date": "2026-03-26",
                        "end_date": "2026-03-26",
                        "search_query": unique_token,
                    }
                },
            )
            search_data = parse_result(search_result)
            ids = [e["id"] for e in search_data["events"]]
            assert event_id in ids, f"Event not found by search_query. Got: {ids}"

            # Garbage query — no results
            no_result = await session.call_tool(
                "list_events",
                {
                    "params": {
                        "start_date": "2026-03-26",
                        "end_date": "2026-03-26",
                        "search_query": "XYZZY_NOMATCH_99999",
                    }
                },
            )
            no_data = parse_result(no_result)
            assert no_data["count"] == 0
        finally:
            await session.call_tool("delete_event", {"params": {"event_id": event_id}})


# ---------------------------------------------------------------------------
# get_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_event():
    """get_event returns full details for a known event ID."""
    ts = int(time.time())
    title = f"[TEST] GetById {ts}"

    async with mcp_calendar_session() as session:
        # Create
        create_result = await session.call_tool(
            "create_event",
            {
                "params": {
                    "title": title,
                    "start_date": "2026-03-26",
                    "end_date": "2026-03-26",
                    "start_time": "07:00",
                    "end_time": "08:00",
                    "calendar_name": TARGET_CALENDAR,
                    "notes": "Note di test",
                    "location": "Test location",
                }
            },
        )
        create_data = parse_result(create_result)
        assert create_data["status"] == "success", create_data
        event_id = create_data["event_id"]

        try:
            get_result = await session.call_tool(
                "get_event",
                {"params": {"event_id": event_id}},
            )
            get_data = parse_result(get_result)
            assert "error" not in get_data, get_data.get("error")
            assert get_data["id"] == event_id
            assert get_data["title"] == title
            assert "start" in get_data
            assert "end" in get_data
            assert "is_all_day" in get_data
            assert "location" in get_data
            assert "notes" in get_data
            assert "calendar_name" in get_data
            assert get_data["calendar_name"] == TARGET_CALENDAR
        finally:
            await session.call_tool("delete_event", {"params": {"event_id": event_id}})


@pytest.mark.asyncio
async def test_create_event_with_single_alarm():
    """Create an event with a single alarm and verify it via get_event."""
    ts = int(time.time())
    title = f"[TEST] SingleAlarm {ts}"

    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "create_event",
            {
                "params": {
                    "title": title,
                    "start_date": "2026-03-26",
                    "end_date": "2026-03-26",
                    "start_time": "10:00",
                    "end_time": "11:00",
                    "calendar_name": TARGET_CALENDAR,
                    "alert_minutes_before": 15,
                }
            },
        )
        data = parse_result(result)
        assert data["status"] == "success", data
        event_id = data["event_id"]

        try:
            get_result = await session.call_tool(
                "get_event", {"params": {"event_id": event_id}}
            )
            get_data = parse_result(get_result)
            assert "alarms" in get_data
            assert len(get_data["alarms"]) == 1
            assert get_data["alarms"][0]["minutes_before"] == 15
        finally:
            await session.call_tool("delete_event", {"params": {"event_id": event_id}})


@pytest.mark.asyncio
async def test_create_event_with_multiple_alarms():
    """Create an event with multiple alarms and verify all are present."""
    ts = int(time.time())
    title = f"[TEST] MultiAlarm {ts}"

    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "create_event",
            {
                "params": {
                    "title": title,
                    "start_date": "2026-03-26",
                    "end_date": "2026-03-26",
                    "start_time": "12:00",
                    "end_time": "13:00",
                    "calendar_name": TARGET_CALENDAR,
                    "alert_minutes_before": [5, 30, 60],
                }
            },
        )
        data = parse_result(result)
        assert data["status"] == "success", data
        event_id = data["event_id"]

        try:
            get_result = await session.call_tool(
                "get_event", {"params": {"event_id": event_id}}
            )
            get_data = parse_result(get_result)
            assert "alarms" in get_data
            assert len(get_data["alarms"]) == 3
            alarm_minutes = sorted(a["minutes_before"] for a in get_data["alarms"])
            assert alarm_minutes == [5, 30, 60]
        finally:
            await session.call_tool("delete_event", {"params": {"event_id": event_id}})


@pytest.mark.asyncio
async def test_update_event_alarms():
    """Create event with one alarm, update to different alarms, verify replacement."""
    ts = int(time.time())
    title = f"[TEST] UpdateAlarm {ts}"

    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "create_event",
            {
                "params": {
                    "title": title,
                    "start_date": "2026-03-26",
                    "end_date": "2026-03-26",
                    "start_time": "14:00",
                    "end_time": "15:00",
                    "calendar_name": TARGET_CALENDAR,
                    "alert_minutes_before": 10,
                }
            },
        )
        data = parse_result(result)
        assert data["status"] == "success", data
        event_id = data["event_id"]

        try:
            update_result = await session.call_tool(
                "update_event",
                {"params": {"event_id": event_id, "alert_minutes_before": [5, 20]}},
            )
            assert parse_result(update_result)["status"] == "success"

            get_result = await session.call_tool(
                "get_event", {"params": {"event_id": event_id}}
            )
            get_data = parse_result(get_result)
            assert len(get_data["alarms"]) == 2
            alarm_minutes = sorted(a["minutes_before"] for a in get_data["alarms"])
            assert alarm_minutes == [5, 20]
        finally:
            await session.call_tool("delete_event", {"params": {"event_id": event_id}})


@pytest.mark.asyncio
async def test_get_event_nonexistent():
    """get_event with a non-existent ID returns failed status."""
    async with mcp_calendar_session() as session:
        result = await session.call_tool(
            "get_event",
            {"params": {"event_id": "x-apple-event://00000000-0000-0000-0000-000000000000"}},
        )
        data = parse_result(result)
        assert data["status"] == "failed"
