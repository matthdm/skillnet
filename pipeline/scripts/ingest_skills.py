# CODEX TASK C2 â€” implement this file exactly as specified
#
# Source files (relative to skillnet/ root, mounted at /app/ in Docker):
#   skills_catalog.json  â€” structured metadata for all 1,431 skills
#   skills_index.json    â€” extended metadata including codex/claude support flags
#   skills/{id}/SKILL.md â€” full markdown content for each skill
#
# Implement TWO functions:
#
# def parse_skill_body(skill_dir: Path) -> str
#   - Read SKILL.md inside skill_dir
#   - Strip the YAML frontmatter block: the opening "---", all lines until the closing "---", inclusive
#   - Return the remaining markdown as a stripped string
#   - Return "" if the file does not exist (do not raise)
#
# def load_all_skills() -> list[dict]
#   1. Load SKILLS_CATALOG with json.loads â€” it is a JSON object {"generatedAt": ..., "total": ..., "skills": [...]}
#   2. Load SKILLS_INDEX with json.loads â€” it is a JSON array of objects with field "id" and optional "plugin"
#      Build a dict keyed by id for O(1) lookup: index_map = {entry["id"]: entry for entry in index_data}
#   3. For each entry in catalog["skills"]:
#        body = parse_skill_body(SKILLS_ROOT / entry["id"])
#        tags = entry.get("tags", [])  â€” already a list, no normalization needed
#        plugin_meta = index_map.get(entry["id"], {}).get("plugin", {})
#        targets = plugin_meta.get("targets", {})  â€” dict like {"codex": "supported", "claude": "supported"}
#        yield dict:
#          skill_id    = entry["id"]
#          name        = entry["name"]
#          description = entry["description"]
#          category    = entry.get("category", "")
#          tags        = tags
#          body        = body
#          source_path = str(SKILLS_ROOT / entry["id"] / "SKILL.md")
#          supports_codex  = targets.get("codex") == "supported"
#          supports_claude = targets.get("claude") == "supported"
#   4. Collect results into a list. Count: total_in_catalog, with_body (body != ""), missing_body.
#   5. Print summary to stdout:
#        Catalog total: {total_in_catalog}
#        Skills with body: {with_body}
#        Skills missing body: {missing_body}
#      Then return the list.
#
# CONSTRAINTS:
#   - Imports: json, pathlib, sys only (stdlib â€” no pyyaml, no requests, no third-party)
#   - DO NOT generate embeddings
#   - DO NOT write to ChromaDB
#   - DO NOT rename or restructure the output dict keys â€” they map directly to models/skill.py Skill fields
#   - Must run standalone: python scripts/ingest_skills.py

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILLS_ROOT = Path(__file__).parent.parent.parent / "skills"
SKILLS_CATALOG = Path(__file__).parent.parent.parent / "skills_catalog.json"
SKILLS_INDEX = Path(__file__).parent.parent.parent / "skills_index.json"


def parse_skill_body(skill_dir: Path) -> str:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return ""

    content = skill_file.read_text(encoding="utf-8")
    lines = content.splitlines()
    if not lines:
        return ""

    if lines[0].strip() != "---":
        return content.strip()

    closing_index = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = idx
            break

    if closing_index is None:
        return content.strip()

    return "\n".join(lines[closing_index + 1 :]).strip()


def load_all_skills() -> list[dict]:
    catalog = json.loads(SKILLS_CATALOG.read_text(encoding="utf-8"))
    index_data = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
    index_map = {entry["id"]: entry for entry in index_data if "id" in entry}

    skills_data = catalog.get("skills", [])
    total_in_catalog = len(skills_data)
    with_body = 0
    missing_body = 0

    results: list[dict] = []
    for entry in skills_data:
        skill_id = str(entry["id"])
        body = parse_skill_body(SKILLS_ROOT / skill_id)
        if body:
            with_body += 1
        else:
            missing_body += 1

        plugin_meta = index_map.get(skill_id, {}).get("plugin", {})
        targets = plugin_meta.get("targets", {})
        tags = entry.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        results.append(
            {
                "skill_id": skill_id,
                "name": entry["name"],
                "description": entry["description"],
                "category": entry.get("category", ""),
                "tags": tags,
                "body": body,
                "source_path": entry.get("path", f"skills/{skill_id}/SKILL.md"),
                "supports_codex": targets.get("codex") == "supported",
                "supports_claude": targets.get("claude") == "supported",
            }
        )

    print(f"Catalog total: {total_in_catalog}", file=sys.stdout)
    print(f"Skills with body: {with_body}", file=sys.stdout)
    print(f"Skills missing body: {missing_body}", file=sys.stdout)

    return results


if __name__ == "__main__":
    skills = load_all_skills()
    print(f"Loaded {len(skills)} skills", file=sys.stdout)
