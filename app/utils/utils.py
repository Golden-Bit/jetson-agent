# -*- coding: utf-8 -*-
"""
Core agente (senza LangChain / senza tools) con OpenAI SDK (Ollama-compat) e streaming eventi.
➤ Selezione modalità: 'env' (Report Ambientale), 'social' (Report Sociale), 'dss' (Analisi DSS/AHP)

- NON usiamo Agent/Tools; inviamo al modello solo:
  • system message specifico per modalità
  • chat_history (user/assistant così com’è)
  • ultimo user_text

- Streaming eventi verso la UI con schema invariato:
    {"type": "token", "text": "...", "kind": "assistant"}   # chunk testo assistant
    {"type": "token", "text": "...", "kind": "reasoning"}    # chunk reasoning (se disponibile)
    {"type": "done"}
    {"type": "error", "message": "..."}

Env richieste & default:
  OPENAI_BASE_URL  (default: http://127.0.0.1:11434/v1)
  OPENAI_API_KEY   (default: ollama)
  OLLAMA_MODEL     (default: qwen3:8b)
  AGENT_TEMPERATURE (default: 0.2)
  HIDE_THINK       (default: true)  # se true, NON emette reasoning
  OLLAMA_KEEP_ALIVE (default: "0s") # 0s = scarica subito il modello (utile per debug)
  OLLAMA_NUM_CTX   (default: 8192)
  REASONING_EFFORT (default: "")    # es. "medium" (inoltrato come extra_body → "reasoning": {"effort": ...})
"""

from __future__ import annotations
import os
import re
import json
from typing import AsyncIterator, Literal, List, Dict, Any

from openai import OpenAI
from langchain_core.messages import HumanMessage, AIMessage  # solo per compat con tua UI/history

# Import dei TRE system message separati
from .system_message import (
    AGENT_ENV_SYSTEM_MESSAGE,
    AGENT_SOC_SYSTEM_MESSAGE,
    AGENT_DSS_SYSTEM_MESSAGE,
)
from .tools import generate_environment_report_tool, EnvReportArgs, read_env_data_tool, ReadEnvDataArgs, \
    get_kpi_targets_tool, GetTargetsArgs, generate_social_report_tool, SocialReportArgs

# ─────────────────────────────────────────────────────────────────────────────
# ENV / default per Ollama OpenAI-compat
# ─────────────────────────────────────────────────────────────────────────────
BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
API_KEY  = os.environ.get("OPENAI_API_KEY", "ollama")
MODEL    = os.environ.get("OLLAMA_MODEL", "qwen3:8b") #"qwen3:8b")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))
HIDE_THINK  = os.environ.get("HIDE_THINK", "true").lower() in ("1", "true", "yes")

KEEP_ALIVE  = os.environ.get("OLLAMA_KEEP_ALIVE", "0s")   # "0s" per debug; es. "5m" in prod
NUM_CTX     = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "").strip()  # opzionale


# --- Placeholder turn per modalità (SOLO per la chiamata API; NON persistono)  # NEW
PH_USER_TEXT: Dict[str, str] = {  # NEW
    "env":   "Sei un assistente ESG specializzato nella generazione di report ambientali per un azienda nel campo tessile.Genera report ambientale nel prossimo emssaggio. Successivamente ogni qual volta ti venga chiesto di generare il report ambientale (nei emssaggi successivi) allora dovrai mostrare il medesimo report usando anche gli stessi dati ottenuti nel report generato nella tua risposta a tale messaggio. inoltre dovrai mostrare all'utente i dati in formato ben visibile e ad esempio non dovtrai mostrare i json grezzi ma testo ben formattato.",
    "social":"Sei un assistente ESG specializzato nella generazione di report sociali per un azienda nel campo tessile.Genera report sociale nel prossimo emssaggio. Successivamente ogni qual volta ti venga chiesto di generare il report ambientale (nei emssaggi successivi) allora dovrai mostrare il medesimo report usando anche gli stessi dati ottenuti nel report generato nella tua risposta a tale messaggio. inoltre dovrai mostrare all'utente i dati in formato ben visibile e ad esempio non dovtrai mostrare i json grezzi ma testo ben formattato.",
    "dss":   "",
}

PH_ASSISTANT_TEXT: Dict[str, str] = {  # NEW
    #"env":   f"Perfetto, di seguito ti mostro i dati letti e il relativo report \n\n '''DATI: {read_env_data_tool(args=ReadEnvDataArgs())}''', \n\n inoltre ti mostro kpi targets \n\n KPI TARGETS: {get_kpi_targets_tool(args=GetTargetsArgs())}''' \n\n Ed infine lo scheletro del report \n\n'''SCHELETRO REPORT: {generate_environment_report_tool(args=EnvReportArgs())}'''",
    "env":   f"Perfetto, di seguito ti mostro i dati letti e il relativo report. \n\n'''SCHELETRO REPORT: {generate_environment_report_tool(args=EnvReportArgs())}'''",
    "social":f"Perfetto, di seguito ti mostro i dati letti e il relativo report. \n\n'''SCHELETRO REPORT: {generate_social_report_tool(args=SocialReportArgs())}'''",
    "dss":   "",
}


print("BASE_URL=", BASE_URL)
print("API_KEY=", API_KEY)
print("MODEL=", MODEL)
print("KEEP_ALIVE=", KEEP_ALIVE)
print("NUM_CTX=", NUM_CTX)
print("HIDE_THINK=", HIDE_THINK)
print("REASONING_EFFORT=", REASONING_EFFORT or "(none)")

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

# ─────────────────────────────────────────────────────────────────────────────
# Mode config → system_message
# ─────────────────────────────────────────────────────────────────────────────
Mode = Literal["env", "social", "dss"]

