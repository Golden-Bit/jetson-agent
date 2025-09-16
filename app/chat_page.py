# -*- coding: utf-8 -*-
"""
Sezione Chat (aggiornata con selettore agente):
- Streaming token-by-token
- Parser <think>...</think> (token-safe)
- Expander '🧠 Thinking' e log strumenti
- Persistenza messaggi (incl. think/tools)
- Traccia strumenti nascosta incorporata nella history inviata all'LLM
- Selectbox in sidebar per scegliere l'agente: ENV | SOCIAL | DSS
  ➜ il cambio agente **non** cancella la chat history; la chat resta visibile/riutilizzabile
"""
import os
import json
import asyncio
import streamlit as st
from typing import Dict, Any, List, Tuple

# Non filtriamo i blocchi <think> nel core, così la UI li visualizza (puoi cambiarlo da ENV)
os.environ.setdefault("HIDE_THINK", "false")

# Import core (con event_stream(user_text, history, mode))
from utils.utils import event_stream, MODEL


# ============================== Utility ======================================

def _truncate64(value: Any) -> str:
    """Converte in stringa (JSON se dict/list) e tronca a 64 caratteri + hint."""
    try:
        if isinstance(value, (dict, list, tuple)):
            s = json.dumps(value, ensure_ascii=False)
        else:
            s = str(value)
    except Exception:
        s = repr(value)
    return s[:64] + ".....(OUTPUT TRONCATO A 64 CARATTERI, SE SI NECESSITA NUOVAMENTE L'OUTPUT INTEGRALE ALLORA RIESEGUIRE LO STRUMENTO!)"

def _hidden_tool_trace_for_msg(msg: Dict[str, Any]) -> str:
    """
    Blocca una traccia strumenti nel testo (invisibile in UI, visibile all'LLM).
    """
    tools = msg.get("tools") or []
    if not tools:
        return ""
    lines = ["", "<!-- TOOL_TRACE_START"]
    for t in tools:
        name = t.get("name", "tool")
        inputs = t.get("inputs", {})
        try:
            inputs_json = json.dumps(inputs, ensure_ascii=False)
        except Exception:
            inputs_json = str(inputs)
        out_trunc = _truncate64(t.get("output", ""))
        lines.append(f"[tool] {name}")
        lines.append(f"inputs: {inputs_json}")
        lines.append(f"output_truncated: {out_trunc}")
    lines.append("TOOL_TRACE_END -->")
    return "\n".join(lines)

def build_llm_history(ui_messages: List[Dict[str, Any]], flatten_assistant: bool = True) -> List[Dict[str, Any]]:
    """
    Ritorna una *copia* della history con il riepilogo strumenti appeso nel testo,
    opzionalmente "appiattendo" i messaggi assistant come 'user' per compatibilità con il core.
    """
    llm_hist: List[Dict[str, Any]] = []
    for m in ui_messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        tool_block = _hidden_tool_trace_for_msg(m)
        content_aug = (content + ("\n" + tool_block if tool_block else "")).strip()
        if flatten_assistant and role == "assistant":
            role = "user"
        llm_hist.append({"role": role, "content": content_aug})
    return llm_hist


# ============================== Stato & Costanti =============================

AGENT_OPTIONS = {
    "env":   {"label": "🌿 Report Ambientale (ENV)", "help": "KPI/trend ambientali e tabella report."},
    "social":{"label": "👥 Report Sociale (SOCIAL)", "help": "KPI/trend sociali e tabella report."},
    "dss":   {"label": "⚖️ DSS (AHP)",              "help": "Combina KPI ENV+SOC per ranking e score."},
}

def _ensure_session_defaults():
    if "messages" not in st.session_state:
        st.session_state.messages = []  # unica history condivisa tra modalità
    if "show_thinking" not in st.session_state:
        st.session_state.show_thinking = True
    if "show_tools" not in st.session_state:
        st.session_state.show_tools = True
    if "agent_mode" not in st.session_state:
        st.session_state.agent_mode = "env"  # default

# ============================== Pagina Chat ==================================

