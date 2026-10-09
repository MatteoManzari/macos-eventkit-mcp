#!/usr/bin/env python3
"""
macOS Calendar MCP Server

A Model Context Protocol server for interacting with macOS Calendar app.
Uses PyObjC + EventKit for direct native access (no AppleScript).

Installation:
    pip install mcp pydantic pyobjc-framework-EventKit

Usage:
    python calendar_mcp.py
"""

import json
import platform
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Union

import EventKit
from Foundation import NSDate
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field


mcp = FastMCP("calendar_mcp")


class CalendarInput(BaseModel):
    """Shared validation rules for Calendar tool inputs."""

    model_config = ConfigDict(
        str_strip_whitespace=True, validate_assignment=True, extra="forbid"
    )


class ListEventsInput(CalendarInput):
    """Input for listing calendar events."""

    calendar_name: Optional[str] = Field(
        default=None,
        description="Name of the calendar to query. Omit or set to None for all calendars.",
    )
    start_date: str = Field(
        ...,
        description="Start of the date range in format YYYY-MM-DD (e.g., '2026-03-20')",
    )
    end_date: str = Field(
        ...,
        description="End of the date range in format YYYY-MM-DD (e.g., '2026-03-27')",
    )
    limit: Optional[int] = Field(
        default=50, description="Maximum number of events to return", ge=1, le=500
    )
    search_query: Optional[str] = Field(
        default=None,
        description="Optional text to search for (case-insensitive) in event title, notes, and location. Applied before limit.",
        max_length=500,
    )


class CreateEventInput(CalendarInput):
    """Input for creating a calendar event."""

    title: str = Field(
        ...,
        description="Title of the event (required)",
        min_length=1,
        max_length=500,
    )
    start_date: str = Field(
        ...,
        description="Start date in format YYYY-MM-DD (e.g., '2026-03-25')",
    )
    end_date: str = Field(
        ...,
        description="End date in format YYYY-MM-DD (e.g., '2026-03-25')",
    )
    start_time: Optional[str] = Field(
        default=None,
        description="Start time in HH:MM 24-hour format (e.g., '14:30'). Omit for all-day event.",
    )
    end_time: Optional[str] = Field(
        default=None,
        description="End time in HH:MM 24-hour format (e.g., '15:30'). Omit for all-day event.",
    )
    calendar_name: Optional[str] = Field(
        default=None,
        description="Name of the calendar to add the event to. Defaults to the system default calendar.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional notes or description for the event",
        max_length=2000,
    )
    location: Optional[str] = Field(
        default=None,
        description="Optional location for the event",
        max_length=500,
    )
    is_all_day: Optional[bool] = Field(
        default=False,
        description="If True, create an all-day event (start_time and end_time are ignored)",
    )
    alert_minutes_before: Optional[Union[int, list[int]]] = Field(
        default=None,
        description="Minutes before the event to trigger an alert. "
        "Pass a single integer (e.g. 15) or a list (e.g. [5, 30]) for multiple alerts.",
    )


class UpdateEventInput(CalendarInput):
    """Input for updating an existing calendar event."""

    event_id: str = Field(
        ..., description="Unique identifier of the event to update", min_length=1
    )
    title: Optional[str] = Field(
        default=None,
        description="New title for the event",
        min_length=1,
        max_length=500,
    )
    start_date: Optional[str] = Field(
        default=None,
        description="New start date in YYYY-MM-DD format",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="New end date in YYYY-MM-DD format",
    )
    start_time: Optional[str] = Field(
        default=None,
        description="New start time in HH:MM 24-hour format",
    )
    end_time: Optional[str] = Field(
        default=None,
        description="New end time in HH:MM 24-hour format",
    )
    notes: Optional[str] = Field(
        default=None,
        description="New notes/description for the event",
        max_length=2000,
    )
    location: Optional[str] = Field(
        default=None,
        description="New location for the event",
        max_length=500,
    )
    alert_minutes_before: Optional[Union[int, list[int]]] = Field(
        default=None,
        description="Minutes before the event to trigger an alert. "
        "Pass a single integer (e.g. 15) or a list (e.g. [5, 30]) for multiple alerts. "
        "Setting this replaces all existing alarms.",
    )


class DeleteEventInput(CalendarInput):
    """Input for deleting a calendar event."""

    event_id: str = Field(
        ..., description="Unique identifier of the event to delete", min_length=1
    )


class GetEventInput(CalendarInput):
    """Input for getting a single event by ID."""

    event_id: str = Field(
        ..., description="Unique identifier of the event to retrieve", min_length=1
    )


# ---------------------------------------------------------------------------
# EventKit backend
# ---------------------------------------------------------------------------

