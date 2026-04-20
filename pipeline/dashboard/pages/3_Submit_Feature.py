from __future__ import annotations

import os

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")

st.title("Submit Feature")

feature_markdown = st.text_area("Feature Specification (markdown)", height=300)

if st.button("Submit"):
    try:
        response = requests.post(
            f"{API_URL}/ingest/feature/markdown",
            data=feature_markdown,
            headers={"Content-Type": "text/plain"},
            timeout=10,
        )
    except requests.RequestException as exc:
        st.error(f"Request failed: {exc}")
    else:
        if response.status_code == 200:
            payload = response.json()
            st.success(f"Job created: {payload['job_id']}")
            st.markdown("[Go to Jobs page](./)")
        elif response.status_code == 409:
            st.warning("This feature has already been submitted.")
        else:
            st.error(f"Error {response.status_code}: {response.text}")