def render_chat_page():
    _ensure_session_defaults()

    st.title("Chat (LangChain + Tool Calling)")
    st.caption("Streaming, blocchi Thinking e log strumenti in-line (multi-tool).")

    # ------------------------- Sidebar: selezione agente + opzioni -----------
    st.sidebar.markdown("---")
    with st.sidebar.expander("Selezione agente", expanded=True):
        # Trova index corrente per selectbox
        modes = list(AGENT_OPTIONS.keys())
        current_idx = modes.index(st.session_state.agent_mode) if st.session_state.agent_mode in modes else 0

        # Selectbox con etichette leggibili
        new_idx = st.selectbox(
            "Modalità agente",
            options=list(range(len(modes))),
            index=current_idx,
            format_func=lambda i: AGENT_OPTIONS[modes[i]]["label"],
            help=AGENT_OPTIONS[modes[current_idx]]["help"],
            key="agent_mode_selectbox",
        )
        # Aggiorna modalità senza toccare la history
        st.session_state.agent_mode = modes[new_idx]
        st.caption(f"Modalità attiva: **{AGENT_OPTIONS[st.session_state.agent_mode]['label']}**")

    with st.sidebar.expander("⚙️ Opzioni Chat", expanded=True):
        st.caption("Ollama OpenAI-compat: usa ENV OPENAI_BASE_URL / OPENAI_API_KEY")
        st.write(f"**Modello:** `{MODEL}`")

        st.session_state.show_thinking = st.toggle(
            "Mostra blocchi Thinking",
            value=st.session_state.show_thinking,
            help="Se attivo, mostra un expander '🧠 Thinking' col contenuto tra <think>...</think>.",
        )
        st.session_state.show_tools = st.toggle(
            "Mostra log strumenti",
            value=st.session_state.show_tools,
            help="Se attivo, mostra un expander per ciascun tool invocato (input/output).",
        )

        cols = st.columns(2)
        with cols[0]:
            if st.button("🧹 Svuota chat"):
                st.session_state.messages = []
                st.experimental_rerun()
        with cols[1]:
            st.caption("La history resta invariata quando cambi agente.")

    # Badge modalità corrente sotto il titolo
    _mode_badge = {
        "env": "🌿 **ENV attivo** — Report Ambientale",
        "social": "👥 **SOCIAL attivo** — Report Sociale",
        "dss": "⚖️ **DSS attivo** — Analisi AHP",
    }
    st.markdown(_mode_badge.get(st.session_state.agent_mode, ""))

    # ------------------------- Rendering storico messaggi ---------------------
    for m in st.session_state.messages:
        _render_message(m)

    # ------------------------- Input utente -----------------------------------
    user_text = st.chat_input("Scrivi un messaggio per l’agente selezionato…")
    if user_text:
        # Mostra subito il messaggio utente e persistilo
        with st.chat_message("user"):
            st.markdown(user_text)
        _append_user_message(user_text)

        # Costruisci la history *per l'LLM*, con i tool inseriti nel testo (nascosti alla UI)
        llm_history = build_llm_history(st.session_state.messages, flatten_assistant=True)

        # Esegui agente e streamma la risposta (con modalità selezionata)
        result = asyncio.run(_run_agent_and_stream(llm_history, user_text, st.session_state.agent_mode))

        # Persisti risposta assistant con Thinking/Tools salvati
        _append_assistant_message(
            content=result["text"],
            think=result["think"],
            tools=result["tools"],
        )


# ===================== Helpers interni (solo per questa pagina) ==============

def _render_message(m: Dict[str, Any]):
    with st.chat_message(m["role"]):
        st.markdown(m.get("content", ""))

        if m["role"] == "assistant":
            # Thinking persistito
            if st.session_state.get("show_thinking") and m.get("think"):
                with st.expander("🧠 Thinking", expanded=False):
                    st.markdown(m["think"])

            # Tool persistiti
            if st.session_state.get("show_tools") and m.get("tools"):
                for t in m["tools"]:
                    exp = st.expander(
                        f"🔧 Eseguendo strumento: {t.get('name', 'tool')}",
                        expanded=False,
                    )
                    with exp:
                        st.markdown("**Input**")
                        st.code(t.get("inputs", {}), language="json")
                        st.markdown("**Output**")
                        st.code(t.get("output", ""), language="json")

def _append_user_message(content: str):
    st.session_state.messages.append(
        {"role": "user", "content": content, "think": None, "tools": None}
    )

def _append_assistant_message(content: str, think: str | None, tools: list | None):
    st.session_state.messages.append(
        {"role": "assistant", "content": content, "think": think, "tools": tools}
    )

