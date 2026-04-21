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
TERMINAL_STAGES = {"committed", "failed", "exhausted", "paused", "rejected"}
# PLANNING is intentionally excluded from TERMINAL_STAGES so auto-refresh continues after approval

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


def badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#495057")
    return (
        f"<span style='background:{color};color:white;"
        "padding:0.3rem 0.7rem;border-radius:0.4rem;font-size:0.9rem;font-weight:600;'>"
        f"{status.upper()}</span>"
    )


def pipeline_progress(status: str) -> None:
    # planning/rejected: show progress at skills_retrieved position
    display_status = "skills_retrieved" if status in {"planning", "rejected"} else status
    try:
        step = PIPELINE_STAGES.index(display_status)
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


def elapsed(created_at: str | None, status: str) -> str:
    if not created_at:
        return "—"
    try:
        t0 = datetime.fromisoformat(created_at.split("+")[0].rstrip("Z"))
        t1 = datetime.utcnow()  # always use wall clock so active jobs keep ticking
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


def post_job_action(job_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
    try:
        response = requests.post(
            f"{API_URL}/jobs/{job_id}/{action}",
            json=payload if payload is not None else {},
            timeout=10,
        )
    except requests.RequestException as exc:
        st.error(f"Request failed: {exc}")
        return

    if response.status_code in {200, 201, 202}:
        content_type = response.headers.get("content-type", "")
        body = response.json() if content_type.startswith("application/json") else {}
        new_job_id = body.get("job_id")
        if action == "retry" and new_job_id:
            st.success(f"Retry queued as job {new_job_id}.")
        else:
            st.success(f"{action.capitalize()} queued.")
        st.rerun()
        return

    if response.status_code == 404:
        st.info(f"{action.capitalize()} endpoint not available yet.")
        return

    st.error(f"{action.capitalize()} failed ({response.status_code}): {response.text}")


def render_recovery_actions(job_id: str, status: str) -> None:
    if status == "paused":
        st.divider()
        if st.button("Resume", type="primary"):
            post_job_action(job_id, "resume")
        return

    if status in {"failed", "exhausted"}:
        st.divider()
        st.markdown("**Retry Job**")
        patch_instructions = st.text_area(
            "Patch instructions (optional)",
            placeholder="Add any guidance for the retry run.",
            height=90,
            key=f"retry_patch_{job_id}",
        )
        if st.button("Retry", type="primary"):
            payload: dict[str, Any] = {}
            if patch_instructions.strip():
                payload["patch_instructions"] = patch_instructions.strip()
            post_job_action(job_id, "retry", payload)


# ── Page ─────────────────────────────────────────────────────────────────────

st.title("Job Detail")

# Support navigation from Jobs Queue via session state or query params
_default_id = st.session_state.get("selected_job_id", "") or st.query_params.get("job_id", "")
job_id = st.text_input("Job ID", value=_default_id)

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

# Post-approval indicator: plan approved but codegen hasn't started yet
plan_data = job.get("implementation_plan")
if plan_data and plan_data.get("status") == "approved" and status == "coding":
    st.info("Plan approved — codegen running.", icon="⏳")

render_recovery_actions(job_id, status)

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
m5.metric("Elapsed", elapsed(job.get("created_at"), status))

# ── Plan Review (PLANNING state) ─────────────────────────────────────────────
plan = job.get("implementation_plan")
if plan and status == "planning":
    st.divider()
    st.markdown(
        "<div style='background:#2d2400;border:1px solid #a07800;border-radius:6px;"
        "padding:1rem 1.2rem;margin-bottom:0.5rem'>"
        "<b style='font-size:1rem;color:#ffd54f'>⏳ Plan Review — awaiting your approval</b>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("**Requirements understood**")
    st.info(plan.get("requirements_brief", ""))

    st.markdown("**Approach**")
    st.write(plan.get("approach", ""))

    files = plan.get("files", [])
    if files:
        st.markdown("**Files**")
        action_colors = {"create": "#198754", "modify": "#fd7e14", "delete": "#dc3545"}
        for f in files:
            action = str(f.get("action", "create")).lower()
            color = action_colors.get(action, "#6c757d")
            st.markdown(
                f"<span style='background:{color};color:white;padding:0.1rem 0.4rem;"
                f"border-radius:3px;font-size:0.75rem;font-weight:600'>{action.upper()}</span> "
                f"`{f.get('path', '')}` — {f.get('description', '')}",
                unsafe_allow_html=True,
            )

    st.markdown("**Estimates**")
    ec1, ec2, ec3 = st.columns(3)
    ec1.metric("Input tokens", f"{plan.get('estimated_input_tokens', 0):,}")
    ec2.metric("Output tokens", f"{plan.get('estimated_output_tokens', 0):,}")
    ec3.metric("Est. cost", f"${plan.get('estimated_cost_usd', 0):.4f}")

    st.markdown("")
    approve_col, reject_col, _ = st.columns([1, 1, 3])
    with approve_col:
        if st.button("Approve", type="primary", use_container_width=True):
            try:
                r = requests.post(f"{API_URL}/jobs/{job_id}/approve-plan", timeout=10)
                if r.status_code == 200:
                    st.success("Approved — job queued for codegen.")
                    st.rerun()
                else:
                    st.error(f"Error: {r.text}")
            except requests.RequestException as exc:
                st.error(str(exc))
    with reject_col:
        if st.button("Reject", type="secondary", use_container_width=True):
            st.session_state["show_reject_form"] = True

    if st.session_state.get("show_reject_form"):
        with st.form("reject_form"):
            reason = st.text_area("Reason (optional — will be shown on retry)", height=80)
            submitted = st.form_submit_button("Confirm Rejection")
            if submitted:
                try:
                    r = requests.post(
                        f"{API_URL}/jobs/{job_id}/reject-plan",
                        json={"reason": reason},
                        timeout=10,
                    )
                    if r.status_code == 200:
                        st.warning("Plan rejected. Refine your spec and submit a new job.")
                        st.session_state["show_reject_form"] = False
                        st.rerun()
                    else:
                        st.error(f"Error: {r.text}")
                except requests.RequestException as exc:
                    st.error(str(exc))

elif plan and status == "rejected":
    st.divider()
    st.error(f"Plan rejected. Reason: {plan.get('rejection_reason') or 'none provided'}")
    st.caption("Refine the feature spec and submit a new job.")

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

# ── Log stream ────────────────────────────────────────────────────────────────
st.divider()
_LOG_LEVEL_COLORS = {
    "INFO":     ("#0dcaf0", "#000"),
    "WARNING":  ("#ffc107", "#000"),
    "ERROR":    ("#dc3545", "#fff"),
    "CRITICAL": ("#842029", "#fff"),
    "DEBUG":    ("#6c757d", "#fff"),
}

log_expanded = status not in TERMINAL_STAGES
with st.expander(f"Log stream", expanded=log_expanded):
    try:
        log_resp = requests.get(f"{API_URL}/jobs/{job_id}/logs", params={"n": 200}, timeout=5)
        log_data = log_resp.json() if log_resp.status_code == 200 else {"lines": []}
    except Exception:
        log_data = {"lines": []}

    log_lines = log_data.get("lines", [])
    if not log_lines:
        st.caption("No log lines captured yet.")
    else:
        rows = []
        for entry in reversed(log_lines):
            level = entry.get("level", "INFO")
            bg, fg = _LOG_LEVEL_COLORS.get(level, ("#6c757d", "#fff"))
            badge = (
                f"<span style='background:{bg};color:{fg};padding:0.1rem 0.35rem;"
                f"border-radius:3px;font-size:0.7rem;font-weight:700'>{level}</span>"
            )
            node_tag = (
                f" <span style='color:#6cf;font-size:0.72rem'>[{entry['node']}]</span>"
                if entry.get("node") else ""
            )
            ts = entry.get("ts", "")[-12:]  # just HH:MM:SS.mmm
            msg = entry.get("msg", "").replace("<", "&lt;").replace(">", "&gt;")
            rows.append(
                f"<div style='font-family:monospace;font-size:0.76rem;"
                f"padding:0.1rem 0;border-bottom:1px solid #1a1a1a;white-space:pre-wrap'>"
                f"<span style='color:#444;margin-right:0.4rem'>{ts}</span>"
                f"{badge}{node_tag} "
                f"<span style='color:#ddd'>{msg}</span></div>"
            )
        st.markdown(
            "<div style='max-height:400px;overflow-y:auto;background:#111;"
            "padding:0.5rem;border-radius:4px'>" + "\n".join(rows) + "</div>",
            unsafe_allow_html=True,
        )
        st.caption(f"{len(log_lines)} log lines")

# ── Live refresh for active jobs ─────────────────────────────────────────────
if status not in TERMINAL_STAGES:
    import time
    st.caption(f"Auto-refreshing every 3s — status: {status}")
    time.sleep(3)
    st.rerun()
