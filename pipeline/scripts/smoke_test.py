from __future__ import annotations

import argparse
from pathlib import Path

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send markdown fixture files to feature ingest endpoint."
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixtures_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    fixtures = sorted(fixtures_dir.glob("*.md"))

    had_failures = False

    for fixture in fixtures:
        body = fixture.read_text(encoding="utf-8")
        try:
            response = requests.post(
                f"{args.base_url}/ingest/feature/markdown",
                data=body,
                headers={"Content-Type": "text/plain"},
                timeout=20,
            )
        except requests.RequestException as exc:
            had_failures = True
            print(f"[FAIL] {fixture.name} -> request error: {exc}")
            continue

        if response.status_code == 200:
            payload = response.json()
            print(f"[OK] {fixture.name} -> job_id={payload.get('job_id')}")
            continue

        if response.status_code == 409:
            print(f"[SKIP] {fixture.name} -> already submitted")
            continue

        had_failures = True
        print(f"[FAIL] {fixture.name} -> HTTP {response.status_code}: {response.text}")

    return 1 if had_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
