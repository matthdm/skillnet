# FEAT-001: GET /api/v1/users

## Description

This feature exposes a read-only API endpoint to fetch a list of users from the database.
It supports pagination and sorting.

## Acceptance Criteria

- Returns HTTP 200 with a JSON array when queried.
- Supports query parameters `page` and `limit`.
- Handles empty database gracefully (returns empty array, not 404).
- Response must include metadata with total count.

## Tech Stack

Python, FastAPI, SQLAlchemy