_MODE_CONFIG = {
    "env":   {"system_message": AGENT_ENV_SYSTEM_MESSAGE,  "run_name": "ENV-Agent"},
    "social":{"system_message": AGENT_SOC_SYSTEM_MESSAGE,  "run_name": "SOC-Agent"},
    "dss":   {"system_message": AGENT_DSS_SYSTEM_MESSAGE,  "run_name": "DSS-Agent"},
}

# ─────────────────────────────────────────────────────────────────────────────
# Utilità
# ─────────────────────────────────────────────────────────────────────────────
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

def _strip_think(text: str) -> str:
    if text and HIDE_THINK:
        return _THINK_RE.sub("", text)
    return text or ""

def _build_messages(system_message: str, chat_history: List[Dict[str, Any]], user_text: str) -> List[Dict[str, str]]:
    """
    Costruisce l'array messages per /chat/completions in questo ordine:
      - system
      - history (user / assistant così come sono, se presenti)
      - ultimo user_text
    """
    msgs: List[Dict[str, str]] = []
    if system_message:
        msgs.append({"role": "system", "content": system_message})

    # La tua UI passava in precedenza solo messaggi 'user' in lc_history; qui accettiamo anche 'assistant'
    for m in chat_history or []:
        role = m.get("role", "user")
        content = m.get("content", "")
        if not content:
            continue
        # normalizza ruoli non standard
        if isinstance(m, HumanMessage):
            role, content = "user", m.content
        elif isinstance(m, AIMessage):
            role, content = "assistant", m.content
        elif role not in ("user", "assistant", "system"):
            role = "user"
        msgs.append({"role": role, "content": str(content)})

    # ultimo turno utente
    if user_text:
        msgs.append({"role": "user", "content": str(user_text)})
    return msgs

# ─────────────────────────────────────────────────────────────────────────────
# EVENT STREAM (UI contract invariato)
# ─────────────────────────────────────────────────────────────────────────────
async def event_stream(user_text: str, chat_history: list[dict], mode: Mode = "env") -> AsyncIterator[dict]:
    """
    Async generator di eventi per Streamlit (nessun tool). Emissioni:
      {"type":"token","text":"...","kind":"assistant"}
      {"type":"token","text":"...","kind":"reasoning"}   # se disponibile e HIDE_THINK=false
      {"type":"done"}
      {"type":"error","message":"..."}
    """
    cfg = _MODE_CONFIG.get(mode)
    if not cfg:
        yield {"type": "error", "message": f"Modalità non valida: {mode}. Valori ammessi: env | social | dss"}
        return

    system_message = cfg["system_message"]
    run_name = cfg["run_name"]

    # Log di debug della history in ingresso
    for m in chat_history or []:
        print("*" * 120)
        try:
            print(json.dumps(m, ensure_ascii=False))
        except Exception:
            print(str(m))
        print("*" * 120)

    messages = _build_messages(system_message, chat_history, user_text)

    # ⬇️ Inietta SOLO nella chiamata API una coppia user+assistant di placeholder
    placeholder_turn = [
        {"role": "user", "content": PH_USER_TEXT[mode]},
        {"role": "assistant", "content": PH_ASSISTANT_TEXT[mode]},
    ]
    messages_for_call = messages[:-1] + placeholder_turn + messages[-1:]  # <-- NON tocca chat_history né la persistenza

    for m in messages_for_call:
        print("#*"*120)
        print(m)
        print("#*" * 120)

    # extra_body per Ollama-compat (options/keep_alive) + reasoning opzionale
    extra_body = {
        "options": {"num_ctx": NUM_CTX},
        "keep_alive": KEEP_ALIVE,
    }
    if REASONING_EFFORT:
        # Alcuni backend supportano un campo "reasoning": {"effort": "..."} (sarà ignorato se non supportato)
        extra_body["reasoning"] = {"effort": REASONING_EFFORT}

    try:
        # NB: nella SDK moderna è possibile passare extra_body direttamente
        with client.chat.completions.create(
            model=MODEL,
            stream=True,
            temperature=TEMPERATURE,
            messages=messages_for_call,
            extra_body=extra_body,   # inoltra options/keep_alive/reasoning a Ollama
        ) as stream:

            # Accumulatori opzionali (se servono per debug)
            # full_reasoning, full_text = [], []

            for ev in stream:
                # Debug a console dell'evento grezzo
                print("#" * 120)
                print(ev)  # rappresentazione del ChatCompletionChunk
                print("#" * 120)

                if not ev or not getattr(ev, "choices", None):
                    continue

                choice = ev.choices[0]
                delta = getattr(choice, "delta", None)
                finish = getattr(choice, "finish_reason", None)

                if delta:
                    # 1) Reasoning tokens (se esistono e non nascosti)
                    r = getattr(delta, "reasoning", None)
                    if r and not HIDE_THINK:
                        txt = _strip_think(r)
                        if txt:
                            # full_reasoning.append(txt)
                            yield {"type": "token", "text": txt, "kind": "reasoning"}

                    # 2) Assistant content tokens
                    c = getattr(delta, "content", None)
                    if c:
                        txt = _strip_think(c)
                        if txt:
                            # full_text.append(txt)
                            yield {"type": "token", "text": txt, "kind": "assistant"}

                if finish == "stop":
                    # Fine naturale della generazione
                    yield {"type": "done"}

        # In alcuni backend lo stop può non attivarsi: garantisci un done
        # (se già emesso sopra, la UI ignorerà i duplicati)
        yield {"type": "done"}

    except Exception as e:
        # Errori di rete o di backend
        yield {"type": "error", "message": str(e)}

__all__ = [
    "event_stream",
    "MODEL",
    "HIDE_THINK",
]
