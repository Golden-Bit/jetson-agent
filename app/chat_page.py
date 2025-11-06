# -*- coding: utf-8 -*-
"""
Sezione Chat (senza LangChain nel core; supporta reasoning separato) + Gestione Chat Multiple
- Streaming token-by-token
- Supporta **due** modalità di reasoning:
  1) NUOVO core → eventi `{"type":"token","kind":"reasoning","text":...}`
  2) FALLBACK → reasoning incluso nel content tramite tag <think>...</think>
- Expander '🧠 Thinking' e log strumenti
- Persistenza messaggi (incl. think/tools) e traccia strumenti nascosta nella history inviata al LLM
- Selectbox per scegliere l'agente: ENV | SOCIAL | DSS (la history non viene resettata)
- **Nuova Chat** (pulsante in fondo alla sidebar)
- **Gestione Chat** (expander in sidebar con lista “scrollabile”, rinomina, elimina, apri)
- **Salvataggio automatico** su file JSON (cartella configurabile) ad ogni fine messaggio (utente/assistant)

Dipendenze:
- `utils.utils.event_stream` (core che chiama OpenAI SDK/Ollama-compat)
- `utils.utils.MODEL`

Note
----
- Il toggle "Mostra blocchi Thinking" controlla solo la **visualizzazione** in UI.
  Il reasoning viene comunque salvato nel messaggio assistant (campo `think`).
- Le chat sono salvate in JSON nella cartella CHATS_DIR (default: ./app/chats).
"""

import os
import json
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple

import streamlit as st

# Non filtriamo i blocchi <think> nel core, così la UI li può visualizzare se arrivano nel content
os.environ.setdefault("HIDE_THINK", "false")

# Import core (con event_stream(user_text, history, mode))
from utils.utils import event_stream, MODEL


# ============================== Costanti & Storage ============================

# Cartella dove salvare i file JSON delle chat (sovrascrivibile via ENV)
CHATS_DIR = Path(os.getenv("CHATS_DIR", Path.cwd() / "app" / "chats"))
CHATS_DIR.mkdir(parents=True, exist_ok=True)

# Nome file helper
def _chat_file_path(chat_id: str) -> Path:
    return CHATS_DIR / f"{chat_id}.json"


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
    Inserisce una traccia strumenti nel testo (invisibile in UI, visibile all'LLM).
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


def build_llm_history(ui_messages: List[Dict[str, Any]], *, flatten_assistant: bool = False) -> List[Dict[str, Any]]:
    """
    Ritorna una *copia* della history con il riepilogo strumenti appeso nel testo.
    Se `flatten_assistant=True`, i messaggi assistant diventano 'user' (legacy). Di default **NO**.
    """
    llm_hist: List[Dict[str, Any]] = []
    for m in ui_messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        tool_block = _hidden_tool_trace_for_msg(m)
        content_aug = (content + ("\n" + tool_block if tool_block else "")).strip()
        if flatten_assistant and role == "assistant":
            role = "user"
        # Normalizza minimo indispensabile
        if role not in ("user", "assistant", "system"):
            role = "user"
        llm_hist.append({"role": role, "content": content_aug})
    return llm_hist


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ============================== Stato & Costanti UI ==========================

AGENT_OPTIONS = {
    "env":   {"label": "🌿 Report Ambientale (ENV)", "help": "KPI/trend ambientali e tabella report."},
    "social":{"label": "👥 Report Sociale (SOCIAL)", "help": "KPI/trend sociali e tabella report."},
    "dss":   {"label": "⚖️ DSS (AHP)",              "help": "Combina KPI ENV+SOC per ranking e score."},
}

def _ensure_session_defaults():
    # --- preferenze UI ---
    if "messages" not in st.session_state:
        st.session_state.messages = []  # messaggi della chat corrente
    if "show_thinking" not in st.session_state:
        st.session_state.show_thinking = True
    if "show_tools" not in st.session_state:
        st.session_state.show_tools = True
    if "agent_mode" not in st.session_state:
        st.session_state.agent_mode = "env"  # default

    # --- gestione chat multiple ---
    if "current_chat_id" not in st.session_state:
        # Carica/crea chat all'avvio
        _initialize_chat_store()
    if "renaming_chat_id" not in st.session_state:
        st.session_state.renaming_chat_id = None


# ============================== Gestione Chat (file JSON) ====================

def _initialize_chat_store():
    """
    Carica le chat esistenti dalla cartella. Se non ce ne sono, crea una prima chat vuota.
    Imposta la chat corrente in sessione.
    """
    chat_ids = _list_chat_ids()
    if not chat_ids:
        chat_id = _create_new_chat_file(default_name=True)
        st.session_state.current_chat_id = chat_id
        chat = _load_chat(chat_id)
        st.session_state.messages = chat.get("messages", [])
        st.session_state.current_chat_name = chat.get("name", "Nuova chat")
        st.session_state.current_chat_created_at = chat.get("created_at", _now_iso())
        return

    # Se ci sono chat: apri la più recente (ordine per updated_at)
    metas = [_load_chat_meta(cid) for cid in chat_ids]
    metas_sorted = sorted(
        [m for m in metas if m],
        key=lambda x: x.get("updated_at", x.get("created_at", "")),
        reverse=True
    )
    cur = metas_sorted[0]
    st.session_state.current_chat_id = cur["id"]
    st.session_state.current_chat_name = cur["name"]
    st.session_state.current_chat_created_at = cur.get("created_at", _now_iso())
    st.session_state.messages = _load_chat(cur["id"]).get("messages", [])


def _list_chat_ids() -> List[str]:
    return [
        p.stem for p in CHATS_DIR.glob("*.json")
        if p.is_file()
    ]


def _load_chat_meta(chat_id: str) -> Dict[str, Any] | None:
    fp = _chat_file_path(chat_id)
    if not fp.exists():
        return None
    try:
        with fp.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "id": data.get("id", chat_id),
            "name": data.get("name", "Chat"),
            "created_at": data.get("created_at", _now_iso()),
            "updated_at": data.get("updated_at", data.get("created_at", _now_iso())),
            "messages_count": len(data.get("messages", [])),
        }
    except Exception:
        return None


