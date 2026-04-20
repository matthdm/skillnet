# FEAT-006: Rate Limiter — Stats and Bulk Reset

## Description

Extend the existing `RateLimiter` class in `rate_limiter/limiter.py` with two new methods:
`stats()` and `reset_all()`. The existing `allow`, `remaining`, and `reset` methods must
not change in behaviour or signature. All new methods must be thread-safe.

This is a change request against the existing `feat-004` repository.
The existing file structure is:

```
rate_limiter/__init__.py
rate_limiter/limiter.py
tests/__init__.py
tests/test_limiter.py
```

Modify `rate_limiter/limiter.py` in place. Add new tests to `tests/test_limiter.py`
without removing any existing tests.

## Acceptance Criteria

- `stats() -> dict` returns a snapshot with exactly these keys:
  - `tracked_keys: int` — number of distinct keys that currently have a bucket
  - `total_remaining: float` — sum of remaining tokens across all tracked keys
  - `exhausted_keys: int` — number of keys whose remaining token count is 0
- `reset_all() -> None` clears every bucket immediately. After calling `reset_all()`, `stats()` returns `{"tracked_keys": 0, "total_remaining": 0.0, "exhausted_keys": 0}`.
- `stats()` returns a consistent snapshot — it must not be affected by concurrent `allow()` calls mid-read.
- `reset_all()` acquires the master lock before clearing, ensuring no `allow()` or `remaining()` call can interleave with the wipe.
- Existing tests all continue to pass without modification.
- New tests cover: stats on empty limiter, stats after several allow() calls, stats after window expiry, reset_all clears all keys, reset_all followed by allow() grants a full token again.

## Tech Stack

- Python
- threading
- time
- dataclasses
