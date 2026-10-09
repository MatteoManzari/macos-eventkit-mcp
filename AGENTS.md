# Repository Guidelines

## Project Structure & Module Organization

`reminders_mcp.py` and `calendar_mcp.py` are separate, single-file MCP servers for macOS Reminders and Calendar. Both use PyObjC and EventKit for native access. Integration tests live in `tests/`: `test_list_reminders.py` and `test_create_complete_delete.py` cover Reminders, while `test_calendar.py` covers Calendar. Shared MCP client helpers are in `tests/conftest.py`. Dependencies are listed in `requirements.txt`; pytest settings are in `pytest.ini`. There are no bundled assets or build artifacts.

## Build, Test, and Development Commands

Use the Python 3.11 virtual environment in `env/`:

```bash
source env/bin/activate
pip install -r requirements.txt
env/bin/python reminders_mcp.py
env/bin/python calendar_mcp.py
env/bin/pytest -v
env/bin/pytest tests/test_calendar.py -v
```

Run either server as an MCP stdio process; its stdout carries protocol messages. Send diagnostics to stderr through `_dbg()`. The final two commands run the full integration suite or just Calendar tests. No separate build step is required.

## Coding Style & Naming Conventions

Follow the existing Python style: four-space indentation, `snake_case` functions and variables, `PascalCase` Pydantic input models, and `UPPER_SNAKE_CASE` constants. Keep EventKit access in private helpers and expose tools with `@mcp.tool` and appropriate `ToolAnnotations`. Tool handlers return JSON strings; preserve their established success and error shapes and the `x-apple-reminder://` and `x-apple-event://` ID prefixes. No formatter or linter is configured in this repository.

## Testing Guidelines

Tests use `pytest` and `pytest-asyncio`; `pytest.ini` sets automatic asyncio mode and function-scoped event loops. Name tests `test_*.py` and functions `test_*`. The helpers in `tests/conftest.py` launch real servers through `stdio_client`; tests use real macOS Reminders and Calendar data, including `[TEST]` records. Grant the Python process access to both apps before running tests. Set `TEST_REMINDERS_LIST` and `TEST_CALENDAR_NAME` to writable local destinations; optionally set comma-separated `TEST_REMINDERS_LISTS` to exercise more lists. Tests skip when their target is unset. Check that test records are cleaned up.

## Commit & Pull Request Guidelines

Use short imperative commit summaries, such as `Fix all-day event listing`. Keep each commit focused. In pull requests, describe the affected server and behavior, list the integration tests run, and call out any required macOS permissions or local list/calendar configuration. Link a related issue when one exists.
