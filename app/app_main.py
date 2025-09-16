# -*- coding: utf-8 -*-
import os
import streamlit as st

from chat_page import render_chat_page
from social_editor_page import render_social_editor_page
from environment_editor_page import render_environment_editor_page
from targets_editor_page import render_targets_editor_page

st.set_page_config(
    page_title="Ollama Agent (LangChain + Streamlit)",
    page_icon="🦙",
    layout="wide",
)

# -----------------------------------------------------------------------------
# Sidebar: navigazione tra sezioni
# -----------------------------------------------------------------------------
st.sidebar.title("📂 Sezioni")
section = st.sidebar.radio(
    "Vai a…",
    ["💬 Chat", "👥 Dati Social", "🌿 Dati Ambientali", "🎯 Target KPI"],
    index=0,
)

# Inizializza stato chat se serve (conservato tra sezioni)
if "messages" not in st.session_state:
    st.session_state.messages = []

# Router semplice
if section == "💬 Chat":
    render_chat_page()
elif section == "👥 Dati Social":
    render_social_editor_page()
elif section == "🌿 Dati Ambientali":
    render_environment_editor_page()
elif section == "🎯 Target KPI":
    render_targets_editor_page()
