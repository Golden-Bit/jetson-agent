# 0) Credenziali di esempio (SOLO test)

* **username**: `administrator`
* **password**: `admin`

> Cambiale subito dopo le prove.

---

# 1) Collegamenti e accensione (rapido)

* Alimentazione: **DC jack** o **USB-C PD**; all’arrivo della corrente il dev-kit in genere **si accende da solo**.
* Video: **DisplayPort** (se serve, adattatore DP→HDMI).
* Diagnostica Jetson: `sudo tegrastats` (su L4T non c’è `nvidia-smi`).

---

# 2) Wi-Fi (GUI o `nmcli`)

```bash
nmcli device wifi list
nmcli device wifi connect "SSID" password "PASSWORD"
nmcli connection show
nmcli connection up "SSID"
```

---

# 3) Prerequisiti

```bash
sudo apt update
sudo apt install -y curl vim
```

---

# 4) Installazione **ufficiale** di Ollama (ARM64)

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl start ollama
sudo systemctl status ollama
ollama -v
```

---

# 5) Scarica e prova il nuovo modello

```bash
ollama pull "llama3.1:8b-instruct-q4_K_M"
ollama run  "llama3.1:8b-instruct-q4_K_M" "Scrivi in una frase cos'è il principio di Pareto."
```

---

# 6) Script di **preload** (con fix `keep_alive`)

Crea/aggiorna lo script **con il fix sul JSON** (se `KEEP_ALIVE` è numerico, niente virgolette):

```bash
sudo mkdir -p /usr/local/sbin
sudo vim /usr/local/sbin/ollama-preload.sh
```

**Incolla tutto** e salva con `:wq`:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

# === Config ===
MODEL="${MODEL:-llama3.1:8b-instruct-q4_K_M}"
OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
KEEP_ALIVE="${KEEP_ALIVE:--1}"
WAIT_MAX_SECS="${WAIT_MAX_SECS:-300}"   # attesa max API (5 min)

# Individua il binario
if command -v /usr/local/bin/ollama >/dev/null 2>&1; then
  OLLAMA_BIN="/usr/local/bin/ollama"
elif command -v /usr/bin/ollama >/dev/null 2>&1; then
  OLLAMA_BIN="/usr/bin/ollama"
else
  echo "[preload] ERRORE: 'ollama' non trovato nel PATH" >&2
  exit 1
fi

echo "[preload] Attendo API su http://${OLLAMA_HOST}:${OLLAMA_PORT} (max ${WAIT_MAX_SECS}s)..."
end=$((SECONDS+WAIT_MAX_SECS))
while (( SECONDS < end )); do
  if curl -fsS "http://${OLLAMA_HOST}:${OLLAMA_PORT}/api/tags" >/dev/null; then
    echo "[preload] API raggiungibile."
    break
  fi
  sleep 1
done
if ! curl -fsS "http://${OLLAMA_HOST}:${OLLAMA_PORT}/api/tags" >/dev/null; then
  echo "[preload] Timeout in attesa dell'API di Ollama." >&2
  exit 1
fi

echo "[preload] Verifico presenza modello '${MODEL}' su disco..."
if ! "${OLLAMA_BIN}" show "${MODEL}" >/dev/null 2>&1; then
  echo "[preload] Pull del modello (può richiedere tempo)..."
  "${OLLAMA_BIN}" pull "${MODEL}"
else
  echo "[preload] Modello già presente su disco."
fi

# --- Warm-up con keep_alive corretto (numero vs stringa) ---
if [[ "${KEEP_ALIVE}" =~ ^-?[0-9]+$ ]]; then
  KA_JSON=${KEEP_ALIVE}          # numerico: senza virgolette
else
  KA_JSON="\"${KEEP_ALIVE}\""    # durata (es. 24h): con virgolette
fi
JSON_PAYLOAD=$(printf '{"model":"%s","prompt":"warmup","keep_alive":%s,"stream":false}' \
  "${MODEL}" "${KA_JSON}")

echo "[preload] Warm-up con keep_alive=${KEEP_ALIVE} ..."
curl -fsS "http://${OLLAMA_HOST}:${OLLAMA_PORT}/api/generate" -d "${JSON_PAYLOAD}" >/dev/null

echo "[preload] OK. Modelli in RAM:"
curl -fsS "http://${OLLAMA_HOST}:${OLLAMA_PORT}/api/ps" || true
```

Permessi:

```bash
sudo chmod +x /usr/local/sbin/ollama-preload.sh
sudo chown root:root /usr/local/sbin/ollama-preload.sh
```

---

# 7) **Unit systemd** di preload (corretta)

```bash
sudo vim /etc/systemd/system/ollama-preload.service
```

**Incolla tutto** e salva:

```ini
[Unit]
Description=Precarica Llama 3.1 8B Instruct (Q4_K_M) all'avvio (warm-up e keep_alive)
Requires=ollama.service
Wants=network-online.target
After=network-online.target ollama.service
StartLimitIntervalSec=10min
StartLimitBurst=20

[Service]
Type=oneshot
User=ollama
Group=ollama
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
Environment="MODEL=llama3.1:8b-instruct-q4_K_M"
Environment="KEEP_ALIVE=-1"
Environment="WAIT_MAX_SECS=300"
ExecStart=/usr/local/sbin/ollama-preload.sh
Restart=on-failure
RestartSec=15
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

> **Perché è “corretta”**:
> • niente “-” davanti al nome modello;
> • `StartLimitIntervalSec/StartLimitBurst` stanno in **[Unit]**;
> • variabili `Environment` chiare;
> • `TimeoutStartSec=0` evita timeout su pull lunghi.

---

# 8) Attiva, testa e verifica

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ollama-preload
sudo systemctl status ollama-preload --no-pager
ollama ps
```

**Prova “reale” di riavvio**

```bash
sudo reboot
# poi:
systemctl status ollama --no-pager
systemctl status ollama-preload --no-pager
ollama ps
```

**Test API manuale (verifica che il 400 sia sparito):**

```bash
curl -i http://127.0.0.1:11434/api/generate -d '{
  "model": "llama3.1:8b-instruct-q4_K_M",
  "prompt": "ping",
  "keep_alive": -1,
  "stream": false
}'
```

Atteso: **HTTP/1.1 200** e `ollama ps` mostra il modello in RAM.

---

# 9) Cheat-sheet utile

```bash
# Stato servizi e log
systemctl status ollama --no-pager
systemctl status ollama-preload --no-pager
journalctl -u ollama -n 200 --no-pager
journalctl -u ollama-preload -n 200 --no-pager

# Verifica API e RAM
curl -s http://127.0.0.1:11434/api/ps
ollama ps

# Run rapidi
ollama run "llama3.1:8b-instruct-q4_K_M" "In 3 bullet spiega deterrenza vs coercizione."

# Svuotare RAM
ollama stop "llama3.1:8b-instruct-q4_K_M"
```
