#!/usr/bin/env python3
"""
macOS Reminders MCP Server

A Model Context Protocol server for interacting with macOS Reminders app.
Uses PyObjC + EventKit for direct native access (no AppleScript).

Installation:
    pip install mcp pydantic pyobjc-framework-EventKit

Usage:
    python reminders_mcp.py
"""

import json
import platform
import sys
import threading
import time
from datetime import datetime
from typing import Optional

import EventKit
from Foundation import NSCalendar, NSDateComponents, NSCalendarIdentifierGregorian
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field


mcp = FastMCP("reminders_mcp")


class ReminderInput(BaseModel):
    """Shared validation rules for Reminders tool inputs."""

    model_config = ConfigDict(
        str_strip_whitespace=True, validate_assignment=True, extra="forbid"
    )


class ListRemindersInput(ReminderInput):
    """Input for listing reminders."""

    list_name: Optional[str] = Field(
        default="All Reminders",
        description="Name of the reminder list to query. Use 'All Reminders' for all lists.",
    )
    limit: Optional[int] = Field(
        default=50, description="Maximum number of reminders to return", ge=1, le=500
    )
    include_completed: Optional[bool] = Field(
        default=False, description="Whether to include completed reminders"
    )
    search_query: Optional[str] = Field(
        default=None,
        description="Optional text to search for (case-insensitive) in reminder title and notes. Applied before limit.",
        max_length=500,
    )


class CreateReminderInput(ReminderInput):
    """Input for creating a reminder."""

    title: str = Field(
        ...,
        description="Title of the reminder (required)",
        min_length=1,
        max_length=500,
    )
    body: Optional[str] = Field(
        default=None,
        description="Optional description or details for the reminder",
        max_length=2000,
    )
    due_date: Optional[str] = Field(
        default=None, description="Due date in format YYYY-MM-DD (e.g., '2026-03-25')"
    )
    due_time: Optional[str] = Field(
        default=None,
        description="Due time in format HH:MM (24-hour format, e.g., '14:30')",
    )
    list_name: Optional[str] = Field(
        default="Reminders",
        description="Name of the list to add the reminder to (default: 'Reminders')",
    )
    priority: Optional[int] = Field(
        default=0,
        description="Priority level: 0 (none), 1 (low), 5 (medium), 9 (high)",
        ge=0,
        le=9,
    )


class CompleteReminderInput(ReminderInput):
    """Input for marking a reminder as complete."""

    reminder_id: str = Field(
        ..., description="Unique identifier of the reminder to complete", min_length=1
    )


class DeleteReminderInput(ReminderInput):
    """Input for deleting a reminder."""

    reminder_id: str = Field(
        ..., description="Unique identifier of the reminder to delete", min_length=1
    )


class GetReminderInput(ReminderInput):
    """Input for getting a single reminder by ID."""

    reminder_id: str = Field(
        ..., description="Unique identifier of the reminder to retrieve", min_length=1
    )


