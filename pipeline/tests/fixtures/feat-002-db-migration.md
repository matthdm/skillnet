# FEAT-002: Migration DB Users Table

## Description

This feature performs a database migration to add an optional `email_verified` boolean column
to the users table with a default value of false.

## Acceptance Criteria

- Migration script must be idempotent (safe to run multiple times).
- Generates appropriate Alembic revision automatically.
- Includes rollback (`downgrade`) function.
- Column defaults to `false` for existing rows.

## Tech Stack

Python, Alembic, PostgreSQL
