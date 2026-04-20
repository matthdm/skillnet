from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pandas as pd
import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")

PIPELINE_STAGES = [
    "pending", "injected", "analyzed", "skills_retrieved",
    "coding", "testing", "committed",
]
TERMINAL_STAGES = {"committed", "failed", "exhausted", "paused"}

STATUS_COLORS = {
    "pending":          "#6c757d",
    "injected":         "#0dcaf0",
    "analyzed":         "#0d6efd",
    "skills_retrieved": "#6610f2",
    "coding":           "#fd7e14",
    "testing":          "#20c997",
    "committed":        "#198754",
    "failed":           "#dc3545",
    "exhausted":        "#adb5bd",
    "paused":           "#495057",
}


def badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#495057")
    return (
        f"<span style='background:{color};color:white;"
        "padding:0.3rem 0.7rem;border-radius:0.4rem;font-size:0.9rem;font-weight:600;'>"
        f"{status.upper()}</span>"
    )


def pipeline_progress(status: str) -> None:
    try:
        step = PIPELINE_STAGES.index(status)
    except ValueError:
        step = 0
    total = len(PIPELINE_STAGES) - 1  # committed = 100%
    pct = step / total

    st.markdown("**Pipeline progress**")
    cols = st.columns(len(PIPELINE_STAGES))
    for i, stage in enumerate(PIPELINE_STAGES):
        color = STATUS_COLORS.get(stage, "#6c757d")
        if i < step:
            bg, text_color = color, "white"
        elif i == step:
            bg, text_color = color, "white"
            label = f"▶ {stage}"
        else:
            bg, text_color = "#f0f0f0", "#888"
        label = stage.replace("_", " ")
        cols[i].markdown(
            f"<div style='background:{bg};color:{text_color};text-align:center;"
            f"padding:0.3rem 0.1rem;border-radius:0.3rem;font-size:0.7rem;font-weight:600;'>"
            f"{label}</div>",
            unsafe_allow_html=True,
        )
    st.progress(min(pct, 1.0))


def elapsed(created_at: str | None, updated_at: str | None) -> str:
    if not created_at:
        return "—"
    try:
        fmt = "%Y-%m-%dT%H:%M:%S.%f"
        t0 = datetime.fromisoformat(created_at.split("+")[0].rstrip("Z"))
        t1 = datetime.fromisoformat(updated_at.split("+")[0].rstrip("Z")) if updated_at else datetime.utcnow()
        secs = int((t1 - t0).total_seconds())
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m {secs % 60}s"
    except Exception:
        return "—"


def render_alerts(job: dict[str, Any]) -> None:
    status = str(job.get("status", "")).lower()
    iteration_count = int(job.get("iteration_count", 0) or 0)
    max_iterations = int(job.get("max_iterations", 0) or 0)
    if status == "exhausted":
        st.warning("Exhausted — manual review required.")
    if status == "paused":
        node = str(job.get("paused_at_node") or "unknown node")
        st.warning(f"Stuck at {node} — awaiting intervention.")
    if status in {"failed", "coding", "testing"} and 0 < iteration_count < max_iterations:
        st.info(f"Retry in progress (iteration {iteration_count} of {max_iterations})")


# ── Page ─────────────────────────────────────────────────────────────────────

st.title("Job Detail")
job_id = st.text_input("Job ID")

if not job_id:
    st.stop()

try:
    resp = requests.get(f"{API_URL}/jobs/{job_id}", timeout=10)
except requests.RequestException as exc:
    st.error(f"Failed to load job: {exc}")
    st.stop()

if resp.status_code == 404:
    st.error(f"Job not found: {job_id}")
    st.stop()

resp.raise_for_status()
job: dict[str, Any] = resp.json()

status = str(job.get("status", "pending")).lower()

# ── Header row ───────────────────────────────────────────────────────────────
header_left, header_right = st.columns([3, 1])
with header_left:
    story_id = job.get("story_id") or job.get("story_content", {}).get("feature_id", "")
    title = job.get("story_content", {}).get("title", "")
    st.markdown(f"### {story_id}" + (f" — {title}" if title else ""))
    st.markdown(badge(status), unsafe_allow_html=True)
