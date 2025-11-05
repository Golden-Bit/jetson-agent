import os, json
from openai import OpenAI

BASE = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
KEY  = os.environ.get("OPENAI_API_KEY", "ollama")

client = OpenAI(base_url=BASE, api_key=KEY)
model = "qwen3:8b"

def p(obj): print(json.dumps(obj, indent=2, ensure_ascii=False))

print("=== (1) no-stream, no-tools ===")
r = client.chat.completions.create(
    model=model, stream=False, temperature=0,
    messages=[
        {"role":"system","content":"Rispondi sempre e solo OK."},
        {"role":"user","content":"ciao"},
    ],
)
p(r.model_dump())

print("=== (2) stream, no-tools ===")
with client.chat.completions.create(
    model=model, stream=True, temperature=0,
    messages=[
        {"role":"system","content":"Rispondi sempre e solo OK."},
        {"role":"user","content":"ciao"},
    ],
) as stream:
    for ev in stream:
        print(ev)  # stampa i frame SSE così come arrivano

print("=== (3) stream + tools ===")
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
        {"role":"system","content":"Se puoi, chiama un tool."},
        {"role":"user","content":"Chiama il tool echo con text=\"PY\"."},
    ],
    tools=tools,
    tool_choice="auto",
) as stream:
    for ev in stream:
        print(ev)