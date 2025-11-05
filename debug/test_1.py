import httpx, json

BASE = "http://127.0.0.1:11434/v1"

def post(path, payload):
    r = httpx.post(BASE + path, json=payload, timeout=30)
    print("STATUS:", r.status_code, r.headers.get("content-type"))
    print(r.text[:1000])  # dump parziale

# (A) no-stream, no-tools
post("/chat/completions", {
  "model":"qwen3:8b",
  "stream": False,
  "messages":[
    {"role":"system","content":"Rispondi sempre e solo OK."},
    {"role":"user","content":"ciao"}
  ],
  "temperature": 0,
})

# (B) no-stream, con tools
post("/chat/completions", {
  "model":"qwen3:8b",
  "stream": False,
  "messages":[
    {"role":"system","content":"Se puoi, chiama un tool."},
    {"role":"user","content":"Usa echo con text=\"RAW\"."}
  ],
  "tools": [{
    "type":"function",
    "function":{
      "name":"echo",
      "description":"Ritorna text",
      "parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}
    }
  }],
  "tool_choice": "auto",
  "temperature": 0,
})

# (C) stream, con tools
post("/chat/completions", {
  "model":"qwen3:8b",
  "stream": True,
  "messages":[
    {"role":"system","content":"Se puoi, chiama un tool."},
    {"role":"user","content":"Usa echo con text=\"RAW\"."}
  ],
  "tools": [{
    "type":"function",
    "function":{
      "name":"echo",
      "description":"Ritorna text",
      "parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}
    }
  }],
  "tool_choice": "auto",
  "temperature": 0,
})