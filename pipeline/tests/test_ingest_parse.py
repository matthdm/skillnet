from __future__ import annotations

from pathlib import Path

import pytest

from api.routes.ingest import _parse_markdown

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_parse_feat_001_users_api() -> None:
    spec = _parse_markdown(_read_fixture("feat-001-users-api.md"))
    assert spec.feature_id == "FEAT-001"
    assert spec.title
    assert spec.description
    assert spec.acceptance_criteria
    assert isinstance(spec.tech_stack_hint, list)
    assert spec.tech_stack_hint


def test_parse_feat_002_db_migration() -> None:
    spec = _parse_markdown(_read_fixture("feat-002-db-migration.md"))
    assert spec.feature_id == "FEAT-002"
    assert spec.title
    assert spec.description
    assert spec.acceptance_criteria
    assert isinstance(spec.tech_stack_hint, list)
    assert spec.tech_stack_hint


def test_parse_feat_003_jwt_auth() -> None:
    spec = _parse_markdown(_read_fixture("feat-003-jwt-auth.md"))
    assert spec.feature_id == "FEAT-003"
    assert spec.title
    assert spec.description
    assert spec.acceptance_criteria
    assert isinstance(spec.tech_stack_hint, list)
    assert spec.tech_stack_hint


def test_parse_markdown_missing_header_raises_value_error() -> None:
    markdown = "Description without a recognized markdown heading."
    with pytest.raises(ValueError):
        _parse_markdown(markdown)
