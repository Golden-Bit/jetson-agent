# -*- coding: utf-8 -*-
"""
Core agente con LangChain + Ollama (OpenAI-compat) e streaming eventi.
➤ Selezione modalità: 'env' (Report Ambientale), 'social' (Report Sociale), 'dss' (Analisi DSS/AHP)

- NON passiamo base_url/api_key a ChatOpenAI: letti da ENV
  * OPENAI_BASE_URL (default: http://localhost:11434/v1)
  * OPENAI_API_KEY  (default: "ollama")
- Modello da ENV OLLAMA_MODEL (default: "qwen3:30b")
- Ogni modalità usa:
  • un proprio System Message (AGENT_ENV_SYSTEM_MESSAGE, AGENT_SOC_SYSTEM_MESSAGE, AGENT_DSS_SYSTEM_MESSAGE)
  • il set di tool minimo necessario:
      env  → [env_kpi_snapshot_tool]
      social → [social_kpi_snapshot_tool]
      dss  → [dss_compute_tool]
- Async generator che emette eventi per la UI:
    {"type": "token",      "text": "..."}                      # chunk testo
    {"type": "tool_start", "name": "...", "inputs": {...}, "run_id": "..."}
    {"type": "tool_end",   "name": "...", "inputs": {...}, "output": {...}, "run_id": "..."}
    {"type": "done"}
    {"type": "error", "message": "..."}
"""

import os
import re
from typing import AsyncIterator, Literal
from datetime import datetime
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage

# Import dei TRE system message separati
from .system_message import (
    AGENT_ENV_SYSTEM_MESSAGE,
    AGENT_SOC_SYSTEM_MESSAGE,
    AGENT_DSS_SYSTEM_MESSAGE,
)

# Import dei tool (uno per modalità)
from .tools import (
    generate_dss_report, read_kpi_data, read_social_data,read_env_data, generate_environment_report, generate_social_report, get_kpi_targets
)


# --------------------------------------------------------------------------
# ENV / fallback per Ollama OpenAI-compat (nessuna vera API key richiesta)
# --------------------------------------------------------------------------
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")  # dummy key

MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b") #"llama3.1:8b-instruct-q4_K_M" #"llama3.3:70b-instruct-q4_K_M" #"qwen3:30b"
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))
HIDE_THINK = os.environ.get("HIDE_THINK", "true").lower() in ("1", "true", "yes")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# --------------------------------------------------------------------------
# LLM condiviso
# --------------------------------------------------------------------------

print("BASE_URL=", os.getenv("OPENAI_BASE_URL"))
print("API_KEY=", os.getenv("OPENAI_API_KEY"))
print("MODEL=", os.getenv("OLLAMA_MODEL", MODEL))

_llm = ChatOpenAI(
    model_name=MODEL,     # alias per la tua classe
    temperature=TEMPERATURE,
    streaming=True,       # token streaming
)

# --------------------------------------------------------------------------
# Config per modalità → system_message, tools, run_name
# --------------------------------------------------------------------------
Mode = Literal["env", "social", "dss"]

_MODE_CONFIG = {
    "env": {
        "system_message": AGENT_ENV_SYSTEM_MESSAGE,
        "tools": [read_env_data, generate_environment_report, get_kpi_targets],
        "run_name": "ENV-Agent",
    },
    "social": {
        "system_message": AGENT_SOC_SYSTEM_MESSAGE,
        "tools": [read_social_data, generate_social_report, get_kpi_targets],
        "run_name": "SOC-Agent",
    },
    "dss": {
        "system_message": AGENT_DSS_SYSTEM_MESSAGE,
        "tools": [generate_dss_report, get_kpi_targets],  # da riempire in seguito
        "run_name": "DSS-Agent",
    },
}

# Cache esecutori per modalità
_EXECUTORS: dict[str, AgentExecutor] = {}
_RUN_NAME_BY_MODE: dict[str, str] = {}

