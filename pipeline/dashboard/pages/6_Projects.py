from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")


def fetch_projects() -> list[dict[str, Any]]:
    response = requests.get(f"{API_URL}/projects", timeout=10)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def fetch_project_jobs(project_id: str) -> list[dict[str, Any]]:
    response = requests.get(f"{API_URL}/projects/{project_id}/jobs", timeout=10)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


st.title("Projects")

try:
    projects = fetch_projects()
except requests.RequestException as exc:
    st.error(f"Failed to load projects: {exc}")
    st.stop()

if not projects:
    st.info("No projects found.")
    st.stop()

job_cache: dict[str, list[dict[str, Any]]] = {}
summary_rows: list[dict[str, Any]] = []
for project in projects:
    project_id = str(project.get("project_id", ""))
    if not project_id:
        continue

    try:
        jobs = fetch_project_jobs(project_id)
    except requests.RequestException:
        jobs = []
    job_cache[project_id] = jobs

    total_jobs = len(jobs)
    committed_jobs = sum(1 for job in jobs if str(job.get("status", "")).lower() == "committed")
    success_rate = (committed_jobs / total_jobs * 100.0) if total_jobs else 0.0

    summary_rows.append(
        {
            "project_id": project_id,
            "name": project.get("name", ""),
            "feature_count": int(project.get("feature_count", 0) or 0),
            "job_count": total_jobs,
            "success_rate": f"{success_rate:.1f}%",
        }
    )

st.subheader("Project Summary")
summary_df = pd.DataFrame(
    [
        {
            "name": row["name"],
            "feature_count": row["feature_count"],
            "job_count": row["job_count"],
            "success_rate": row["success_rate"],
        }
        for row in summary_rows
    ]
)
st.dataframe(summary_df, use_container_width=True, hide_index=True)

project_index = {
    f"{row['name']} ({row['project_id']})": row["project_id"] for row in summary_rows
}
selected_label = st.selectbox("Select project", options=list(project_index))
selected_project_id = project_index[selected_label]

jobs = job_cache.get(selected_project_id, [])
if not jobs:
    st.info("No jobs linked to this project yet.")
else:
    st.subheader(f"Jobs for {selected_project_id}")
    jobs_df = pd.DataFrame(
        [
            {
                "job_id": job.get("job_id", ""),
                "feature_id": job.get("feature_id", ""),
                "status": job.get("status", ""),
                "degraded": bool(job.get("degraded", False)),
                "updated_at": job.get("updated_at", ""),
                "pr_url": job.get("pr_url", ""),
            }
            for job in jobs
        ]
    )
    st.dataframe(jobs_df, use_container_width=True, hide_index=True)
