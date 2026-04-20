from __future__ import annotations

import os

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")

st.set_page_config(page_title="Skillnet Pipeline", layout="wide")
st.title("Skillnet Pipeline")
st.markdown("Use the sidebar to navigate between pages.")

try:
    resp = requests.get(f"{API_URL}/health", timeout=5)
    resp.raise_for_status()
    data = resp.json()
    st.success(f"API healthy — {data.get('skills_indexed', 0)} skills indexed.")
except requests.RequestException:
    st.warning("API unreachable. Start the pipeline with `docker compose up`.")
