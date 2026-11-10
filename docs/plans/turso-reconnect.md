# Plan: Recover from stale Turso connections

## Problem (production, 2026-11-10)

- `Database` opens ONE libsql (Turso, Hrana protocol) connection at startup and reuses it forever.
  Turso expires the server-side stream; afterwards every query raises
  `ValueError: Hrana: api error: status=404 Not Found, body={"error":"stream not found: ..."}`.
  The bot never reconnects, so all DB calls fail until the process restarts.
- `Watcher.run` calls `poll_once()` without a try/except. A DB exception escapes, the watcher
  task ends, and `/status` shows "Stopped" permanently (violates AGENTS.md priority 3:
  never crash the whole watcher).

## Task 1 — Reconnect and retry once on stale Turso connection

Files: `app/db.py`, `tests/test_db.py`.

- Only in Turso mode (`self.turso_database_url` set): when a public `Database` method fails with
  an exception raised by the libsql connection (the Hrana `ValueError`; match on a stream/Hrana
  error message, do not swallow unrelated programming errors), close the connection, call the
  existing connection setup again, and retry the whole method ONCE. If the retry fails, raise.
- Implement with one small private helper or decorator applied to the public data methods
  (not `connect`/`close`/`initialize`). No generic retry framework, no backoff loops.
- Retrying a whole method is safe: the failed stream means the transaction did not commit, and
  writes are upserts / `INSERT OR IGNORE` / state updates.
- Log a warning on reconnect (no secrets, no URL/token).
- Local SQLite mode behavior is unchanged.

Done when: a test with a fake connection that raises the Hrana stream error once proves the
method reconnects and returns the correct result; a second consecutive failure raises; a
non-Hrana error is not retried; existing tests pass.

## Task 2 — Watcher survives unexpected errors

Files: `app/watcher.py`, `tests/test_watcher.py`.

- In `Watcher.run`, wrap each `poll_once()` call in `try/except Exception`, log with
  `logger.exception("Polling cycle failed")`, then continue to the normal sleep. `asyncio`
  cancellation (`CancelledError`, a BaseException) must still stop the loop.
- Do not restructure `poll_once`.

Done when: a test where `poll_once` raises on the first cycle and succeeds on the second
shows the watcher keeps running and polls again; full suite + ruff pass.

## Out of scope

No connection pooling, health checks, or changes to schema/commands.
