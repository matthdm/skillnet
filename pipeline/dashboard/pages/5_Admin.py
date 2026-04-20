from __future__ import annotations

import os
import time
from typing import Any

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")


def fetch_status() -> dict[str, Any] | None:
    try:
        response = requests.get(f"{API_URL}/admin/ingest-skills/status", timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        st.error(f"Failed to fetch ingestion status: {exc}")
        return None
    data = response.json()
    return data if isinstance(data, dict) else None


def render_status(status: dict[str, Any]) -> None:
    state = str(status.get("state", "idle"))
    skills_processed = int(status.get("skills_processed") or 0)
    skills_total = int(status.get("skills_total") or 0)
    started_at = status.get("started_at")
    completed_at = status.get("completed_at")
    error = status.get("error")

    if state == "idle":
        st.info("Status: idle")
    elif state == "running":
        st.info("Status: running")
        denominator = skills_total if skills_total > 0 else 1
        st.progress(min(skills_processed / denominator, 1.0))
        st.text(f"{skills_processed} / {skills_total} skills embedded")
    elif state == "complete":
        st.success(f"Status: complete — {skills_processed} skills indexed")
    elif state == "failed":
        st.error(f"Status: failed — {error or 'Unknown error'}")
    else:
        st.warning(f"Status: {state}")

    if started_at:
        st.caption(f"Started: {started_at}")
    if completed_at:
        st.caption(f"Completed: {completed_at}")


def poll_until_finished(status_placeholder: st.delta_generator.DeltaGenerator) -> dict[str, Any] | None:
    while True:
        status = fetch_status()
        if status is None:
            return None
        with status_placeholder.container():
            render_status(status)
        if str(status.get("state", "")) != "running":
            return status
        time.sleep(2)


st.title("Admin")
st.subheader("Skill Ingestion")

status_placeholder = st.empty()
status = fetch_status()

if status is not None:
    with status_placeholder.container():
        render_status(status)

    is_running = str(status.get("state", "")) == "running"
    if st.button("Run Ingestion", disabled=is_running):
        if is_running:
            st.warning("Ingestion already running.")
        else:
            try:
                trigger_resp = requests.post(f"{API_URL}/admin/ingest-skills", timeout=10)
                if trigger_resp.status_code == 202:
                    st.info("Ingestion started...")
                    poll_until_finished(status_placeholder)
                elif trigger_resp.status_code == 409:
                    st.warning("Ingestion already running.")
                    poll_until_finished(status_placeholder)
                else:
                    st.error(f"Failed to start ingestion: {trigger_resp.status_code} {trigger_resp.text}")
            except requests.RequestException as exc:
                st.error(f"Failed to start ingestion: {exc}")

    if is_running:
        poll_until_finished(status_placeholder)
