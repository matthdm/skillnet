from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")

STATUS_COLORS = {
    "pending":          "#6c757d",
    "injected":         "#0dcaf0",
    "analyzed":         "#0d6efd",
    "skills_retrieved": "#6610f2",
    "planning":         "#e83e8c",
    "coding":           "#fd7e14",
    "testing":          "#20c997",
    "committed":        "#198754",
    "failed":           "#dc3545",
    "exhausted":        "#adb5bd",
    "paused":           "#495057",
    "rejected":         "#842029",
}

ACTIVE_STATUSES = {
    "pending", "injected", "analyzed", "skills_retrieved", "coding", "testing",
}
TERMINAL_STATUSES = {"committed", "failed", "exhausted", "rejected"}


def render_status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#495057")
    return (
        f"<span style='background:{color};color:white;"
        "padding:0.25rem 0.6rem;border-radius:0.4rem;font-size:0.8rem;font-weight:600'>"
        f"{status}</span>"
    )


def _format_created_at(value: str | None) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _sort_key(job: dict[str, Any]) -> tuple[int, str, str]:
    # PLANNING first, then by created_at descending (negate via sort trick)
    status = str(job.get("status", ""))
    planning_first = 0 if status == "planning" else 1
    created_at = str(job.get("created_at") or "")
    return (planning_first, created_at, str(job.get("job_id") or ""))


# ── Page ─────────────────────────────────────────────────────────────────────

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
    st.stop()

# Build parent/child tree
job_by_id = {str(job.get("job_id", "")): job for job in jobs}
children_by_parent: dict[str, list[dict[str, Any]]] = {}
root_jobs: list[dict[str, Any]] = []

for job in jobs:
    parent_id = str(job.get("parent_job_id") or "")
    if parent_id and parent_id in job_by_id:
        children_by_parent.setdefault(parent_id, []).append(job)
    else:
        root_jobs.append(job)

for children in children_by_parent.values():
    children.sort(key=_sort_key)
root_jobs.sort(key=_sort_key)

# PLANNING banner
planning_jobs = [j for j in jobs if j.get("status") == "planning"]
if planning_jobs:
    st.warning(f"{len(planning_jobs)} job(s) awaiting plan approval", icon="⚠️")

# Column header
hdr_left, hdr_mid, hdr_right = st.columns([3, 4, 2])
hdr_left.caption("Status")
hdr_mid.caption("Feature / Title")
hdr_right.caption("Created")
st.divider()


def render_job_row(job: dict[str, Any], depth: int = 0) -> None:
    job_id = str(job.get("job_id", ""))
    status = str(job.get("status", "unknown"))
    feature_id = str(job.get("feature_id", "")) or "—"
    title = str(job.get("title", "")) or "—"
    created_at = _format_created_at(str(job.get("created_at", "")))
    degraded = bool(job.get("degraded", False))

    col_status, col_info, col_time = st.columns([3, 4, 2])

    with col_status:
        st.markdown(render_status_badge(status), unsafe_allow_html=True)

    with col_info:
        indent_px = depth * 20
        degraded_tag = " <span style='color:#ffc107;font-size:0.75rem'>(degraded)</span>" if degraded else ""
        retry_tag = "<span style='color:#adb5bd;font-size:0.75rem'>↳ retry &nbsp;</span>" if depth > 0 else ""
        st.markdown(
            f"<div style='padding-left:{indent_px}px'>"
            f"{retry_tag}"
            f"<span style='font-weight:600;font-size:0.95rem'>{feature_id}</span> "
            f"<span style='color:#adb5bd;font-size:0.85rem'>— {title}</span>"
            f"{degraded_tag}<br>"
            f"<span style='font-family:monospace;font-size:0.7rem;color:#666'>{job_id}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    with col_time:
        st.caption(created_at)
        if st.button("View", key=f"view_{job_id}", use_container_width=True):
            st.session_state["selected_job_id"] = job_id
            st.switch_page("pages/2_Job_Detail.py")

    for child in children_by_parent.get(job_id, []):
        render_job_row(child, depth=depth + 1)


for root in root_jobs:
    render_job_row(root)
    st.divider()

# Auto-refresh while any active jobs exist
has_active = any(j.get("status") in ACTIVE_STATUSES for j in jobs)
if has_active:
    st.caption("Auto-refreshing every 4s…")
    time.sleep(4)
    st.rerun()
