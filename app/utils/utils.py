# -*- coding: utf-8 -*-
"""
Core agente con LangChain + Ollama (OpenAI-compat) e streaming eventi.

- NON passiamo base_url/api_key a ChatOpenAI: vengono letti da ENV.
  * OPENAI_BASE_URL (default: http://localhost:11434/v1)
  * OPENAI_API_KEY  (default: "ollama")
- Modello da ENV OLLAMA_MODEL (default: "qwen3:8b")
- Tool "get_weather" come StructuredTool (Pydantic)
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
from langchain_core.tools import StructuredTool
from langchain_core.messages import HumanMessage

from .system_message import AGENT_SYSTEM_MESSAGE
from .tools import TOOLS

# --------------------------------------------------------------------------
# ENV / fallback per Ollama OpenAI-compat (nessuna vera API key richiesta)
# --------------------------------------------------------------------------
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")  # dummy key

MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))
HIDE_THINK = os.environ.get("HIDE_THINK", "true").lower() in ("1", "true", "yes")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


# --------------------------------------------------------------------------
# COSTRUZIONE AGENTE
# --------------------------------------------------------------------------
_llm = ChatOpenAI(
    model_name=MODEL,            # alias di model_name nella tua classe
    temperature=TEMPERATURE,
    streaming=True,              # token streaming
)

_system_message = AGENT_SYSTEM_MESSAGE

_prompt = ChatPromptTemplate.from_messages([
    ("system",_system_message),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

_tools = TOOLS
_agent = create_tool_calling_agent(_llm, _tools, _prompt)
_executor: AgentExecutor = AgentExecutor(agent=_agent, tools=_tools).with_config({"run_name": "Agent"})


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
# --------------------------------------------------------------------------
async def event_stream(user_text: str, chat_history: list[dict]) -> AsyncIterator[dict]:
    """Async generator di eventi per Streamlit."""
    # Converti la history "plain" in LangChain messages (solo Human per semplicità).
    lc_history = []
    for m in chat_history:
        if m.get("role") == "user":
            lc_history.append(HumanMessage(content=m.get("content", "")))
        # Potresti aggiungere anche AIMessage per contesti più ricchi.

    try:
        async for event in _executor.astream_events(
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
            elif etype == "on_chain_end" and (name == "Agent" or event.get("metadata", {}).get("name") == "Agent"):
                yield {"type": "done"}

    except Exception as e:
        yield {"type": "error", "message": str(e)}


__all__ = [
    "event_stream",
    "MODEL",
    "HIDE_THINK",
]
