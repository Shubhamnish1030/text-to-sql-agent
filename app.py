"""Streamlit web UI.  Run with:  streamlit run app.py"""

import os
from pathlib import Path

import anthropic
import streamlit as st

from t2s import TextToSQLAgent, open_database

DB_TARGET = os.environ.get("T2S_DB", str(Path(__file__).parent / "data" / "chinook.db"))

st.set_page_config(page_title="Text-to-SQL Agent", layout="wide")
st.title("Text-to-SQL Agent")
st.caption("Ask a question about your data in plain English. The agent explores the schema, tests read-only queries and answers.")


@st.cache_resource
def get_agent(effort: str) -> TextToSQLAgent:
    return TextToSQLAgent(open_database(DB_TARGET, schema=os.environ.get("T2S_SCHEMA")), effort=effort)


with st.sidebar:
    effort = st.selectbox("Effort", ["low", "medium", "high"], index=1)
    st.markdown("**Try:**")
    examples = [
        "Which 5 artists have the most tracks?",
        "Total sales by country, top 5",
        "Which employee has the highest total sales from their customers?",
        "What share of tracks are longer than 5 minutes?",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state.question = ex

question = st.text_input("Question", key="question", placeholder="e.g. Which genre earns the most revenue?")

if question:
    agent = get_agent(effort)
    with st.status("Thinking...", expanded=True) as status:
        def show(step):
            icon = "❌" if step.is_error else "🔧"
            st.write(f"{icon} `{step.summary()[:200]}`")
        try:
            res = agent.ask(question, on_step=show)
        except (anthropic.AuthenticationError, TypeError) as e:
            if isinstance(e, TypeError) and "authentication" not in str(e).lower():
                raise
            st.error("No valid API key: export ANTHROPIC_API_KEY before starting the app.")
            st.stop()
        status.update(label=f"Done in {res.turns} model calls", state="complete", expanded=False)

    st.subheader("Answer")
    st.write(res.answer)
    if res.sql:
        st.code(res.sql, language="sql")
    if res.result and res.result.columns:
        st.dataframe([dict(zip(res.result.columns, r)) for r in res.result.rows], use_container_width=True)
    st.caption(f"{res.input_tokens} input / {res.output_tokens} output tokens")
