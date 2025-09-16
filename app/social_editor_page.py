# -*- coding: utf-8 -*-
"""
Sezione Editor Social:
- Visualizza e permette di modificare il JSON 'social_kpis.json'
- Pulsante SALVA che valida e scrive su file
"""
import os
import json
import streamlit as st

SOCIAL_DATA_PATH = os.environ.get(
    "SOCIAL_DATA_PATH",
    "C:\\Users\\info\\Desktop\\work_space\\repositories\\jetson-agent\\app\\data\\social_kpis.json",
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
    # valida JSON prima di scrivere
    data = json.loads(text)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def render_social_editor_page():
    st.title("👥 Editor Dati Social")
    st.caption("Modifica direttamente il file JSON usato dai tool social.")

    st.markdown(f"**Percorso file:** `{SOCIAL_DATA_PATH}`")
    text = st.text_area(
        "Contenuto JSON",
        value=_read_json_text(SOCIAL_DATA_PATH),
        height=500,
        help="Modifica liberamente. Il pulsante SALVA valida il JSON prima di scrivere.",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Salva"):
            try:
                _write_json_text(SOCIAL_DATA_PATH, text)
                st.success("Dati social salvati correttamente.")
            except Exception as e:
                st.error(f"Errore di validazione/salvataggio: {e}")
    with c2:
        if st.button("↻ Ricarica dal disco"):
            st.rerun()

    st.markdown("**Schema atteso (per record):**")
    st.code(
        """{
  "facility": "Stabilimento_Lino_A",
  "period_start": "YYYY-MM-DD",
  "period_end": "YYYY-MM-DD",
  "turnover_pct": 10.8,
  "training_hours_per_employee_y": 18.0,
  "satisfaction_index": 75.0,
  "satisfaction_scale": 100,
  "absenteeism_pct": 3.1,
  "gender_female_pct": 41.0,
  "accidents_per_1000h": 0.7,
  "salary_vs_benchmark_pct": 99.0,
  "ethical_suppliers_pct": 76.0,
  "overtime_hours_per_employee_m": 16.0,
  "community_projects_count": 1,
  "saved_at": "2025-01-10T09:00:00Z"
}""",
        language="json",
    )
