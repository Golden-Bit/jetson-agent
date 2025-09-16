# -*- coding: utf-8 -*-
"""
Streamlit UI per agente LangChain + Ollama (OpenAI-compat) con:
- Streaming token-by-token
- Parser <think>...</think> token-safe (un solo placeholder, niente duplicati)
- Expander "🧠 Thinking" e "🔧 Eseguendo strumento: ..." solo se presenti
- Switch per mostrare/nascondere Thinking/Tool
- Persistenza di Thinking e Tool nello storico dei messaggi
- Supporto MULTI-TOOL (mappa run_id → expander corretto)
- Compatibile con versioni Streamlit che non supportano `key` in st.expander()
"""
import json
import os
# Non filtriamo i blocchi <think> nel core, così la UI li visualizza.
os.environ.setdefault("HIDE_THINK", "false")

import asyncio
import streamlit as st
from typing import Dict, Any, List, Tuple

# Se il tuo core si chiama agent_core, cambia qui l'import.
from utils.utils import event_stream, MODEL

st.set_page_config(
    page_title="Ollama Agent (LangChain + Streamlit)",
    page_icon="🦙",
    layout="wide",
)


def _truncate64(value: Any) -> str:
    """Converte in stringa (JSON se dict/list) e tronca a 64 caratteri + '.....'."""
    try:
        if isinstance(value, (dict, list, tuple)):
            s = json.dumps(value, ensure_ascii=False)
        else:
            s = str(value)
    except Exception:
        s = repr(value)
    return s[:64] + ".....(OUTPUT TRONCATO A 64 CARATTERI, SE SI NECESSITA NUOVAMENTE L'OUTPUT INTEGRALE ALLORA RIESEGUIRE LOS TRUMENTO!)"

def _hidden_tool_trace_for_msg(msg: Dict[str, Any]) -> str:
    """
    Crea un blocco *nascosto per la UI* ma visibile all'LLM con i tool usati nel messaggio.
    Formato:
      <!-- TOOL_TRACE_START
      [tool] <name>
      inputs: <json>
      output_truncated: <64ch>.....
      TOOL_TRACE_END -->
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
    Ritorna una *copia* della history con il riepilogo strumenti appeso nel testo.
    - Se flatten_assistant=True, i messaggi 'assistant' diventano 'user' (compat con utils che
      forwarda spesso solo HumanMessage); altrimenti mantiene i ruoli originali.
    - NON modifica st.session_state.messages.
    """
    llm_hist: List[Dict[str, Any]] = []
    for m in ui_messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        # Appendi il blocco tool nascosto solo se ci sono strumenti nel messaggio
        tool_block = _hidden_tool_trace_for_msg(m)
        content_aug = (content + ("\n" + tool_block if tool_block else "")).strip()
        if flatten_assistant and role == "assistant":
            role = "user"
        llm_hist.append({"role": role, "content": content_aug})
    return llm_hist


# -----------------------------------------------------------------------------
# Sidebar: impostazioni / switch visibilità
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("⚙️ Impostazioni")
    st.caption("Ollama OpenAI-compat: usa ENV OPENAI_BASE_URL / OPENAI_API_KEY")
    st.write(f"**Modello:** `{MODEL}`")

    show_thinking = st.toggle(
        "Mostra blocchi Thinking",
        value=True,
        help="Se attivo, mostra un expander '🧠 Thinking' col contenuto tra <think>...</think>.",
    )
    show_tools = st.toggle(
        "Mostra log strumenti",
        value=True,
        help="Se attivo, mostra expander per ciascun tool invocato (input/output).",
    )

    if st.button("🧹 Svuota chat"):
        st.session_state.messages = []
        st.rerun()

# -----------------------------------------------------------------------------
# Stato iniziale chat (persistiamo anche 'think' e 'tools' nei messaggi assistant)
# -----------------------------------------------------------------------------
# Schema messaggio:
#   {"role": "user"|"assistant", "content": str, "think": str|None, "tools": list|None}
if "messages" not in st.session_state:
    st.session_state.messages: List[Dict[str, Any]] = []

st.title("🦙 Ollama + LangChain (Tool Calling) — Streamlit")
st.caption("Chat con streaming, blocchi Thinking e log strumenti in-line (multi-tool).")

# -----------------------------------------------------------------------------
# Rendering storico (inclusi Thinking/Tool salvati)
# -----------------------------------------------------------------------------
def render_message(m: Dict[str, Any]):
    with st.chat_message(m["role"]):
        st.markdown(m.get("content", ""))

        if m["role"] == "assistant":
            # Thinking persistito
            if show_thinking and m.get("think"):
                # Niente `key=`: versioni Streamlit meno recenti non lo supportano su expander
                with st.expander("🧠 Thinking", expanded=False):
                    st.markdown(m["think"])

            # Tool persistiti
            if show_tools and m.get("tools"):
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

for m in st.session_state.messages:
    render_message(m)

# -----------------------------------------------------------------------------
# Helpers per lo stato della chat
# -----------------------------------------------------------------------------
def append_user_message(content: str):
    st.session_state.messages.append(
        {"role": "user", "content": content, "think": None, "tools": None}
    )

def append_assistant_message(content: str, think: str | None, tools: list | None):
    st.session_state.messages.append(
        {"role": "assistant", "content": content, "think": think, "tools": tools}
    )