_store: Optional[EventKit.EKEventStore] = None
_store_lock = threading.Lock()
_macos_version = tuple(int(x) for x in platform.mac_ver()[0].split("."))


def _dbg(msg: str):
    """Print debug message to stderr (stdout is reserved for MCP JSON protocol)."""
    print(f"[DEBUG] {msg}", file=sys.stderr, flush=True)


def _get_store() -> EventKit.EKEventStore:
    """Return a lazily-initialized, authorized EKEventStore for Calendar access."""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store

        store = EventKit.EKEventStore.alloc().init()
        granted_event = threading.Event()
        result_holder: list = [False, None]  # [granted, error]

        def on_completion(granted, error):
            result_holder[0] = granted
            result_holder[1] = error
            granted_event.set()

        if _macos_version >= (14, 0):
            store.requestFullAccessToEventsWithCompletion_(on_completion)
        else:
            store.requestAccessToEntityType_completion_(
                EventKit.EKEntityTypeEvent, on_completion
            )

        if not granted_event.wait(timeout=30):
            raise TimeoutError("Timed out waiting for Calendar access authorization")

        if not result_holder[0]:
            err_msg = str(result_holder[1]) if result_holder[1] else "User denied access"
            raise RuntimeError(f"Calendar access not granted: {err_msg}")

        _store = store
        _dbg("EKEventStore initialized and authorized for Calendar")
        return _store


def _find_calendar(
    store: EventKit.EKEventStore, calendar_name: str
) -> Optional[EventKit.EKCalendar]:
    """Find an event calendar by name."""
    for cal in store.calendarsForEntityType_(EventKit.EKEntityTypeEvent):
        if cal.title() == calendar_name:
            return cal
    return None


def _parse_ns_date(date_str: str, time_str: Optional[str]) -> NSDate:
    """Build an NSDate from 'YYYY-MM-DD' and optional 'HH:MM'. Midnight if no time."""
    if time_str:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    else:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def _ns_date_to_str(ns_date) -> str:
    """Convert an NSDate to a human-readable string."""
    if ns_date is None:
        return ""
    ts = ns_date.timeIntervalSince1970()
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%A, %B %-d, %Y at %I:%M %p")


def _fetch_events(
    store: EventKit.EKEventStore,
    start_ns: NSDate,
    end_ns: NSDate,
    calendars: Optional[list] = None,
) -> list:
    """Fetch events synchronously via EventKit predicate."""
    t0 = time.time()
    store.refreshSourcesIfNecessary()
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
        start_ns, end_ns, calendars
    )
    events = store.eventsMatchingPredicate_(predicate)
    result = list(events) if events else []
    _dbg(f"fetch_events => {time.time()-t0:.2f}s ({len(result)} events)")
    return result


_ID_PREFIX = "x-apple-event://"


def _event_to_dict(event: EventKit.EKEvent) -> dict:
    """Convert an EKEvent to the output dict format."""
    raw_id = event.calendarItemIdentifier()
    event_id = f"{_ID_PREFIX}{raw_id}"

    alarms_list = []
    raw_alarms = event.alarms()
    if raw_alarms:
        for alarm in raw_alarms:
            offset = alarm.relativeOffset()
            alarms_list.append({"minutes_before": int(abs(offset) / 60)})

    return {
        "id": event_id,
        "title": str(event.title() or ""),
        "start": _ns_date_to_str(event.startDate()),
        "end": _ns_date_to_str(event.endDate()),
        "is_all_day": bool(event.isAllDay()),
        "location": str(event.location() or ""),
        "notes": str(event.notes() or ""),
        "calendar_name": str(event.calendar().title()),
        "alarms": alarms_list,
    }


def _parse_event_id(event_id: str) -> str:
    """Strip x-apple-event:// prefix if present, return plain identifier."""
    if event_id.startswith(_ID_PREFIX):
        return event_id[len(_ID_PREFIX):]
    return event_id


def _find_event_by_id(
    store: EventKit.EKEventStore, event_id: str
) -> Optional[EventKit.EKEvent]:
    """Look up a single EKEvent by its calendarItemIdentifier."""
    plain_id = _parse_event_id(event_id)
    item = store.calendarItemWithIdentifier_(plain_id)
    if item is None:
        return None
    if not isinstance(item, EventKit.EKEvent):
        return None
    return item


