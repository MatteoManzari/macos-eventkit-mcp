# macOS EventKit MCP servers

Two separate MCP servers expose Apple Reminders and Calendar through native EventKit access. Each server runs over stdio and can be configured independently in an MCP client.

## Requirements

- macOS with Reminders and Calendar available
- Python 3.11
- Reminders or Calendar access granted to the Python process in System Settings > Privacy & Security

## Install and run

```bash
python3.11 -m venv env
env/bin/pip install -r requirements.txt
env/bin/python reminders_mcp.py
```

Run `env/bin/python calendar_mcp.py` for the Calendar server. Configure each as a separate stdio MCP server in your client. Standard output is reserved for MCP messages; diagnostics go to standard error.

## Tools

The Reminders server provides `list_reminders`, `list_reminder_lists`, `get_reminder`, `create_reminder`, `update_reminder`, `complete_reminder`, and `delete_reminder`. The Calendar server provides `list_events`, `list_calendars`, `get_event`, `create_event`, `update_event`, and `delete_event`. Calendar event creation and updates support one or more alerts through `alert_minutes_before`.

## Integration tests

Tests use the real macOS apps and create temporary records prefixed with `[TEST]`. Set writable destinations before running them:

```bash
export TEST_REMINDERS_LIST="your writable reminder list"
export TEST_CALENDAR_NAME="your writable calendar"
env/bin/pytest -v
```

Optionally set `TEST_REMINDERS_LISTS` to a comma-separated list of additional reminder lists to test. Tests for an unset destination are skipped. The values stay in your local environment and are not committed.
