# -*- coding: utf-8 -*-
"""
Sezione Editor Target KPI:
- Visualizza e permette di modificare il JSON dei target/soglie
- Pulsante SALVA che valida e scrive su file
"""
import os
import json
import streamlit as st

KPI_TARGETS_PATH = os.environ.get(
    "KPI_TARGETS_PATH",
    "C:\\Users\\info\\Desktop\\work_space\\repositories\\jetson-agent\\app\\data\\kpi_targets.json",
)

def _read_json_text(path: str) -> str:
    if not os.path.exists(path):
        # se assente mostra un template minimo (i tool lo bootstrapperanno comunque)
        return json.dumps({
            "environment": {"trend_epsilon": 0.1, "trend_window_n": 5},
            "social": {"trend_epsilon": 0.1, "trend_window_n": 3}
        }, ensure_ascii=False, indent=2)
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

def render_targets_editor_page():
    st.title("🎯 Editor Target KPI")
    st.caption("Modifica direttamente il file JSON dei target; i tool lo ricaricheranno a runtime.")

    st.markdown(f"**Percorso file:** `{KPI_TARGETS_PATH}`")
    text = st.text_area(
        "Contenuto JSON",
        value=_read_json_text(KPI_TARGETS_PATH),
        height=600,
        help="Modifica liberamente. Il pulsante SALVA valida il JSON prima di scrivere.",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Salva"):
            try:
                _write_json_text(KPI_TARGETS_PATH, text)
                st.success("Target KPI salvati correttamente.")
            except Exception as e:
                st.error(f"Errore di validazione/salvataggio: {e}")
    with c2:
        if st.button("↻ Ricarica dal disco"):
            st.rerun()

    st.markdown("**Estratto esempio struttura:**")
    st.code(
        """{
  "environment": {
    "temperature":   {"green": [24, 30], "yellow": [[20, 24], [30, 32]], "limits": [20, 35], "unit": "°C"},
    "humidity":      {"green": [50, 65], "yellow": [[45, 50], [65, 70]], "limits": [30, 80], "unit": "%"},
    "light":         {"green": [80, 100], "yellow": [[70, 80], [100, 110]], "unit": "lux"},
    "distance_mm":   {"target": 120, "tol": 5, "yellow_extra": 5, "unit": "mm"},
    "vibration_g":   {"green": [0.2, 1.0], "yellow": [[0.0, 0.2], [1.0, 1.5]], "unit": "g"},
    "co2_ppm":       {"green": [0, 700], "yellow": [[700, 1000]], "unit": "ppm"},
    "trend_epsilon": 0.1,
    "trend_window_n": 5
  },
  "social": {
    "turnover_pct":                  {"green": [0, 10], "yellow": [[10, 15]], "direction": "lower"},
    "training_hours_per_employee_y": {"green": [24, 1000], "yellow": [[12, 24]], "direction": "higher"},
    "satisfaction_index":            {"green": [80, 100], "yellow": [[70, 80]], "direction": "higher", "scale": 100},
    "trend_epsilon": 0.1,
    "trend_window_n": 3
  }
}""",
        language="json",
    )
