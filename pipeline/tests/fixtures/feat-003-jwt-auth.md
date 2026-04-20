# FEAT-003: JWT Authentication Flow

## Description

This feature implements secure login and logout endpoints. It generates JWT tokens upon
successful email/password verification and invalidates them on logout via Redis.

## Acceptance Criteria

- `POST /login` returns HTTP 200 with an access token on valid credentials.
- `POST /logout` invalidates the token server-side via Redis.
- Invalid credentials return HTTP 401 Unauthorized.
- Tokens must expire after 15 minutes by default.

## Tech Stack

Python, PyJWT, Redis
