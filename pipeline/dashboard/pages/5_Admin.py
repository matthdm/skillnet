from __future__ import annotations

import os
import time
from typing import Any

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")

st.title("Admin")


# ── Skill Ingestion ───────────────────────────────────────────────────────────

st.subheader("Skill Ingestion")


def fetch_ingestion_status() -> dict[str, Any] | None:
    try:
        r = requests.get(f"{API_URL}/admin/ingest-skills/status", timeout=10)
        r.raise_for_status()
        return r.json() if isinstance(r.json(), dict) else None
    except requests.RequestException as exc:
        st.error(f"Failed to fetch ingestion status: {exc}")
        return None


def render_ingestion_status(status: dict[str, Any]) -> None:
    state = str(status.get("state", "idle"))
    processed = int(status.get("skills_processed") or 0)
    total = int(status.get("skills_total") or 0)
    if state == "idle":
        st.info("Status: idle")
    elif state == "running":
        st.info("Status: running")
        st.progress(min(processed / (total or 1), 1.0))
        st.text(f"{processed} / {total} skills embedded")
    elif state == "complete":
        st.success(f"Status: complete — {processed} skills indexed")
    elif state == "failed":
        st.error(f"Status: failed — {status.get('error') or 'unknown error'}")
    else:
        st.warning(f"Status: {state}")
    if status.get("started_at"):
        st.caption(f"Started: {status['started_at']}")
    if status.get("completed_at"):
        st.caption(f"Completed: {status['completed_at']}")


def poll_ingestion(placeholder: st.delta_generator.DeltaGenerator) -> None:
    while True:
        status = fetch_ingestion_status()
        if status is None:
            return
        with placeholder.container():
            render_ingestion_status(status)
        if str(status.get("state", "")) != "running":
            return
        time.sleep(2)


ingest_placeholder = st.empty()
ingest_status = fetch_ingestion_status()

if ingest_status is not None:
    with ingest_placeholder.container():
        render_ingestion_status(ingest_status)

    is_running = str(ingest_status.get("state", "")) == "running"
    if st.button("Run Ingestion", disabled=is_running):
        try:
            resp = requests.post(f"{API_URL}/admin/ingest-skills", timeout=10)
            if resp.status_code in (202, 409):
                st.info("Ingestion started..." if resp.status_code == 202 else "Already running.")
                poll_ingestion(ingest_placeholder)
            else:
                st.error(f"Failed to start ingestion: {resp.status_code} {resp.text}")
        except requests.RequestException as exc:
            st.error(f"Failed to start ingestion: {exc}")

    if is_running:
        poll_ingestion(ingest_placeholder)

st.divider()


# ── Redis Management ─────────────────────────────────────────────────────────

st.subheader("Redis Management")


def fetch_redis_stats() -> dict[str, Any] | None:
    try:
        r = requests.get(f"{API_URL}/admin/redis/stats", timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        st.error(f"Failed to fetch Redis stats: {exc}")
        return None


redis_stats = fetch_redis_stats()

if redis_stats:
    c1, c2, c3 = st.columns(3)
    c1.metric("Jobs (total)", redis_stats.get("total_jobs", 0))
    c2.metric("Queue depth", redis_stats.get("queue_depth", 0))
    c3.metric("Dedup entries", redis_stats.get("dedup_count", 0))

    seen = redis_stats.get("seen_feature_ids", [])
    if seen:
        with st.expander(f"Seen feature IDs ({len(seen)})", expanded=False):
            for fid in seen:
                col_a, col_b = st.columns([4, 1])
                col_a.code(fid)
                if col_b.button("Clear", key=f"dedup_{fid}"):
                    try:
                        r = requests.delete(f"{API_URL}/admin/redis/dedup/{fid}", timeout=10)
                        if r.status_code == 200:
                            st.success(f"Cleared dedup for {fid}")
                            st.rerun()
                        else:
                            st.error(f"Error: {r.text}")
                    except requests.RequestException as exc:
                        st.error(str(exc))

st.divider()
st.markdown("**Danger Zone**")

col_dedup, col_jobs = st.columns(2)

with col_dedup:
    st.caption("Removes all entries from the dedup set. All features can be resubmitted.")
    if st.button("Clear All Dedup", type="secondary"):
        try:
            r = requests.delete(f"{API_URL}/admin/redis/dedup", timeout=10)
            data = r.json()
            st.success(f"Cleared {data.get('cleared_count', 0)} dedup entries")
            st.rerun()
        except requests.RequestException as exc:
            st.error(str(exc))

with col_jobs:
    st.caption("Deletes all job records and empties the queue. Does not touch the dedup set.")
    if st.button("Clear All Jobs", type="primary"):
        try:
            r = requests.delete(f"{API_URL}/admin/redis/jobs", timeout=10)
            data = r.json()
            st.success(f"Cleared {data.get('cleared_jobs', 0)} jobs")
            st.rerun()
        except requests.RequestException as exc:
            st.error(str(exc))
