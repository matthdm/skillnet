from __future__ import annotations

import os
from typing import Any

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")
JOB_TYPE_OPTIONS = {
    "feature": "Feature",
    "new_service": "New Service",
    "change_request": "Change Request",
}


def load_projects() -> list[dict[str, Any]]:
    try:
        response = requests.get(f"{API_URL}/projects", timeout=10)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
    except requests.RequestException as exc:
        st.warning(f"Projects unavailable: {exc}")
    return []


st.title("Submit Feature")

projects = load_projects()
project_options = ["(None)"] + [
    f"{project.get('name', project.get('project_id', ''))} ({project.get('project_id', '')})"
    for project in projects
]
project_lookup = {
    f"{project.get('name', project.get('project_id', ''))} ({project.get('project_id', '')})": project.get("project_id")
    for project in projects
}

job_type = st.selectbox(
    "Job Type",
    options=list(JOB_TYPE_OPTIONS),
    format_func=lambda key: JOB_TYPE_OPTIONS[key],
)
selected_project = st.selectbox("Project (optional)", options=project_options, index=0)
target_repo = ""
if job_type == "change_request":
    target_repo = st.text_input("Target Repo", placeholder="owner/repo or repo-name")

feature_markdown = st.text_area("Feature Specification (markdown)", height=300)

if st.button("Submit"):
    markdown = feature_markdown.strip()
    if not markdown:
        st.error("Feature markdown is required.")
    else:
        payload: dict[str, Any] = {
            "markdown": markdown,
            "job_type": job_type,
        }
        project_id = project_lookup.get(selected_project)
        if project_id:
            payload["project_id"] = project_id
        if job_type == "change_request" and target_repo.strip():
            payload["target_repo"] = target_repo.strip()

        try:
            response = requests.post(
                f"{API_URL}/ingest/feature/markdown",
                json=payload,
                timeout=10,
            )
        except requests.RequestException as exc:
            st.error(f"Request failed: {exc}")
        else:
            if response.status_code == 200:
                body = response.json()
                st.success(f"Queued as job {body['job_id']}")
                st.markdown("[Go to Jobs page](./1_Jobs_Queue)")
            elif response.status_code == 409:
                st.info("Feature already queued.")
            else:
                st.error(f"Error {response.status_code}: {response.text}")
