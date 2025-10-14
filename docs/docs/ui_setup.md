# 1) Prerequisiti (una volta sola)

```bash
sudo apt update
sudo apt install -y git python3.10 python3.10-venv
```

> Se usi firewall UFW e vuoi accedere da LAN:
> `sudo ufw allow 8600/tcp`

---

# 2) Script di avvio (clona/venv/install/avvia Streamlit)

Crea lo **script** che farà tutto in automatico.

```bash
sudo mkdir -p /usr/local/sbin
sudo vim /usr/local/sbin/jetson-agent-ui.sh
```

Incolla **tutto** il contenuto qui sotto e salva con `:wq`:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

# === Configurazione ===
USER_HOME="/home/administrator"
REPO_URL="https://github.com/Golden-Bit/jetson-agent.git"
REPO_DIR="${USER_HOME}/jetson-agent"
VENV_DIR="${REPO_DIR}/env"
PORT="${PORT:-8600}"                      # porta fissa richiesta
PYBIN="$(command -v python3.10 || true)"  # usa py3.10 come richiesto

if [[ -z "${PYBIN}" ]]; then
  echo "[ui] ERRORE: python3.10 non trovato. Installa: sudo apt install -y python3.10 python3.10-venv" >&2
  exit 1
fi

# === Clona repo se mancante ===
if [[ ! -d "${REPO_DIR}" ]]; then
  echo "[ui] Clono repo in ${REPO_DIR} ..."
  git clone "${REPO_URL}" "${REPO_DIR}"
fi

cd "${REPO_DIR}"

# (opzionale) allinea repo senza distruggere modifiche locali
if command -v git >/dev/null 2>&1; then
  echo "[ui] Aggiorno repo (git fetch --all) ..."
  git fetch --all || true
  # non faccio pull automatico per non sovrascrivere modifiche locali
fi

# === Crea venv se mancante ===
if [[ ! -d "${VENV_DIR}" ]]; then
  echo "[ui] Creo venv con python3.10 ..."
  "${PYBIN}" -m venv "${VENV_DIR}"
fi

# === Pip install ===
echo "[ui] Aggiorno pip/setuptools/wheel ..."
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel

if [[ -f requirements.txt ]]; then
  echo "[ui] Installo requirements ..."
  "${VENV_DIR}/bin/pip" install -r requirements.txt
else
  echo "[ui] ATTENZIONE: requirements.txt non trovato in ${REPO_DIR}" >&2
fi

# === Avvio Streamlit sulla porta fissa 8600 ===
echo "[ui] Avvio Streamlit su 0.0.0.0:${PORT} ..."
exec "${VENV_DIR}/bin/python" -m streamlit run app/app_main.py \
  --server.port "${PORT}" \
  --server.address 0.0.0.0 \
  --server.headless true
```

Permessi esecuzione:

```bash
sudo chmod +x /usr/local/sbin/jetson-agent-ui.sh
sudo chown root:root /usr/local/sbin/jetson-agent-ui.sh
```

---

# 3) Service systemd per auto-avvio all’accensione

Crea il **servizio** che richiama lo script sopra (gira come utente `administrator` e usa la sua home).

```bash
sudo vim /etc/systemd/system/jetson-agent-ui.service
```

Incolla **tutto** e salva:

```ini
[Unit]
Description=Jetson Agent UI (Streamlit su porta 8600)
Wants=network-online.target
After=network-online.target ollama.service

[Service]
Type=simple
User=administrator
Group=administrator
WorkingDirectory=/home/administrator/jetson-agent
# PATH pulito; lo script richiama esplicitamente la venv
Environment="PORT=8600"
Environment="HOME=/home/administrator"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/local/sbin/jetson-agent-ui.sh
Restart=always
RestartSec=5
# Evita kill brutali per permettere shutdown pulito
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

**Abilita e avvia subito:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jetson-agent-ui
systemctl status jetson-agent-ui --no-pager
```

---

# 4) Verifica rapida

* **Log live**:

  ```bash
  journalctl -u jetson-agent-ui -f -n 100
  ```
* **Porta in ascolto**:

  ```bash
  ss -lntp | grep :8600
  ```
* **Apri UI** (sul Jetson o da un altro PC della LAN):
  `http://<IP_DEL_JETSON>:8600`

Se usi firewall UFW: `sudo ufw allow 8600/tcp`.

---

# 5) Uso manuale (facoltativo)

Se vuoi lanciare a mano senza systemd:

```bash
cd /home/administrator
[ -d jetson-agent ] || git clone https://github.com/Golden-Bit/jetson-agent.git
cd jetson-agent
python3.10 -m venv env
./env/bin/python -m pip install --upgrade pip setuptools wheel
./env/bin/pip install -r requirements.txt
./env/bin/python -m streamlit run app/app_main.py --server.port 8600 --server.address 0.0.0.0 --server.headless true
```

---

# 6) Operazioni comuni

* **Aggiornare il codice UI**:

  ```bash
  sudo systemctl stop jetson-agent-ui
  cd /home/administrator/jetson-agent
  git pull
  sudo systemctl start jetson-agent-ui
  ```

* **Re-installare requirements** (se cambiano):

  ```bash
  sudo systemctl stop jetson-agent-ui
  cd /home/administrator/jetson-agent
  ./env/bin/pip install -r requirements.txt
  sudo systemctl start jetson-agent-ui
  ```

* **Riconfigurare porta** (es. 8610):
  modifica in `/etc/systemd/system/jetson-agent-ui.service` la riga `Environment="PORT=8610"`, poi:

  ```bash
  sudo systemctl daemon-reload
  sudo systemctl restart jetson-agent-ui
  ```

---

# 7) Troubleshooting veloce

* **Service “active (running)” ma pagina non raggiungibile**

  * Controlla IP/porta: `ss -lntp | grep :8600`
  * Firewall: `sudo ufw status` (apri 8600/tcp)
  * Log: `journalctl -u jetson-agent-ui -n 200 --no-pager`

* **Manca Python 3.10 o venv**
  `sudo apt install -y python3.10 python3.10-venv`

* **Errori pip**
  `./env/bin/python -m pip install --upgrade pip setuptools wheel` poi `./env/bin/pip install -r requirements.txt`

* **Permessi nel repo**
  Il service gira come **administrator**: assicurati che `/home/administrator/jetson-agent` sia leggibile/scrivibile da quell’utente.