def _build_executor(mode: Mode) -> AgentExecutor:
    """Costruisce (e cache) un AgentExecutor per la modalità indicata."""
    if mode in _EXECUTORS:
        return _EXECUTORS[mode]

    cfg = _MODE_CONFIG.get(mode)
    if not cfg:
        raise ValueError(f"Modalità non valida: {mode}. Valori ammessi: env | social | dss")

    system_message = cfg["system_message"]
    tools = cfg["tools"]
    run_name = cfg["run_name"]

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_message),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(_llm, tools, prompt)
    executor: AgentExecutor = AgentExecutor(agent=agent, tools=tools).with_config({"run_name": run_name})

    _EXECUTORS[mode] = executor
    _RUN_NAME_BY_MODE[mode] = run_name
    return executor

def _strip_think(text: str) -> str:
    if HIDE_THINK and text:
        return _THINK_RE.sub("", text)
    return text

def _normalize_tool_inputs(data: dict) -> dict:
    """
    Gli eventi LangChain per i tool espongono in genere 'input' (singolare).
    In alcuni contesti si vede 'inputs'. Normalizziamo a 'inputs'.
    """
    if data is None:
        return {}
    if "inputs" in data and isinstance(data["inputs"], dict):
        return data["inputs"]
    if "input" in data and isinstance(data["input"], dict):
        return data["input"]
    return {}

# --------------------------------------------------------------------------
# EVENT STREAM: genera eventi strutturati per la UI
#   - chat_history: lista di dizionari [{"role":"user"|"assistant","content":"..."}]
#   - mode: 'env' | 'social' | 'dss' → seleziona system message e tools specifici
# --------------------------------------------------------------------------
async def event_stream(user_text: str, chat_history: list[dict], mode: Mode = "env") -> AsyncIterator[dict]:
    """Async generator di eventi per Streamlit, con selettore di modalità (env|social|dss)."""
    # Costruisci executor specifico per modalità
    try:
        executor = _build_executor(mode)
        run_name = _RUN_NAME_BY_MODE.get(mode, "Agent")
    except Exception as e:
        yield {"type": "error", "message": f"Errore inizializzazione agente ({mode}): {e}"}
        return

    # Converti la history "plain" in LangChain messages (solo Human per semplicità).
    lc_history = []
    for m in chat_history:
        if m.get("role") == "user":
            lc_history.append(HumanMessage(content=m.get("content", "")))
        # (Opzionale) Aggiungere AIMessage se serve più contesto

    try:
        async for event in executor.astream_events(
            {"input": user_text, "chat_history": lc_history},
            version="v2",
        ):
            etype = event["event"]
            name  = event.get("name") or event.get("metadata", {}).get("name")
            data  = event.get("data", {}) or {}
            run_id = event.get("run_id")

            # --- TOKEN STREAMING ---
            if etype == "on_llm_new_token":
                text = _strip_think(data.get("chunk", ""))
                if text:
                    yield {"type": "token", "text": text}

            elif etype == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk is not None:
                    text = _strip_think(getattr(chunk, "content", "") or "")
                    if text:
                        yield {"type": "token", "text": text}

            # --- TOOL START / END ---
            elif etype == "on_tool_start":
                tool_name = name or "tool"
                inputs = _normalize_tool_inputs(data)
                yield {
                    "type": "tool_start",
                    "name": tool_name,
                    "inputs": inputs,
                    "run_id": run_id,
                }

            elif etype == "on_tool_end":
                tool_name = name or "tool"
                inputs = _normalize_tool_inputs(data)  # molti backend ribadiscono gli input qui
                output = data.get("output", "")
                yield {
                    "type": "tool_end",
                    "name": tool_name,
                    "inputs": inputs,
                    "output": output,
                    "run_id": run_id,
                }

            # --- CHIUSURA CHAIN ---
            elif etype == "on_chain_end" and (name == run_name or event.get("metadata", {}).get("name") == run_name):
                yield {"type": "done"}

    except Exception as e:
        yield {"type": "error", "message": str(e)}

__all__ = [
    "event_stream",
    "MODEL",
    "HIDE_THINK",
]