# -----------------------------------------------------------------------------
# Parser in streaming per separare testo vs <think>...</think> (token-safe)
# -----------------------------------------------------------------------------
def stream_split_think(chunk: str, state: Dict[str, Any]) -> Tuple[str, str]:
    """
    Aggiorna lo stato e ritorna (visible_text_delta, think_text_delta).
    Stato richiesto: {'in_think': bool}
    Gestisce tag spezzati sui token.
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

# -----------------------------------------------------------------------------
# Esecuzione agente + stream in UI (multi-tool con run_id)
# -----------------------------------------------------------------------------
async def run_agent_and_stream(ui_history: List[Dict[str, Any]], user_text: str) -> Dict[str, Any]:
    """
    Consuma event_stream(...) e aggiorna la UI:
      - scrive i token nel messaggio assistant
      - intercetta i blocchi <think> (expander "🧠 Thinking", un SOLO placeholder aggiornato)
      - per ogni tool call crea un expander dedicato, mappato su run_id
    Ritorna:
      {"text": <risposta_senza_think>, "think": <contenuto_think_o_vuoto>, "tools": <lista_tool_call>}
    """
    final_text = ""
    think_text = ""
    tool_calls: List[Dict[str, Any]] = []  # [{name, inputs, output}...]

    # Stato parser <think>
    parse_state = {"in_think": False}

    with st.chat_message("assistant"):
        # Placeholder principale per il testo
        text_ph = st.empty()

        # Thinking (creato solo alla prima necessità)
        think_expander = None
        think_ph = None  # st.empty() interno all'expander

        # Contenitore strumenti (creato alla prima tool-call se visibile)
        tools_container = None

        # Mappa dei tool in corso: run_id -> placeholders & index in tool_calls
        tool_placeholders: Dict[str, Dict[str, Any]] = {}
        # Esempio:
        # tool_placeholders[run_id] = {
        #   "idx": int, "exp": <st.expander>, "input_ph": st.empty(), "output_ph": st.empty()
        # }

        async for ev in event_stream(user_text, ui_history):
            et = ev.get("type")

            # --- token di testo (potrebbe contenere <think>) ---
            if et == "token":
                chunk = ev.get("text", "")
                vis, th = stream_split_think(chunk, parse_state)

                if vis:
                    final_text += vis
                    text_ph.markdown(final_text)

                if (th or parse_state["in_think"]) and show_thinking:
                    if think_expander is None:
                        think_expander = st.expander("🧠 Thinking", expanded=False)
                        think_ph = think_expander.empty()
                    if th:
                        think_text += th
                        think_ph.markdown(think_text)

            # --- inizio tool: crea expander dedicato SOLO all'arrivo dell'evento ---
            elif et == "tool_start":
                # Normalizza campi: alcune versioni emettono 'input', altre 'inputs'
                tname = ev.get("name", "tool")
                trun  = ev.get("run_id") or f"run_{len(tool_calls)}"
                tinp  = ev.get("inputs")
                if tinp is None:
                    tinp = ev.get("input")  # fallback
                if tinp is None:
                    tinp = {}

                # Aggiungi un record persistente
                tool_calls.append({"name": tname, "inputs": tinp, "output": None})
                idx = len(tool_calls) - 1

                # UI solo se abilitata
                if show_tools:
                    if tools_container is None:
                        tools_container = st.container()

                    # Expander per questo tool
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
                    exp = None
                    input_ph = None
                    output_ph = None

                # Mappa run_id → placeholders e indice
                tool_placeholders[trun] = {
                    "idx": idx,
                    "exp": exp,
                    "input_ph": input_ph,
                    "output_ph": output_ph,
                }

            # --- fine tool: aggiorna l'expander giusto tramite run_id ---
            elif et == "tool_end":
                trun  = ev.get("run_id")
                tname = ev.get("name", "tool")
                tout  = ev.get("output", "")
                tinp  = ev.get("inputs")
                if tinp is None:
                    tinp = ev.get("input")  # fallback
                if tinp is None:
                    tinp = {}

                # Se non abbiamo visto il tool_start, creiamo al volo
                if trun not in tool_placeholders:
                    tool_calls.append({"name": tname, "inputs": tinp, "output": None})
                    idx = len(tool_calls) - 1

                    if show_tools:
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
                        exp = None
                        input_ph = None
                        output_ph = None

                    tool_placeholders[trun] = {
                        "idx": idx,
                        "exp": exp,
                        "input_ph": input_ph,
                        "output_ph": output_ph,
                    }

                # Aggiorna UI + stato persistente
                ph = tool_placeholders[trun]
                if show_tools and ph["output_ph"] is not None:
                    ph["output_ph"].code(tout, language="json")
                tool_calls[ph["idx"]]["output"] = tout

            # --- errore: mostra e prosegui ---
            elif et == "error":
                err = f"**[Errore]** {ev.get('message')}"
                final_text += ("\n\n" + err)
                text_ph.markdown(final_text)
                if show_tools:
                    if tools_container is None:
                        tools_container = st.container()
                    tools_container.error(err)

            # --- fine della run ---
            elif et == "done":
                break

    return {
        "text": final_text.strip(),
        "think": think_text.strip() if think_text else "",
        "tools": tool_calls if tool_calls else [],
    }

# -----------------------------------------------------------------------------
# Input utente + invio
# -----------------------------------------------------------------------------
user_text = st.chat_input("Scrivi un messaggio...")

if user_text:
    # Mostra subito il messaggio utente e persistilo
    with st.chat_message("user"):
        st.markdown(user_text)
    append_user_message(user_text)

    # Esegui agente e streamma la risposta (Streamlit è sync → asyncio.run)
    # Costruisci la history *solo per l'LLM*, con i tool inseriti nel testo (nascosti alla UI)
    llm_history = build_llm_history(st.session_state.messages, flatten_assistant=True)

    # Passa la history arricchita all'agente (la UI continua a renderizzare la history originale)
    result = asyncio.run(run_agent_and_stream(llm_history, user_text))

    # Persisti risposta assistant con Thinking/Tools salvati
    append_assistant_message(
        content=result["text"],
        think=result["think"],
        tools=result["tools"],
    )
