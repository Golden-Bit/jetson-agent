Perfetto: facciamo una batteria di **test mirati (curl + Python)** per isolare *esattamente* dove si rompe la catena: server OpenAI-compat, streaming SSE, template del modello, oppure wrapper Python.

Di seguito trovi:

1. test via **curl** (4 prove chiave);
2. test via **Python openai** (3 prove);
3. test via **Python httpx** (POST grezzo);
4. **check lato Ollama** (digest/template del modello e log server);
5. come **interpretare** i risultati (cosa conclude ogni prova).

---

# 1) Test con `curl`

> Nota: usa **127.0.0.1** (non `localhost`) per escludere edge-case IPv6/bind.
> Su Jetson esegui le stesse identiche richieste che fai su Windows.

## (A) Base: no-stream, senza tools (il “curl che funziona”)

```bash
curl -sS http://127.0.0.1:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"qwen3:8b",
    "stream": false,
    "messages":[
      {"role":"system","content":"Rispondi sempre e solo OK."},
      {"role":"user","content":"ciao"}
    ],
    "temperature": 0
  }' | jq .
```

**Atteso:** JSON con `choices[0].message.content == "OK"`.

## (B) **Stream**, senza tools (solo SSE)

```bash
curl -N http://127.0.0.1:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"qwen3:8b",
    "stream": true,
    "messages":[
      {"role":"system","content":"Rispondi sempre e solo OK."},
      {"role":"user","content":"ciao"}
    ],
    "temperature": 0
  }'
```

**Atteso:** una sequenza SSE (linee `data: { ... }`) che, sommate, formano “OK”.
Se **qui** il system viene ignorato → problema nello **streaming** anche senza tools.

## (C) No-stream **con tools** (niente SSE)

```bash
curl -sS http://127.0.0.1:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"qwen3:8b",
    "stream": false,
    "messages":[
      {"role":"system","content":"Se ricevi strumenti, usa un tool per rispondere."},
      {"role":"user","content":"Usa il tool echo con text=\"TEST\"."}
    ],
    "tools": [{
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
    }],
    "tool_choice": "auto",
    "temperature": 0
  }' | jq .
```

**Atteso:** `choices[0].message.tool_calls[0].function.name == "echo"` con gli args.
Se **non** compaiono `tool_calls` e/o ignora il system → problema nel **ramo tools** senza streaming.

> Variante “forzata” (alcuni server gestiscono `tool_choice` esplicito):

```bash
...,
"tool_choice": {"type":"function","function":{"name":"echo"}}
```

## (D) **Stream + tools** (il caso sospetto)

```bash
curl -N http://127.0.0.1:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"qwen3:8b",
    "stream": true,
    "messages":[
      {"role":"system","content":"Se hai strumenti, chiamane uno. Non produrre testo: chiama il tool."},
      {"role":"user","content":"Per favore chiama il tool echo con text=\"STREAM\"."}
    ],
    "tools": [{
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
    }],
    "tool_choice": "auto",
    "temperature": 0
  }'
```

**Atteso:** tra le linee SSE devono comparire frammenti con `tool_calls` (di solito in `delta`).
Se qui **scompare** il system/`tool_calls` → il problema è **specifico del path SSE+tools** su Jetson.

---

# 2) Test Python con `openai` (SDK)

Crea `test_openai.py`:

```python
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
```

**Cosa guardare**

* (1) deve stampare `content: "OK"`.
* (2) deve arrivare in più frame ma sempre rispettando il `system`.
* (3) in almeno un frame (tipicamente `choices[0].delta`) deve apparire `tool_calls`.

Se su Jetson **(3)** non mostra `tool_calls` o il `system` è ignorato mentre su Windows sì → conferma che il path **SSE+tools** sul Jetson è il colpevole (anche se versione server e SDK sono “uguali”).

---

# 3) Test Python **grezzo** con `httpx` (senza SDK/LC)

Elimineremo ogni possibile effetto del client Python. Crea `test_httpx.py`:

```python
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
```

**Atteso:** nel secondo POST, il JSON deve contenere `tool_calls`.
Se qui **appare** `tool_calls` ma con l’SDK/LC in streaming no → il problema è proprio **SSE**.

---

# 4) Check lato **Ollama** (modello & log)

1. **Confronta template/digest del modello su entrambe le macchine**

```bash
ollama show qwen3:8b --modelfile | sed -n '/template/,$p' | head -n 80
ollama show qwen3:8b --json | jq '.model | {family,format,parameter_size,quantization_level}?, .template?, .digest?'
```

> Confronta **a vista** il blocco `template` e l’eventuale `digest`. Devono combaciare.

2. **Log server mentre lanci i test SSE+tools**

```bash
journalctl -u ollama -n 200 --no-pager
# oppure in streaming:
journalctl -u ollama -f
```

> Cerca errori/warn relativi a `tools`, `function_call`, `openai-compat`, `SSE`.

---

# 5) Come interpretare i risultati

* **(A) e (B) OK, ma (C) e (D) NO**
  → Il server “vede” il system ma quando aggiungi **tools** (soprattutto in **streaming**) cade in un branch che ignora system/tools.
  **Conferma:** path **tools** non stabile per quel modello/build su Jetson.

* **(A) OK, (B) NO**
  → Il problema è **SSE** anche senza tools (rete/proxy/bind o bug SSE).
  **Workaround:** disabilita `stream=True` in LangChain su Jetson.

* **(A)(B)(C) OK, (D) NO**
  → Colpevole: **SSE+tools**.
  **Workaround:** `streaming=False` quando usi tools **oppure** usa un modello “tool-ready” noto (es. `qwen2.5:7b-instruct`, `llama3.1`).

* **SDK (`openai`) fallisce ma `httpx` grezzo funziona**
  → Incompatibilità nel client streaming parser (rara, ma succede su ARM).
  **Workaround:** usa no-stream o il client grezzo in attesa fix.

* **Solo Jetson fallisce, curl e httpx funzionano, ma LangChain no**
  → Path **SSE client** di LangChain/OpenAI SDK su ARM.
  **Workaround:** `streaming=False` in `ChatOpenAI` *solo* su Jetson, o `tool_choice="none"` e tool-call manuale.

---

## Due safety-switch utili (proprio nella tua codebase)

* Test veloce senza streaming:

```python
_llm = ChatOpenAI(model_name=MODEL, temperature=0.2, streaming=False)
```

* Forza/nega tool-calling per A/B:

```python
_llm = ChatOpenAI(
  model_name=MODEL,
  temperature=0.2,
  streaming=True,
  model_kwargs={"tool_choice": "none"}  # oppure "auto"
)
```

* Modello alternativo solo su Jetson (ARM):

```python
import platform
if platform.machine() in ("aarch64","arm64"):
    MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
```

---

Se esegui la sequenza sopra su **Jetson e Windows** e mi incolli i 4 output chiave:

* (B) SSE senza tools,
* (C) no-stream con tools (JSON intero),
* (D) SSE con tools (prime 10 righe),
* `ollama show ...` (template/digest),

possiamo puntare il dito con certezza assoluta sul branch difettoso e darti il fix definitivo (config o cambio modello/build).
