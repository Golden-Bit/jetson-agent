import os, json
from openai import OpenAI

from app.utils.system_message import AGENT_ENV_SYSTEM_MESSAGE

BASE = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
KEY  = os.environ.get("OPENAI_API_KEY", "ollama")

client = OpenAI(base_url=BASE, api_key=KEY)
model = "qwen3:8b"

def p(obj): print(json.dumps(obj, indent=2, ensure_ascii=False))


print("=== stream + tools ===")
tools = [{
  "type": "function",
  "function": {
    "name": "echo",
    "description": "Rimanda indietro il testo",
    "parameters": {
      "type":"object",
      "properties":{"text":{"type":"string"}},
      "required":["text"]
    }
  }
}]
with client.chat.completions.create(
    model=model, stream=True, temperature=0,
    messages=[
        {"role":"system","content":AGENT_ENV_SYSTEM_MESSAGE},
        {"role":"user","content":"Chiama il tool echo con text=\"PY\". inoltre raccontami chi sei e cosa fai"},
    ],
    tools=tools,
    tool_choice="auto",
) as stream:
    for ev in stream:
        print(ev)