def _stream_split_think(chunk: str, state: Dict[str, Any]) -> Tuple[str, str]:
    """
    Parser in streaming per separare testo vs <think>...</think> (token-safe).
    """
    visible_delta = ""
    think_delta = ""
    i = 0

    while i < len(chunk):
        if not state["in_think"]:
            start = chunk.find("<think>", i)
            if start == -1:
                visible_delta += chunk[i:]
                break
            visible_delta += chunk[i:start]
            i = start + len("<think>")
            state["in_think"] = True
        else:
            end = chunk.find("</think>", i)
            if end == -1:
                think_delta += chunk[i:]
                break
            think_delta += chunk[i:end]
            i = end + len("</think>")
            state["in_think"] = False

    return visible_delta, think_delta


async def _run_agent_and_stream(ui_history: List[Dict[str, Any]], user_text: str, mode: str) -> Dict[str, Any]:
    """
    Consuma event_stream(...) e aggiorna la UI:
      - scrive i token nel messaggio assistant
      - intercetta i blocchi <think>
      - crea expander per ciascun tool (multi-tool con run_id)
    Passa la modalità selezionata (env|social|dss) al core.
    """
    final_text = ""
    think_text = ""
    tool_calls: List[Dict[str, Any]] = []  # [{name, inputs, output}...]

    parse_state = {"in_think": False}

    with st.chat_message("assistant"):
        text_ph = st.empty()
        think_expander = None
        think_ph = None
        tools_container = None
        tool_placeholders: Dict[str, Dict[str, Any]] = {}

        async for ev in event_stream(user_text, ui_history, mode=mode):
            et = ev.get("type")

            if et == "token":
                chunk = ev.get("text", "")
                vis, th = _stream_split_think(chunk, parse_state)
                if vis:
                    final_text += vis
                    text_ph.markdown(final_text)
                if (th or parse_state["in_think"]) and st.session_state.get("show_thinking"):
                    if think_expander is None:
                        think_expander = st.expander("🧠 Thinking", expanded=False)
                        think_ph = think_expander.empty()
                    if th:
                        think_text += th
                        think_ph.markdown(think_text)

            elif et == "tool_start":
                tname = ev.get("name", "tool")
                trun  = ev.get("run_id") or f"run_{len(tool_calls)}"
                tinp  = ev.get("inputs") or ev.get("input") or {}

                tool_calls.append({"name": tname, "inputs": tinp, "output": None})
                idx = len(tool_calls) - 1

                if st.session_state.get("show_tools"):
                    if tools_container is None:
                        tools_container = st.container()
                    exp = tools_container.expander(
                        f"🔧 Eseguendo strumento: {tname}",
                        expanded=False,
                    )
                    with exp:
                        st.markdown("**Input**")
                        input_ph = st.empty()
                        st.markdown("**Output**")
                        output_ph = st.empty()
                    input_ph.code(tinp, language="json")
                else:
                    exp = input_ph = output_ph = None

                tool_placeholders[trun] = {
                    "idx": idx,
                    "exp": exp,
                    "input_ph": input_ph,
                    "output_ph": output_ph,
                }

            elif et == "tool_end":
                trun = ev.get("run_id")
                tname = ev.get("name", "tool")
                tout = ev.get("output", "")
                tinp = ev.get("inputs") or ev.get("input") or {}

                if trun not in tool_placeholders:
                    tool_calls.append({"name": tname, "inputs": tinp, "output": None})
                    idx = len(tool_calls) - 1
                    if st.session_state.get("show_tools"):
                        if tools_container is None:
                            tools_container = st.container()
                        exp = tools_container.expander(
                            f"🔧 Eseguendo strumento: {tname}",
                            expanded=False,
                        )
                        with exp:
                            st.markdown("**Input**")
                            input_ph = st.empty()
                            st.markdown("**Output**")
                            output_ph = st.empty()
                        input_ph.code(tinp, language="json")
                    else:
                        exp = input_ph = output_ph = None
                    tool_placeholders[trun] = {
                        "idx": idx, "exp": exp, "input_ph": input_ph, "output_ph": output_ph
                    }

                ph = tool_placeholders[trun]
                if st.session_state.get("show_tools") and ph["output_ph"] is not None:
                    ph["output_ph"].code(tout, language="json")
                tool_calls[ph["idx"]]["output"] = tout

            elif et == "error":
                err = f"**[Errore]** {ev.get('message')}"
                final_text += ("\n\n" + err)
                text_ph.markdown(final_text)
                if st.session_state.get("show_tools"):
                    if tools_container is None:
                        tools_container = st.container()
                    tools_container.error(err)

            elif et == "done":
                break

    return {
        "text": final_text.strip(),
        "think": think_text.strip() if think_text else "",
        "tools": tool_calls if tool_calls else [],
    }
