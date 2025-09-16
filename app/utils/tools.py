# -*- coding: utf-8 -*-
"""
Helper + Structured Tools (path fisso lato logica)

- Il dataset è un JSON array di misure (schema tipo):
  {
    "light": 82.5,
    "acceleration": 9.68,
    "distance_mm": 123,
    "temperature": 30.9,
    "humidity": 60.2,
    "air_quality_raw": 256,
    "timestamp": "2025-09-15T23:34:10.489804"
  }

- Il path del dataset è fisso e NON è passato dai tool:
  - Env: SENSOR_DATA_PATH
  - Fallback: ./data/sensor_timeseries.json
"""

import os
import json
import datetime
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

# ---------------------------------------------------------------------------
# PATH FISSO DEL DATASET
# ---------------------------------------------------------------------------
SENSOR_DATA_PATH = os.environ.get("SENSOR_DATA_PATH", "C:\\Users\\info\\Desktop\\work_space\\repositories\\jetson-agent\\app\\data\\dati_sensori.json")


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def _load_records_from_store() -> List[Dict[str, Any]]:
    """Carica la lista di record dal path fisso SENSOR_DATA_PATH."""
    if not os.path.exists(SENSOR_DATA_PATH):
        raise FileNotFoundError(
            f"Dataset non trovato: {SENSOR_DATA_PATH}. "
            "Imposta la variabile d'ambiente SENSOR_DATA_PATH o crea il file."
        )
    with open(SENSOR_DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Il dataset deve essere una lista JSON di misure.")
    # Normalizza: tieni solo record con timestamp
    data = [r for r in data if isinstance(r, dict) and "timestamp" in r]
    data.sort(key=lambda r: r["timestamp"])
    return data


def _parse_ts(ts: str) -> datetime.datetime:
    # accetta ISO con/ senza Z
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _filter_fields(rows: List[Dict[str, Any]], fields: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not fields:
        return rows
    keep = set(fields + ["timestamp"])
    return [{k: v for k, v in r.items() if k in keep} for r in rows]


def _trend(vals: List[float], eps: float = 0.1) -> str:
    seq = [v for v in vals if isinstance(v, (int, float))]
    if len(seq) < 2:
        return "→"
    slope = seq[-1] - seq[0]
    if slope > eps:
        return "↗"
    if slope < -eps:
        return "↘"
    return "→"


def _status(value: Optional[float], green: Tuple[float, float],
            yellow: Tuple[Tuple[float, float], Tuple[float, float]] | Tuple[float, float] | None = None,
            hard_limits: Optional[Tuple[float, float]] = None) -> str:
    if value is None:
        return "INDEFINITO"
    gmin, gmax = green
    if gmin <= value <= gmax:
        return "🟢"

    def _in(r):
        return r[0] <= value <= r[1]

    if yellow:
        if isinstance(yellow[0], (int, float)):  # singola tupla
            if _in(yellow):  # type: ignore[arg-type]
                return "🟡"
        else:  # lista di tuple
            for r in yellow:  # type: ignore[assignment]
                if _in(r):
                    return "🟡"

    if hard_limits and not (hard_limits[0] <= value <= hard_limits[1]):
        return "🔴"
    return "🔴"


# ---------------------------------------------------------------------------
# TOOL A) Leggi storico per intervallo temporale
# ---------------------------------------------------------------------------
class ReadByTimeArgs(BaseModel):
    start_ts: Optional[str] = Field(None, description="Timestamp ISO8601 inclusivo (es. 2025-09-15T23:34:10Z)")
    end_ts: Optional[str] = Field(None, description="Timestamp ISO8601 inclusivo (es. 2025-09-15T23:39:59Z)")
    fields: Optional[List[str]] = Field(
        None, description="Sottoinsieme di campi da restituire (oltre a 'timestamp')."
    )

def read_data_by_time(
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    rows = _load_records_from_store()

    if start_ts:
        t0 = _parse_ts(start_ts)
        rows = [r for r in rows if _parse_ts(r["timestamp"]) >= t0]
    if end_ts:
        t1 = _parse_ts(end_ts)
        rows = [r for r in rows if _parse_ts(r["timestamp"]) <= t1]

    out_rows = _filter_fields(rows, fields)
    return {
        "source": SENSOR_DATA_PATH,
        "count": len(out_rows),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "fields": fields or "all",
        "records": out_rows,
    }

read_data_by_time_tool = StructuredTool.from_function(
    func=read_data_by_time,
    name="read_data_by_time",
    description="Leggi lo storico filtrando per intervallo temporale (timestamp ISO8601). Il dataset è letto da un path fisso.",
    args_schema=ReadByTimeArgs,
)


# ---------------------------------------------------------------------------
# TOOL B) Ultime n misure
# ---------------------------------------------------------------------------
class ReadLastNArgs(BaseModel):
    n: int = Field(5, ge=1, le=1000, description="Quante misurazioni più recenti restituire.")
    fields: Optional[List[str]] = Field(
        None, description="Sottoinsieme di campi da restituire (oltre a 'timestamp')."
    )

def read_last_n(n: int = 5, fields: Optional[List[str]] = None) -> Dict[str, Any]:
    rows = _load_records_from_store()
    out_rows = rows[-n:]
    out_rows = _filter_fields(out_rows, fields)
    return {
        "source": SENSOR_DATA_PATH,
        "count": len(out_rows),
        "fields": fields or "all",
        "records": out_rows,
        "window": {
            "from": out_rows[0]["timestamp"] if out_rows else None,
            "to": out_rows[-1]["timestamp"] if out_rows else None,
        },
    }

read_last_n_tool = StructuredTool.from_function(
    func=read_last_n,
    name="read_last_n",
    description="Restituisce le ultime n misurazioni (ordinate per timestamp crescente). Il dataset è letto da un path fisso.",
    args_schema=ReadLastNArgs,
)


# ---------------------------------------------------------------------------
# TOOL C) Snapshot KPI + Trend (opzionale, consigliato)
# ---------------------------------------------------------------------------
class KpiSnapshotArgs(BaseModel):
    window_n: int = Field(5, ge=1, le=100, description="Finestra per trend (ultime n misure).")
    co2_field: Optional[str] = Field(None, description="Nome campo ppm CO2 se disponibile (es. 'co2_ppm').")
    distance_target_mm: int = Field(120, description="Target posizionamento in mm.")
    distance_tol_mm: int = Field(5, description="Tolleranza ±mm sul target.")

def kpi_snapshot(
    window_n: int = 5,
    co2_field: Optional[str] = None,
    distance_target_mm: int = 120,
    distance_tol_mm: int = 5,
) -> Dict[str, Any]:
    rows = _load_records_from_store()
    if not rows:
        return {"error": "Dataset vuoto", "source": SENSOR_DATA_PATH}

    win = rows[-window_n:] if len(rows) >= window_n else rows[:]
    last = win[-1]

    def _series(key: str) -> List[float]:
        return [r.get(key) for r in win if isinstance(r.get(key), (int, float))]

    temp = last.get("temperature")
    hum  = last.get("humidity")
    lux  = last.get("light")
    dist = last.get("distance_mm")
    acc  = last.get("acceleration")

    # euristica: se accelerazione > 3 assumiamo m/s^2 → converti in g
    acc_g = None
    if isinstance(acc, (int, float)):
        acc_g = acc/9.806 if acc > 3 else acc

    # CO2
    co2ppm = last.get(co2_field) if co2_field else None
    air_raw = last.get("air_quality_raw")

    # Status secondo regole nel system message
    st_temp = _status(temp, (24, 30), ((20, 24), (30, 32)), hard_limits=(20, 35)) if isinstance(temp, (int, float)) else "INDEFINITO"
    st_hum  = _status(hum,  (50, 65), ((45, 50), (65, 70)), hard_limits=(30, 80)) if isinstance(hum,  (int, float)) else "INDEFINITO"
    st_lux  = _status(lux,  (80, 100), ((70, 80), (100, 110))) if isinstance(lux,  (int, float)) else "INDEFINITO"

    lo = distance_target_mm - distance_tol_mm
    hi = distance_target_mm + distance_tol_mm
    st_dist = _status(dist, (lo, hi), ((lo-5, lo), (hi, hi+5))) if isinstance(dist, (int, float)) else "INDEFINITO"

    if acc_g is None:
        st_vib = "INDEFINITO"
    else:
        if acc_g < 0.2:   st_vib = "🟡"  # fermo/idle (valutare contesto)
        elif acc_g <= 1:  st_vib = "🟢"
        elif acc_g <= 1.5:st_vib = "🟡"
        else:             st_vib = "🔴"

    if co2ppm is not None:
        st_co2 = "🟢" if co2ppm < 700 else ("🟡" if co2ppm <= 1000 else "🔴")
        co2_value, co2_unit = co2ppm, "ppm"
    else:
        st_co2 = "INDEFINITO"
        co2_value, co2_unit = air_raw, "idx_raw"

    # Trend su finestra
    tr_temp = _trend(_series("temperature"))
    tr_hum  = _trend(_series("humidity"))
    tr_lux  = _trend(_series("light"))
    tr_dist = _trend(_series("distance_mm"))
    tr_acc  = _trend(_series("acceleration"))
    tr_co2  = _trend(_series(co2_field)) if co2_field else "→"

    return {
        "source": SENSOR_DATA_PATH,
        "window": {
            "from": win[0]["timestamp"],
            "to":   win[-1]["timestamp"],
            "used_last_n": len(win),
        },
        "current": {
            "temperature": {"value": temp, "unit": "°C",  "status": st_temp, "trend": tr_temp},
            "humidity":    {"value": hum,  "unit": "%",   "status": st_hum,  "trend": tr_hum},
            "light":       {"value": lux,  "unit": "lux", "status": st_lux,  "trend": tr_lux},
            "distance_mm": {"value": dist, "unit": "mm",  "status": st_dist, "trend": tr_dist},
            "vibration":   {"value": acc_g,"unit": "g",   "status": st_vib,  "trend": tr_acc},
            "co2":         {"value": co2_value, "unit": co2_unit, "status": st_co2, "trend": tr_co2},
            # campi non presenti nel dataset → lasciati a INDEFINITO lato report
            "energy_specific": None,
            "water_specific":  None,
            "co2eq_ratio":     None,
        },
    }

kpi_snapshot_tool = StructuredTool.from_function(
    func=kpi_snapshot,
    name="kpi_snapshot",
    description="Calcola valori correnti e trend dei KPI su finestra (ultime n). Dataset letto da path fisso.",
    args_schema=KpiSnapshotArgs,
)

# ---------------------------------------------------------------------------
# REGISTRAZIONE TOOL (esempio)
# ---------------------------------------------------------------------------
TOOLS = [read_data_by_time_tool, read_last_n_tool, kpi_snapshot_tool]
