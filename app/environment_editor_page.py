# -*- coding: utf-8 -*-
"""
Sezione Editor Ambientale (cross-platform):
- Visualizza e permette di modificare il JSON 'dati_sensori.json'
- Pulsante SALVA che valida e scrive su file
- Autodiscovery di PROJECT_ROOT e app/data, override via ENV
"""
from __future__ import annotations
import os
import json
from pathlib import Path
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Autodiscovery root e data dir
def _guess_project_root() -> Path:
    """
    Heuristics:
    1) PROJECT_ROOT (ENV)
    2) CWD (utile se il WorkingDirectory del service è la root del repo)
    3) Risalita da __file__ cercando .env o app/data
    4) Antenato chiamato 'jetson-agent'
    5) Fallback: CWD
    """
    pr_env = os.getenv("PROJECT_ROOT")
    if pr_env:
        p = Path(pr_env).expanduser().resolve()
        if p.exists():
            return p

    cwd = Path.cwd().resolve()
    if (cwd / "app" / "data").exists() or (cwd / ".env").exists():
        return cwd

    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "app" / "data").exists() or (p / ".env").exists():
            return p

    for p in here.parents:
        if p.name.lower() == "jetson-agent":
            return p

    return cwd

PROJECT_ROOT: Path = _guess_project_root()
DATA_DIR: Path = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "app" / "data"))

def _resolve_path_from_env(env_key: str, default_filename: str) -> Path:
    val = os.getenv(env_key)
    return Path(val).expanduser().resolve() if val else (DATA_DIR / default_filename)

# Percorso finale (override ENV possibile)
SENSOR_DATA_PATH: Path = _resolve_path_from_env("SENSOR_DATA_PATH", "dati_sensori.json")

# ─────────────────────────────────────────────────────────────────────────────
# I/O helpers
def _read_json_text(path: Path) -> str:
    if not path.exists():
        return "[]"
    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            f.seek(0)
            return f.read()

def _write_json_text(path: Path, text: str) -> None:
    data = json.loads(text)  # valida JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ─────────────────────────────────────────────────────────────────────────────
# Streamlit page (tool)
def render_environment_editor_page():
    st.title("🌿 Editor Dati Ambientali")
    st.caption("Modifica direttamente il file JSON delle misure sensori.")

    st.markdown(f"**Percorso file (rilevato):** `{SENSOR_DATA_PATH}`")
    text = st.text_area(
        "Contenuto JSON",
        value=_read_json_text(SENSOR_DATA_PATH),
        height=500,
        help="Array di record con timestamp e misure. Il pulsante SALVA valida il JSON.",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Salva", type="primary"):
            try:
                _write_json_text(SENSOR_DATA_PATH, text)
                st.success("Dati ambientali salvati correttamente.")
            except Exception as e:
                st.error(f"Errore di validazione/salvataggio: {e}")
    with c2:
        if st.button("↻ Ricarica dal disco"):
            st.rerun()

    st.markdown("**Schema record sensori (esempio):**")
    st.code(
        """{
  "light": 82.5,
  "acceleration": 9.68,
  "distance_mm": 123,
  "temperature": 30.9,
  "humidity": 60.2,
  "air_quality_raw": 256,
  "timestamp": "2025-09-15T22:48:23.896876+02:00"
}""",
        language="json",
    )
