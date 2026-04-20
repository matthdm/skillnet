# CODEX TASK C5 â€” implement this file exactly as specified
#
# Streamlit dashboard with sidebar navigation. Four pages:
#   1. "Jobs Queue"     â€” list all jobs from GET /jobs, show job_id + status as colored badges
#   2. "Job Detail"     â€” text input for job_id, call GET /jobs/{job_id}, display all fields
#                         highlight degraded=True jobs with st.warning
#                         show error_logs in st.error if non-empty
#                         show provider_log and skills used
#   3. "Skill Pool"     â€” text input for search query, call GET /skills/search?q=... (stub for now)
#                         show results as cards with name, description, score
#   4. "Degraded Jobs"  â€” filtered list from GET /jobs where degraded=True
#                         each row has a "Queue Repair" button (stub â€” just st.success for now)
#
# Use requests to call the API at os.environ.get("API_URL", "http://localhost:8000")
# Use st.sidebar.selectbox for navigation.
# Do not implement any direct Redis or ChromaDB access â€” go through the API only.

from __future__ import annotations

import os
from typing import Any

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
STATUS_COLORS = {
    "pending": "#6c757d",
    "injected": "#0dcaf0",
    "analyzed": "#0d6efd",
    "skills_retrieved": "#6610f2",
    "coding": "#fd7e14",
    "testing": "#20c997",
    "committed": "#198754",
    "failed": "#dc3545",
    "exhausted": "#adb5bd",
}


def get_jobs() -> list[dict[str, Any]]:
    response = requests.get(f"{API_URL}/jobs/", timeout=10)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list):
        return data
    return []


def render_status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#495057")
    return (
        f"<span style='background:{color};color:white;"
        "padding:0.25rem 0.5rem;border-radius:0.4rem;font-size:0.8rem;'>"
        f"{status}</span>"
    )


st.set_page_config(page_title="Skillnet Pipeline", layout="wide")
st.title("Skillnet Pipeline")

page = st.sidebar.selectbox("Navigate", ["Jobs Queue", "Job Detail", "Skill Pool", "Degraded Jobs"])

if page == "Jobs Queue":
    st.subheader("Jobs Queue")
    try:
        jobs = get_jobs()
    except requests.RequestException as exc:
        st.error(f"Failed to load jobs: {exc}")
    else:
        if not jobs:
            st.info("No jobs in queue.")
        for job in jobs:
            job_id = str(job.get("job_id", ""))
            status = str(job.get("status", "unknown"))
            degraded = bool(job.get("degraded", False))
            degraded_label = " <span style='color:#ffc107;'>(degraded)</span>" if degraded else ""
            st.markdown(
                f"`{job_id}` {render_status_badge(status)}{degraded_label}",
                unsafe_allow_html=True,
            )

elif page == "Job Detail":
    st.subheader("Job Detail")
    job_id = st.text_input("Job ID")
    if job_id:
        try:
            response = requests.get(f"{API_URL}/jobs/{job_id}", timeout=10)
            if response.status_code == 404:
                st.error(f"Job not found: {job_id}")
            else:
                response.raise_for_status()
                job = response.json()

                if job.get("degraded"):
                    st.warning("This job is marked degraded.")

                error_logs = job.get("error_logs", [])
                if error_logs:
                    st.error("\n".join(str(item) for item in error_logs))

                st.markdown("**Provider Log**")
                st.write(job.get("provider_log", []))

                st.markdown("**Skills Used**")
                skill_entries = job.get("skills_pool", [])
                if skill_entries:
                    for item in skill_entries:
                        skill = item.get("skill", {})
                        name = skill.get("name", "")
                        score = item.get("score", 0.0)
                        st.write(f"{name} ({score:.4f})")
                else:
                    st.write([])

                st.markdown("**Full Job State**")
                st.json(job)
        except requests.RequestException as exc:
            st.error(f"Failed to load job: {exc}")

elif page == "Skill Pool":
    st.subheader("Skill Pool")
    st.info("Skill search endpoint not yet available.")
    st.text_input("Search query", disabled=True, placeholder="Endpoint pending")

elif page == "Degraded Jobs":
    st.subheader("Degraded Jobs")
    try:
        jobs = get_jobs()
    except requests.RequestException as exc:
        st.error(f"Failed to load jobs: {exc}")
    else:
        degraded_jobs = [job for job in jobs if job.get("degraded")]
        if not degraded_jobs:
            st.info("No degraded jobs.")
        for job in degraded_jobs:
            job_id = str(job.get("job_id", ""))
            status = str(job.get("status", "unknown"))
            cols = st.columns([4, 2, 2])
            cols[0].markdown(f"`{job_id}`")
            cols[1].markdown(render_status_badge(status), unsafe_allow_html=True)
            if cols[2].button("Queue Repair", key=f"repair-{job_id}"):
                st.success(f"Repair queued for {job_id} (stub).")