def _set_alarms(event: EventKit.EKEvent, minutes_before: Union[int, list[int]]) -> None:
    """Set alarms on an event, clearing any existing alarms first."""
    existing = event.alarms()
    if existing:
        for alarm in list(existing):
            event.removeAlarm_(alarm)

    if isinstance(minutes_before, int):
        minutes_before = [minutes_before]

    for minutes in minutes_before:
        alarm = EventKit.EKAlarm.alarmWithRelativeOffset_(-minutes * 60)
        event.addAlarm_(alarm)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="list_events",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def list_events(params: ListEventsInput) -> str:
    """
    List events from macOS Calendar app within a date range.

    Retrieves events from a specified calendar or all calendars.
    Returns events with id, title, start/end times, location, notes, and calendar name.

    Args:
        params (ListEventsInput): Parameters containing:
            - calendar_name (str): Name of the calendar to query (None for all)
            - start_date (str): Start of date range in YYYY-MM-DD format
            - end_date (str): End of date range in YYYY-MM-DD format
            - limit (int): Maximum number of events to return
    """
    try:
        store = _get_store()
        limit = params.limit or 50

        start_ns = _parse_ns_date(params.start_date, None)
        end_dt = datetime.strptime(params.end_date, "%Y-%m-%d") + timedelta(days=1)
        end_ns = NSDate.dateWithTimeIntervalSince1970_(end_dt.timestamp())

        calendars = None
        label = "All Calendars"
        if params.calendar_name:
            cal = _find_calendar(store, params.calendar_name)
            if cal is None:
                return json.dumps(
                    {
                        "calendar_name": params.calendar_name,
                        "count": 0,
                        "events": [],
                        "message": f"Calendar '{params.calendar_name}' not found",
                    },
                    indent=2,
                )
            calendars = [cal]
            label = params.calendar_name

        ek_events = _fetch_events(store, start_ns, end_ns, calendars)
        # Sort by start date
        ek_events.sort(key=lambda e: e.startDate().timeIntervalSince1970())
        all_events = [_event_to_dict(e) for e in ek_events]

        if params.search_query:
            q = params.search_query.lower()
            all_events = [
                e for e in all_events
                if q in e["title"].lower()
                or q in e["notes"].lower()
                or q in e["location"].lower()
            ]

        all_events = all_events[:limit]

        return json.dumps(
            {
                "calendar_name": label,
                "start_date": params.start_date,
                "end_date": params.end_date,
                "count": len(all_events),
                "events": all_events,
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"error": str(e), "status": "failed"}, indent=2)


