# FEAT-004: In-Memory Token Bucket Rate Limiter

## Description

Implement a thread-safe in-memory rate limiter using the token bucket algorithm.
The limiter controls how many requests a given key (e.g. user ID, IP address) can
make within a sliding time window. Each key maintains its own independent bucket.
No external dependencies — pure Python stdlib only.

## Acceptance Criteria

- `RateLimiter(limit, window_seconds)` initializes a limiter with a max request count and time window.
- `allow(key: str) -> bool` returns `True` if the request is permitted and consumes one token, `False` if the bucket is exhausted.
- A key that has not been seen before always gets its full allocation on first call.
- After `limit` consecutive allowed calls, the next call within the same window returns `False`.
- After the window expires, the bucket resets and `allow` returns `True` again.
- `remaining(key: str) -> int` returns how many tokens are left for a given key.
- `reset(key: str) -> None` clears the bucket for a key immediately.
- Different keys are fully independent — exhausting one does not affect another.
- All methods are thread-safe under concurrent access.

## Tech Stack

- Python
- threading
- time
- dataclasses
