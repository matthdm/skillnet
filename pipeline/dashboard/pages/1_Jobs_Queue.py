from __future__ import annotations

import os
from typing import Any

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")
STATUS_COLORS = {
    "pending": "#6c757d", "injected": "#0dcaf0", "analyzed": "#0d6efd",
    "skills_retrieved": "#6610f2", "coding": "#fd7e14", "testing": "#20c997",
    "committed": "#198754", "failed": "#dc3545", "exhausted": "#adb5bd", "paused": "#495057",
}


def render_status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#495057")
    return (
        f"<span style='background:{color};color:white;"
        "padding:0.25rem 0.5rem;border-radius:0.4rem;font-size:0.8rem;'>"
        f"{status}</span>"
    )


st.title("Jobs Queue")

try:
    response = requests.get(f"{API_URL}/jobs/", timeout=10)
    response.raise_for_status()
    jobs: list[dict[str, Any]] = response.json() if isinstance(response.json(), list) else []
except requests.RequestException as exc:
    st.error(f"Failed to load jobs: {exc}")
    jobs = []

if not jobs:
    st.info("No jobs found.")
else:
    for job in jobs:
        job_id = str(job.get("job_id", ""))
        status = str(job.get("status", "unknown"))
        degraded = bool(job.get("degraded", False))
        degraded_label = " <span style='color:#ffc107;'>(degraded)</span>" if degraded else ""
        st.markdown(
            f"`{job_id}` {render_status_badge(status)}{degraded_label}",
            unsafe_allow_html=True,
        )