@mcp.tool(
    name="create_event",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def create_event(params: CreateEventInput) -> str:
    """
    Create a new event in macOS Calendar app.

    Creates a new event with title, start/end date and time, optional notes,
    location, and target calendar. Returns confirmation with the created event's ID.

    Args:
        params (CreateEventInput): Parameters containing:
            - title (str): Title of the event (required)
            - start_date (str): Start date in YYYY-MM-DD format (required)
            - end_date (str): End date in YYYY-MM-DD format (required)
            - start_time (str): Start time in HH:MM format (omit for all-day)
            - end_time (str): End time in HH:MM format (omit for all-day)
            - calendar_name (str): Target calendar name (default: system default)
            - notes (str): Optional notes/description
            - location (str): Optional location
            - is_all_day (bool): If True, creates an all-day event
    """
    try:
        store = _get_store()
        event = EventKit.EKEvent.eventWithEventStore_(store)
        event.setTitle_(params.title)

        all_day = params.is_all_day or False
        event.setAllDay_(all_day)

        if all_day:
            start_ns = _parse_ns_date(params.start_date, None)
            end_ns = _parse_ns_date(params.end_date, None)
        else:
            start_ns = _parse_ns_date(params.start_date, params.start_time or "09:00")
            end_ns = _parse_ns_date(params.end_date, params.end_time or "10:00")

        event.setStartDate_(start_ns)
        event.setEndDate_(end_ns)

        if params.notes:
            event.setNotes_(params.notes)
        if params.location:
            event.setLocation_(params.location)

        cal = None
        if params.calendar_name:
            cal = _find_calendar(store, params.calendar_name)
        if cal is None:
            cal = store.defaultCalendarForNewEvents()
        event.setCalendar_(cal)

        if params.alert_minutes_before is not None:
            _set_alarms(event, params.alert_minutes_before)

        success, error = store.saveEvent_span_commit_error_(
            event, EventKit.EKSpanThisEvent, True, None
        )
        if not success or error is not None:
            raise RuntimeError(f"Failed to save event: {error}")

        event_id = f"{_ID_PREFIX}{event.calendarItemIdentifier()}"

        return json.dumps(
            {
                "status": "success",
                "message": f"Event '{params.title}' created successfully",
                "event_id": event_id,
                "title": params.title,
                "calendar": cal.title(),
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, indent=2)


@mcp.tool(
    name="update_event",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def update_event(params: UpdateEventInput) -> str:
    """
    Update an existing event in macOS Calendar app.

    Modifies the specified event's fields. Only provided fields are updated;
    omitted fields remain unchanged.

    Args:
        params (UpdateEventInput): Parameters containing:
            - event_id (str): The event ID to update (required)
            - title (str): New title (optional)
            - start_date (str): New start date in YYYY-MM-DD format (optional)
            - end_date (str): New end date in YYYY-MM-DD format (optional)
            - start_time (str): New start time in HH:MM format (optional)
            - end_time (str): New end time in HH:MM format (optional)
            - notes (str): New notes/description (optional)
            - location (str): New location (optional)
    """
    try:
        store = _get_store()
        event = _find_event_by_id(store, params.event_id)

        if event is None:
            return json.dumps(
                {
                    "status": "failed",
                    "error": f"Event with ID {params.event_id} not found",
                },
                indent=2,
            )

        if params.title is not None:
            event.setTitle_(params.title)
        if params.notes is not None:
            event.setNotes_(params.notes)
        if params.location is not None:
            event.setLocation_(params.location)

        # Update dates if any date/time field is provided
        if any(
            f is not None
            for f in [params.start_date, params.start_time, params.end_date, params.end_time]
        ):
            # Resolve current values as fallback for partial updates
            current_start_ts = event.startDate().timeIntervalSince1970()
            current_end_ts = event.endDate().timeIntervalSince1970()
            current_start_dt = datetime.fromtimestamp(current_start_ts)
            current_end_dt = datetime.fromtimestamp(current_end_ts)

            start_date = params.start_date or current_start_dt.strftime("%Y-%m-%d")
            start_time = params.start_time or current_start_dt.strftime("%H:%M")
            end_date = params.end_date or current_end_dt.strftime("%Y-%m-%d")
            end_time = params.end_time or current_end_dt.strftime("%H:%M")

            event.setStartDate_(_parse_ns_date(start_date, start_time))
            event.setEndDate_(_parse_ns_date(end_date, end_time))

        if params.alert_minutes_before is not None:
            _set_alarms(event, params.alert_minutes_before)

        success, error = store.saveEvent_span_commit_error_(
            event, EventKit.EKSpanThisEvent, True, None
        )
        if not success or error is not None:
            raise RuntimeError(f"Failed to update event: {error}")

        return json.dumps(
            {
                "status": "success",
                "message": "Event updated successfully",
                "event_id": params.event_id,
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, indent=2)


@mcp.tool(
    name="delete_event",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def delete_event(params: DeleteEventInput) -> str:
    """
    Delete an event from macOS Calendar app.

    WARNING: This operation is destructive and cannot be undone.
    Permanently removes the specified event.

    Args:
        params (DeleteEventInput): Parameters containing:
            - event_id (str): The event ID to delete
    """
    try:
        store = _get_store()
        event = _find_event_by_id(store, params.event_id)

        if event is None:
            return json.dumps(
                {
                    "status": "failed",
                    "error": f"Event with ID {params.event_id} not found",
                },
                indent=2,
            )

        success, error = store.removeEvent_span_commit_error_(
            event, EventKit.EKSpanThisEvent, True, None
        )
        if not success or error is not None:
            raise RuntimeError(f"Failed to delete event: {error}")

        return json.dumps(
            {
                "status": "success",
                "message": "Event deleted successfully",
                "event_id": params.event_id,
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, indent=2)


@mcp.tool(
    name="list_calendars",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def list_calendars() -> str:
    """
    List all available calendars in macOS Calendar app.

    Returns the names and source accounts of all calendars. Useful for discovery
    before calling list_events or create_event with a specific calendar_name.
    """
    try:
        store = _get_store()
        calendars = store.calendarsForEntityType_(EventKit.EKEntityTypeEvent)

        result = []
        for cal in calendars:
            result.append({
                "name": str(cal.title()),
                "source": str(cal.source().title()) if cal.source() else "",
            })

        result.sort(key=lambda x: (x["source"], x["name"]))

        return json.dumps(
            {"count": len(result), "calendars": result},
            indent=2,
        )

    except Exception as e:
        return json.dumps({"error": str(e), "status": "failed"}, indent=2)


@mcp.tool(
    name="get_event",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def get_event(params: GetEventInput) -> str:
    """
    Get a single calendar event by its ID.

    Retrieves full details of one event including id, title, start/end times,
    all-day flag, location, notes, and calendar name.

    Args:
        params (GetEventInput): Parameters containing:
            - event_id (str): The event ID to retrieve
    """
    try:
        store = _get_store()
        event = _find_event_by_id(store, params.event_id)

        if event is None:
            return json.dumps(
                {
                    "status": "failed",
                    "error": f"Event with ID {params.event_id} not found",
                },
                indent=2,
            )

        return json.dumps(_event_to_dict(event), indent=2)

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, indent=2)


if __name__ == "__main__":
    mcp.run()