class UpdateReminderInput(ReminderInput):
    """Input for updating an existing reminder."""

    reminder_id: str = Field(
        ..., description="Unique identifier of the reminder to update", min_length=1
    )
    title: Optional[str] = Field(
        default=None,
        description="New title for the reminder",
        min_length=1,
        max_length=500,
    )
    body: Optional[str] = Field(
        default=None,
        description="New description/notes for the reminder",
        max_length=2000,
    )
    due_date: Optional[str] = Field(
        default=None, description="New due date in format YYYY-MM-DD (e.g., '2026-03-25')"
    )
    due_time: Optional[str] = Field(
        default=None,
        description="New due time in format HH:MM (24-hour format, e.g., '14:30')",
    )
    priority: Optional[int] = Field(
        default=None,
        description="New priority level: 0 (none), 1 (low), 5 (medium), 9 (high)",
        ge=0,
        le=9,
    )
    list_name: Optional[str] = Field(
        default=None,
        description="Move reminder to this list name",
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
    """Return a lazily-initialized, authorized EKEventStore."""
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
            store.requestFullAccessToRemindersWithCompletion_(on_completion)
        else:
            store.requestAccessToEntityType_completion_(
                EventKit.EKEntityTypeReminder, on_completion
            )

        if not granted_event.wait(timeout=30):
            raise TimeoutError("Timed out waiting for Reminders access authorization")

        if not result_holder[0]:
            err_msg = str(result_holder[1]) if result_holder[1] else "User denied access"
            raise RuntimeError(f"Reminders access not granted: {err_msg}")

        _store = store
        _dbg("EKEventStore initialized and authorized")
        return _store


def _find_calendar(
    store: EventKit.EKEventStore, list_name: str
) -> Optional[EventKit.EKCalendar]:
    """Find a Reminders calendar by name."""
    for cal in store.calendarsForEntityType_(EventKit.EKEntityTypeReminder):
        if cal.title() == list_name:
            return cal
    return None


def _fetch_reminders(
    store: EventKit.EKEventStore,
    calendars: Optional[list] = None,
    include_completed: bool = False,
) -> list:
    """Fetch reminders via EventKit predicate. calendars=None means all."""
    t0 = time.time()
    predicate = store.predicateForRemindersInCalendars_(calendars)

    fetch_event = threading.Event()
    result_holder: list = [None]

    def on_fetch(reminders):
        result_holder[0] = reminders
        fetch_event.set()

    store.fetchRemindersMatchingPredicate_completion_(predicate, on_fetch)
    if not fetch_event.wait(timeout=60):
        raise TimeoutError("Timed out fetching reminders")

    reminders = result_holder[0]
    if reminders is None:
        _dbg(f"fetch_reminders => {time.time()-t0:.2f}s (no results)")
        return []

    if not include_completed:
        reminders = [r for r in reminders if not r.isCompleted()]

    _dbg(f"fetch_reminders => {time.time()-t0:.2f}s ({len(reminders)} reminders)")
    return list(reminders)


_ID_PREFIX = "x-apple-reminder://"


def _reminder_to_dict(reminder: EventKit.EKReminder) -> dict:
    """Convert an EKReminder to the output dict format."""
    raw_id = reminder.calendarItemIdentifier()
    reminder_id = f"{_ID_PREFIX}{raw_id}"

    due_date = ""
    comps = reminder.dueDateComponents()
    if comps is not None:
        cal = NSCalendar.alloc().initWithCalendarIdentifier_(
            NSCalendarIdentifierGregorian
        )
        nsdate = cal.dateFromComponents_(comps)
        if nsdate is not None:
            ts = nsdate.timeIntervalSince1970()
            dt = datetime.fromtimestamp(ts)
            due_date = dt.strftime("%A, %B %-d, %Y at %I:%M:%S %p")

    return {
        "id": reminder_id,
        "title": str(reminder.title() or ""),
        "body": str(reminder.notes() or ""),
        "priority": str(reminder.priority()),
        "completed": str(reminder.isCompleted()).lower(),
        "due_date": due_date,
        "list_name": str(reminder.calendar().title()),
    }


def _parse_reminder_id(reminder_id: str) -> str:
    """Strip x-apple-reminder:// prefix if present, return plain UUID."""
    if reminder_id.startswith(_ID_PREFIX):
        return reminder_id[len(_ID_PREFIX) :]
    return reminder_id


def _find_reminder_by_id(
    store: EventKit.EKEventStore, reminder_id: str
) -> Optional[EventKit.EKReminder]:
    """Look up a single EKReminder by its calendarItemIdentifier."""
    plain_id = _parse_reminder_id(reminder_id)
    item = store.calendarItemWithIdentifier_(plain_id)
    if item is None:
        return None
    if not isinstance(item, EventKit.EKReminder):
        return None
    return item


def _make_due_date_components(due_date: str, due_time: Optional[str]) -> NSDateComponents:
    """Build NSDateComponents from 'YYYY-MM-DD' and optional 'HH:MM'."""
    parts = due_date.split("-")
    comps = NSDateComponents.alloc().init()
    comps.setYear_(int(parts[0]))
    comps.setMonth_(int(parts[1]))
    comps.setDay_(int(parts[2]))
    if due_time:
        time_parts = due_time.split(":")
        comps.setHour_(int(time_parts[0]))
        comps.setMinute_(int(time_parts[1]))
    else:
        comps.setHour_(9)
        comps.setMinute_(0)
    return comps


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="list_reminders",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def list_reminders(params: ListRemindersInput) -> str:
    """
    List reminders from macOS Reminders app.

    Retrieves reminders from a specified list or all lists.
    Returns reminders with id, title, body, due date, priority, completed status, and list name.

    Args:
        params (ListRemindersInput): Parameters containing:
            - list_name (str): Name of the reminder list to query ('All Reminders' for all)
            - limit (int): Maximum number of reminders to return
            - include_completed (bool): Whether to include completed reminders
    """
    try:
        store = _get_store()
        list_name = params.list_name or "All Reminders"
        limit = params.limit or 50
        include_completed = params.include_completed or False

        calendars = None
        if list_name != "All Reminders":
            cal = _find_calendar(store, list_name)
            if cal is None:
                return json.dumps(
                    {
                        "list_name": list_name,
                        "count": 0,
                        "reminders": [],
                        "message": f"No reminders found in '{list_name}'",
                    },
                    indent=2,
                )
            calendars = [cal]

        ek_reminders = _fetch_reminders(store, calendars, include_completed)
        all_reminders = [_reminder_to_dict(r) for r in ek_reminders]

        if params.search_query:
            q = params.search_query.lower()
            all_reminders = [
                r for r in all_reminders
                if q in r["title"].lower() or q in r["body"].lower()
            ]

        all_reminders = all_reminders[:limit]

        if not all_reminders:
            return json.dumps(
                {
                    "list_name": list_name,
                    "count": 0,
                    "reminders": [],
                    "message": f"No reminders found in '{list_name}'",
                },
                indent=2,
            )

        return json.dumps(
            {
                "list_name": list_name,
                "count": len(all_reminders),
                "reminders": all_reminders,
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"error": str(e), "status": "failed"}, indent=2)


@mcp.tool(
    name="create_reminder",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def create_reminder(params: CreateReminderInput) -> str:
    """
    Create a new reminder in macOS Reminders app.

    Creates a new reminder with title, optional description, due date/time, priority,
    and target list. Returns confirmation with the created reminder's ID.

    Args:
        params (CreateReminderInput): Parameters containing:
            - title (str): Title of the reminder (required)
            - body (str): Optional description
            - due_date (str): Optional due date in YYYY-MM-DD format
            - due_time (str): Optional due time in HH:MM format
            - list_name (str): Target reminder list name
            - priority (int): Priority level (0=none, 1=low, 5=medium, 9=high)
    """
    try:
        store = _get_store()
        reminder = EventKit.EKReminder.reminderWithEventStore_(store)
        reminder.setTitle_(params.title)
        if params.body:
            reminder.setNotes_(params.body)
        reminder.setPriority_(params.priority or 0)

        cal = _find_calendar(store, params.list_name or "Reminders")
        if cal is None:
            cal = store.defaultCalendarForNewReminders()
        reminder.setCalendar_(cal)

        if params.due_date:
            comps = _make_due_date_components(params.due_date, params.due_time)
            reminder.setDueDateComponents_(comps)

        success, error = store.saveReminder_commit_error_(reminder, True, None)
        if not success or error is not None:
            raise RuntimeError(f"Failed to save reminder: {error}")

        reminder_id = f"{_ID_PREFIX}{reminder.calendarItemIdentifier()}"

        return json.dumps(
            {
                "status": "success",
                "message": f"Reminder '{params.title}' created successfully",
                "reminder_id": reminder_id,
                "title": params.title,
                "list": params.list_name,
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, indent=2)


@mcp.tool(
    name="complete_reminder",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def complete_reminder(params: CompleteReminderInput) -> str:
    """
    Mark a reminder as completed.

    Marks the specified reminder as done in the macOS Reminders app.

    Args:
        params (CompleteReminderInput): Parameters containing:
            - reminder_id (str): The reminder ID to mark as complete
    """
    try:
        store = _get_store()
        reminder = _find_reminder_by_id(store, params.reminder_id)

        if reminder is None:
            return json.dumps(
                {
                    "status": "failed",
                    "error": f"Reminder with ID {params.reminder_id} not found",
                },
                indent=2,
            )

        reminder.setCompleted_(True)
        success, error = store.saveReminder_commit_error_(reminder, True, None)
        if not success or error is not None:
            raise RuntimeError(f"Failed to complete reminder: {error}")

        return json.dumps(
            {
                "status": "success",
                "message": "Reminder marked as completed",
                "reminder_id": params.reminder_id,
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, indent=2)


@mcp.tool(
    name="delete_reminder",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def delete_reminder(params: DeleteReminderInput) -> str:
    """
    Delete a reminder from macOS Reminders app.

    WARNING: This operation is destructive and cannot be undone.
    Permanently removes the specified reminder.

    Args:
        params (DeleteReminderInput): Parameters containing:
            - reminder_id (str): The reminder ID to delete
    """
    try:
        store = _get_store()
        reminder = _find_reminder_by_id(store, params.reminder_id)

        if reminder is None:
            return json.dumps(
                {
                    "status": "failed",
                    "error": f"Reminder with ID {params.reminder_id} not found",
                },
                indent=2,
            )

        success, error = store.removeReminder_commit_error_(reminder, True, None)
        if not success or error is not None:
            raise RuntimeError(f"Failed to delete reminder: {error}")

        return json.dumps(
            {
                "status": "success",
                "message": "Reminder deleted successfully",
                "reminder_id": params.reminder_id,
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, indent=2)


@mcp.tool(
    name="list_reminder_lists",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def list_reminder_lists() -> str:
    """
    List all available reminder lists in macOS Reminders app.

    Returns the names of all reminder lists along with the count of incomplete reminders
    in each list. Useful for discovery before calling list_reminders or create_reminder.
    """
    try:
        store = _get_store()
        calendars = store.calendarsForEntityType_(EventKit.EKEntityTypeReminder)

        # Fetch all incomplete reminders once, then group by list
        all_ek = _fetch_reminders(store, None, include_completed=False)
        counts: dict = {}
        for r in all_ek:
            name = str(r.calendar().title())
            counts[name] = counts.get(name, 0) + 1

        result = []
        for cal in calendars:
            name = str(cal.title())
            result.append({"name": name, "incomplete_count": counts.get(name, 0)})

        result.sort(key=lambda x: x["name"])

        return json.dumps(
            {"count": len(result), "lists": result},
            indent=2,
        )

    except Exception as e:
        return json.dumps({"error": str(e), "status": "failed"}, indent=2)


@mcp.tool(
    name="get_reminder",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def get_reminder(params: GetReminderInput) -> str:
    """
    Get a single reminder by its ID.

    Retrieves full details of one reminder including id, title, body, due date,
    priority, completed status, and list name.

    Args:
        params (GetReminderInput): Parameters containing:
            - reminder_id (str): The reminder ID to retrieve
    """
    try:
        store = _get_store()
        reminder = _find_reminder_by_id(store, params.reminder_id)

        if reminder is None:
            return json.dumps(
                {
                    "status": "failed",
                    "error": f"Reminder with ID {params.reminder_id} not found",
                },
                indent=2,
            )

        return json.dumps(_reminder_to_dict(reminder), indent=2)

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, indent=2)


@mcp.tool(
    name="update_reminder",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def update_reminder(params: UpdateReminderInput) -> str:
    """
    Update an existing reminder in macOS Reminders app.

    Modifies the specified reminder's fields. Only provided fields are updated;
    omitted fields remain unchanged.

    Args:
        params (UpdateReminderInput): Parameters containing:
            - reminder_id (str): The reminder ID to update (required)
            - title (str): New title (optional)
            - body (str): New notes/description (optional)
            - due_date (str): New due date in YYYY-MM-DD format (optional)
            - due_time (str): New due time in HH:MM format (optional)
            - priority (int): New priority 0/1/5/9 (optional)
            - list_name (str): Move to this list (optional)
    """
    try:
        store = _get_store()
        reminder = _find_reminder_by_id(store, params.reminder_id)

        if reminder is None:
            return json.dumps(
                {
                    "status": "failed",
                    "error": f"Reminder with ID {params.reminder_id} not found",
                },
                indent=2,
            )

        if params.title is not None:
            reminder.setTitle_(params.title)
        if params.body is not None:
            reminder.setNotes_(params.body)
        if params.priority is not None:
            reminder.setPriority_(params.priority)
        if params.list_name is not None:
            cal = _find_calendar(store, params.list_name)
            if cal is None:
                return json.dumps(
                    {
                        "status": "failed",
                        "error": f"Reminder list '{params.list_name}' not found",
                    },
                    indent=2,
                )
            reminder.setCalendar_(cal)

        # Update due date/time if any date/time field provided
        if params.due_date is not None or params.due_time is not None:
            if params.due_date is not None:
                # Build fresh components from provided date (+ time if given)
                comps = _make_due_date_components(params.due_date, params.due_time)
            else:
                # Preserve existing date, update only time
                existing_comps = reminder.dueDateComponents()
                if existing_comps is not None:
                    cal_obj = NSCalendar.alloc().initWithCalendarIdentifier_(
                        NSCalendarIdentifierGregorian
                    )
                    nsdate = cal_obj.dateFromComponents_(existing_comps)
                    if nsdate is not None:
                        ts = nsdate.timeIntervalSince1970()
                        dt = datetime.fromtimestamp(ts)
                        comps = _make_due_date_components(
                            dt.strftime("%Y-%m-%d"), params.due_time
                        )
                    else:
                        comps = _make_due_date_components("1970-01-01", params.due_time)
                else:
                    # No existing date — set today with given time
                    today = datetime.now().strftime("%Y-%m-%d")
                    comps = _make_due_date_components(today, params.due_time)
            reminder.setDueDateComponents_(comps)

        success, error = store.saveReminder_commit_error_(reminder, True, None)
        if not success or error is not None:
            raise RuntimeError(f"Failed to update reminder: {error}")

        return json.dumps(
            {
                "status": "success",
                "message": "Reminder updated successfully",
                "reminder_id": params.reminder_id,
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, indent=2)


if __name__ == "__main__":
    mcp.run()