with header_right:
    repo_url = job.get("repo_url")
    pr_url = job.get("pr_url")
    if repo_url:
        st.link_button("Open Repo", repo_url)
    if pr_url:
        st.link_button("Open PR", pr_url)

render_alerts(job)

# ── Progress bar ─────────────────────────────────────────────────────────────
st.divider()
pipeline_progress(status)

# ── Metrics row ──────────────────────────────────────────────────────────────
st.divider()
trace = job.get("execution_trace", [])
total_in = sum(int(t.get("input_tokens", 0)) for t in trace)
total_out = sum(int(t.get("output_tokens", 0)) for t in trace)
total_cost = sum(float(t.get("cost_usd", 0.0)) for t in trace)
total_dur = sum(int(t.get("duration_ms", 0)) for t in trace)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Input tokens", f"{total_in:,}")
m2.metric("Output tokens", f"{total_out:,}")
m3.metric("Est. cost", f"${total_cost:.4f}")
m4.metric("Node time", f"{total_dur / 1000:.1f}s")
m5.metric("Elapsed", elapsed(job.get("created_at"), job.get("updated_at")))

# ── Alerts / errors ──────────────────────────────────────────────────────────
error_logs = job.get("error_logs", [])
if error_logs:
    with st.expander("Error logs", expanded=status in {"failed", "exhausted"}):
        for e in error_logs:
            st.text(e)

# ── Skills + provider ────────────────────────────────────────────────────────
st.divider()
sk_col, prov_col = st.columns([3, 2])

with sk_col:
    st.markdown("**Skills retrieved**")
    skills_pool = job.get("skills_pool", [])
    if skills_pool:
        for item in skills_pool:
            skill = item.get("skill", {})
            score = float(item.get("score", 0.0))
            bar_pct = min(int(score * 100), 100)
            name = skill.get("name", "—")
            category = skill.get("category", "")
            st.markdown(
                f"<div style='margin-bottom:0.4rem'>"
                f"<span style='font-weight:600'>{name}</span>"
                f"<span style='color:#888;font-size:0.8rem'> {category}</span><br>"
                f"<div style='background:#e9ecef;border-radius:3px;height:6px;width:100%'>"
                f"<div style='background:#0d6efd;width:{bar_pct}%;height:6px;border-radius:3px'></div></div>"
                f"<span style='font-size:0.75rem;color:#555'>similarity {score:.3f}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("None yet")

with prov_col:
    st.markdown("**LLM providers**")
    for entry in job.get("provider_log", []):
        node, _, provider = str(entry).partition(":")
        st.markdown(f"- `{node}` → {provider}")

    st.markdown("**Tech stack**")
    for t in job.get("tech_stack", []):
        st.markdown(f"- {t}")

# ── Execution trace ──────────────────────────────────────────────────────────
st.divider()
if trace:
    with st.expander("Execution trace", expanded=True):
        df = pd.DataFrame([
            {
                "node": t.get("node"),
                "status": t.get("status_after"),
                "provider": t.get("provider") or "—",
                "dur (ms)": t.get("duration_ms"),
                "iter": t.get("iteration"),
                "in tok": t.get("input_tokens", 0),
                "out tok": t.get("output_tokens", 0),
                "cost $": f"{float(t.get('cost_usd', 0)):.4f}",
                "error": t.get("error") or "",
            }
            for t in trace
        ])
        st.dataframe(df, use_container_width=True)

# ── Generated files ───────────────────────────────────────────────────────────
generated = job.get("generated_files", {})
if generated:
    with st.expander(f"Generated files ({len(generated)})", expanded=False):
        for path, content in generated.items():
            st.markdown(f"**`{path}`**")
            lang = "python" if path.endswith(".py") else "text"
            st.code(content, language=lang)

# ── Raw state ────────────────────────────────────────────────────────────────
with st.expander("Full job state", expanded=False):
    st.json(job)
