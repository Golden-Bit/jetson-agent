# -*- coding: utf-8 -*-
"""
Sezione Editor Ambientale:
- Visualizza e permette di modificare il JSON 'dati_sensori.json'
- Pulsante SALVA che valida e scrive su file
"""
import os
import json
import streamlit as st

SENSOR_DATA_PATH = os.environ.get(
    "SENSOR_DATA_PATH",
    "C:\\Users\\info\\Desktop\\work_space\\repositories\\jetson-agent\\app\\data\\dati_sensori.json",
)

def _read_json_text(path: str) -> str:
    if not os.path.exists(path):
        return "[]"
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            f.seek(0)
            return f.read()

def _write_json_text(path: str, text: str) -> None:
    data = json.loads(text)  # valida
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def render_environment_editor_page():
    st.title("🌿 Editor Dati Ambientali")
    st.caption("Modifica direttamente il file JSON delle misure sensori.")

    st.markdown(f"**Percorso file:** `{SENSOR_DATA_PATH}`")
    text = st.text_area(
        "Contenuto JSON",
        value=_read_json_text(SENSOR_DATA_PATH),
        height=500,
        help="Array di record con timestamp e misure. Il pulsante SALVA valida il JSON.",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Salva"):
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