def _load_chat(chat_id: str) -> Dict[str, Any]:
    fp = _chat_file_path(chat_id)
    if not fp.exists():
        return {"id": chat_id, "name": "Chat", "created_at": _now_iso(), "updated_at": _now_iso(), "messages": []}
    with fp.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_current_chat():
    """
    Salva la chat corrente su file JSON. Viene chiamato automaticamente al termine
    dell'aggiunta di un messaggio utente o assistant.
    """
    chat_id = st.session_state.current_chat_id
    data = {
        "id": chat_id,
        "name": st.session_state.get("current_chat_name", "Chat"),
        "created_at": st.session_state.get("current_chat_created_at", _now_iso()),
        "updated_at": _now_iso(),
        "messages": st.session_state.messages,
    }
    fp = _chat_file_path(chat_id)
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _create_new_chat_file(default_name: bool = False) -> str:
    """
    Crea una nuova chat su file e ne ritorna l'ID.
    """
    chat_id = uuid.uuid4().hex[:12]
    now = _now_iso()
    name = f"Nuova chat ({now.split('T')[0]})" if default_name else "Nuova chat"
    data = {
        "id": chat_id,
        "name": name,
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    with _chat_file_path(chat_id).open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return chat_id


def _switch_chat(chat_id: str):
    """
    Cambia la chat corrente (carica messaggi e metadati in sessione).
    """
    chat = _load_chat(chat_id)
    st.session_state.current_chat_id = chat_id
    st.session_state.current_chat_name = chat.get("name", "Chat")
    st.session_state.current_chat_created_at = chat.get("created_at", _now_iso())
    st.session_state.messages = chat.get("messages", [])


def _rename_chat(chat_id: str, new_name: str):
    """
    Rinomina una chat e salva su file. Se è la chat corrente, aggiorna la sessione.
    """
    chat = _load_chat(chat_id)
    chat["name"] = new_name.strip() or "Chat"
    chat["updated_at"] = _now_iso()
    with _chat_file_path(chat_id).open("w", encoding="utf-8") as f:
        json.dump(chat, f, ensure_ascii=False, indent=2)
    if chat_id == st.session_state.current_chat_id:
        st.session_state.current_chat_name = chat["name"]


def _delete_chat(chat_id: str):
    """
    Elimina il file della chat. Se è la chat corrente, passa a un'altra chat o ne crea una nuova.
    """
    fp = _chat_file_path(chat_id)
    if fp.exists():
        fp.unlink(missing_ok=True)

    # Se ho eliminato la corrente → scegli un'altra
    if chat_id == st.session_state.current_chat_id:
        ids = _list_chat_ids()
        if not ids:
            new_id = _create_new_chat_file(default_name=True)
            _switch_chat(new_id)
        else:
            # Apri la più recente
            metas = [_load_chat_meta(cid) for cid in ids]
            metas_sorted = sorted(
                [m for m in metas if m],
                key=lambda x: x.get("updated_at", x.get("created_at", "")),
                reverse=True
            )
            _switch_chat(metas_sorted[0]["id"])


# ============================== Rendering / UI ===============================

def render_chat_page():
    _ensure_session_defaults()

    # ---- CSS leggero per lista chat (sidebar) ----
    st.markdown(
        """
        <style>
        .chat-list-box {
            max-height: 320px;
            overflow-y: auto;
            padding-right: 6px;
            border: 1px solid rgba(250, 250, 250, 0.08);
            border-radius: 8px;
            background: rgba(250, 250, 250, 0.03);
        }
        .chat-item-name {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .small-muted {
            color: rgba(128,128,128,0.85);
            font-size: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("Chat")
    st.caption("Streaming, blocchi Thinking separati o via <think>…</think>, log strumenti, gestione chat multiple.")

    # ------------------------- Sidebar: selezione agente + opzioni + chats ----
    st.sidebar.markdown("---")
    with st.sidebar.expander("Selezione agente", expanded=True):
        modes = list(AGENT_OPTIONS.keys())
        current_idx = modes.index(st.session_state.agent_mode) if st.session_state.agent_mode in modes else 0

        new_idx = st.selectbox(
            "Modalità agente",
            options=list(range(len(modes))),
            index=current_idx,
            format_func=lambda i: AGENT_OPTIONS[modes[i]]["label"],
            help=AGENT_OPTIONS[modes[current_idx]]["help"],
            key="agent_mode_selectbox",
        )
        st.session_state.agent_mode = modes[new_idx]
        st.caption(f"Modalità attiva: **{AGENT_OPTIONS[st.session_state.agent_mode]['label']}**")

    with st.sidebar.expander("⚙️ Opzioni Chat", expanded=True):
        st.caption("Ollama OpenAI-compat: usa ENV OPENAI_BASE_URL / OPENAI_API_KEY")
        st.write(f"**Modello:** `{MODEL}`")

        st.session_state.show_thinking = st.toggle(
            "Mostra blocchi Thinking",
            value=st.session_state.show_thinking,
            help="Se attivo, mostra un expander '🧠 Thinking' con i ragionamenti.",
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
                _save_current_chat()
                st.rerun()
        with cols[1]:
            st.caption("La history resta invariata quando cambi agente.")

    # ---- Gestione Chats (expander) ----
    with st.sidebar.expander("💬 Chats", expanded=False):
        _render_chats_manager()

    # ---- Pulsante Nuova Chat (in fondo alla sidebar) ----
    st.sidebar.markdown("---")
    if st.sidebar.button("➕ Nuova chat", use_container_width=True):
        new_id = _create_new_chat_file(default_name=True)
        _switch_chat(new_id)
        st.rerun()

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
        _save_current_chat()  # salvataggio automatico dopo messaggio utente

        # Costruisci la history *per l'LLM*, con i tool inseriti nel testo (nascosti alla UI)
        llm_history = build_llm_history(st.session_state.messages, flatten_assistant=False)

        # Esegui core e streamma la risposta (con modalità selezionata)
        result = asyncio.run(_run_agent_and_stream(llm_history, user_text, st.session_state.agent_mode))

        # Persisti risposta assistant con Thinking/Tools salvati
        _append_assistant_message(
            content=result["text"],
            think=result["think"],
            tools=result["tools"],
        )
        _save_current_chat()  # salvataggio automatico dopo messaggio assistant


# ===================== UI Helpers: Chats Manager =============================

def _render_chats_manager():
    """
    Expander in sidebar che mostra lista scrollabile delle chat, con azioni:
    - Apri
    - Rinomina
    - Elimina
    """
    chat_ids = _list_chat_ids()
    metas = [_load_chat_meta(cid) for cid in chat_ids]
    metas = [m for m in metas if m]

    # Ordina per aggiornamento (desc)
    metas_sorted = sorted(metas, key=lambda x: x.get("updated_at", x.get("created_at", "")), reverse=True)

    st.caption(f"Chat salvate: **{len(metas_sorted)}**")
    list_box = st.container()
    with list_box:
        st.markdown('<div class="chat-list-box">', unsafe_allow_html=True)
        for meta in metas_sorted:
            cid = meta["id"]
            is_current = (cid == st.session_state.current_chat_id)
            name = meta["name"]
            updated = meta.get("updated_at") or meta.get("created_at")
            msgs_count = meta.get("messages_count", 0)

            cols = st.columns([0.64, 0.12, 0.12, 0.12])
            with cols[0]:
                lbl = f"▶️ {name}" if is_current else name
                if st.button(lbl, key=f"open_{cid}", help=f"Apri questa chat ({msgs_count} messaggi)", use_container_width=True):
                    _switch_chat(cid)
                    st.rerun()
                st.markdown(f"<div class='small-muted'>Agg.: {updated} • Msg: {msgs_count}</div>", unsafe_allow_html=True)

            with cols[1]:
                if st.button("✏️", key=f"rn_{cid}", help="Rinomina"):
                    st.session_state.renaming_chat_id = cid
                    st.rerun()

            with cols[2]:
                if st.button("🗑️", key=f"del_{cid}", help="Elimina definitivamente"):
                    _delete_chat(cid)
                    st.rerun()

            with cols[3]:
                if st.button("📄", key=f"dup_{cid}", help="Duplica chat"):
                    # Duplica su nuovo file
                    _duplicate_chat(cid)
                    st.rerun()

            # Campo rinomina inline (se in modalità rinomina)
            if st.session_state.renaming_chat_id == cid:
                rcols = st.columns([0.7, 0.15, 0.15])
                with rcols[0]:
                    new_name = st.text_input("Nuovo nome", value=name, key=f"rn_input_{cid}", label_visibility="collapsed")
                with rcols[1]:
                    if st.button("💾", key=f"save_{cid}", help="Salva nome"):
                        _rename_chat(cid, new_name)
                        st.session_state.renaming_chat_id = None
                        st.rerun()
                with rcols[2]:
                    if st.button("✖️", key=f"cancel_{cid}", help="Annulla"):
                        st.session_state.renaming_chat_id = None
                        st.rerun()

            st.markdown("---")

        st.markdown("</div>", unsafe_allow_html=True)


def _duplicate_chat(chat_id: str):
    """
    Duplica una chat su un nuovo file, con suffix timestamp.
    """
    chat = _load_chat(chat_id)
    new_id = uuid.uuid4().hex[:12]
    now = _now_iso()
    chat["id"] = new_id
    chat["name"] = f"{chat.get('name','Chat')} (copia {now.split('T')[0]})"
    chat["created_at"] = now
    chat["updated_at"] = now
    with _chat_file_path(new_id).open("w", encoding="utf-8") as f:
        json.dump(chat, f, ensure_ascii=False, indent=2)


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
    Ritorna (visible_delta, think_delta).
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
      - intercetta i blocchi di reasoning separati (kind="reasoning")
      - fallback: separa <think>...</think> dal content assistant
      - crea expander per ciascun tool (multi-tool con run_id)
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
            kind = ev.get("kind")  # "assistant" | "reasoning" | None (fallback)

            if et == "token":
                chunk = ev.get("text", "")

                # ====== NUOVO: stream reasoning separato quando il core lo fornisce ======
                if kind == "reasoning":
                    # Accumula sempre; mostra solo se il toggle è attivo
                    think_text += chunk
                    if st.session_state.get("show_thinking"):
                        if think_expander is None:
                            think_expander = st.expander("🧠 Thinking", expanded=False)
                            think_ph = think_expander.empty()
                        think_ph.markdown(think_text)
                    continue  # non mischiare nel testo visibile

                # ====== OLD/FALLBACK: parsing dei tag <think> dentro il content assistant ======
                vis, th = _stream_split_think(chunk, parse_state)

                # testo visibile
                if vis:
                    final_text += vis
                    text_ph.markdown(final_text)

                # thinking estratto dai tag <think>...</think>
                if th:
                    think_text += th
                    if st.session_state.get("show_thinking"):
                        if think_expander is None:
                            think_expander = st.expander("🧠 Thinking", expanded=False)
                            think_ph = think_expander.empty()
                        think_ph.markdown(think_text)

                # se siamo *ancora* dentro <think>, aggiorna live l’expander (se visibile)
                if parse_state["in_think"] and st.session_state.get("show_thinking"):
                    if think_expander is None:
                        think_expander = st.expander("🧠 Thinking", expanded=False)
                        think_ph = think_expander.empty()
                    # Forziamo il render dell'attuale contenuto accumulato
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
