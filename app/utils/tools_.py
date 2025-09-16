# -*- coding: utf-8 -*-
"""
Structured Tools:
- Ambientale: lettura storico (periodo / ultime N), KPI snapshot/trend usando target da JSON.
- Sociale: raccolta e lettura KPI, snapshot/trend/score usando target da JSON.
- I file di target sono esterni e modificabili senza cambiare codice (auto-bootstrap con default).

Percorsi configurabili via ENV (default tra parentesi):
- SENSOR_DATA_PATH  (./data/sensor_timeseries.json)
- SOCIAL_DATA_PATH  (./data/social_kpis.json)
- KPI_TARGETS_PATH  (./data/kpi_targets.json)
"""

import os
import json
import datetime
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field, validator
from langchain_core.tools import StructuredTool

# ─────────────────────────────────────────────────────────────────────────────
# PATH
# ─────────────────────────────────────────────────────────────────────────────
SENSOR_DATA_PATH = os.environ.get("SENSOR_DATA_PATH", "C:\\Users\\info\\Desktop\\work_space\\repositories\\jetson-agent\\app\\data\\dati_sensori.json")
SOCIAL_DATA_PATH = os.environ.get("SOCIAL_DATA_PATH", "C:\\Users\\info\\Desktop\\work_space\\repositories\\jetson-agent\\app\\data\\social_kpis.json")
KPI_TARGETS_PATH = os.environ.get("KPI_TARGETS_PATH", "C:\\Users\\info\\Desktop\\work_space\\repositories\\jetson-agent\\app\\data\\kpi_targets.json")

# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT TARGETS (usati per bootstrap se il file non esiste)
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_TARGETS: Dict[str, Any] = {
    "environment": {
        "temperature":   {"green": [24, 30], "yellow": [[20, 24], [30, 32]], "limits": [20, 35], "unit": "°C"},
        "humidity":      {"green": [50, 65], "yellow": [[45, 50], [65, 70]], "limits": [30, 80], "unit": "%"},
        "light":         {"green": [80, 100], "yellow": [[70, 80], [100, 110]], "unit": "lux"},
        "distance_mm":   {"target": 120, "tol": 5, "yellow_extra": 5, "unit": "mm"},
        "vibration_g":   {"green": [0.2, 1.0], "yellow": [[0.0, 0.2], [1.0, 1.5]], "limits": [0, 99], "unit": "g"},
        "co2_ppm":       {"green": [0, 700], "yellow": [[700, 1000]], "limits": [0, 100000], "unit": "ppm"},
        "energy_specific": {"unit": "kWh/kg"},   # target opzionali → possono essere aggiunti
        "water_specific":  {"unit": "L/kg"},
        "co2eq_ratio":     {"unit": "%"},
        "trend_epsilon": 0.1,
        "trend_window_n": 5
    },
    "social": {
        "turnover_pct":                    {"green": [0, 10], "yellow": [[10, 15]], "direction": "lower"},
        "training_hours_per_employee_y":   {"green": [24, 1000], "yellow": [[12, 24]], "direction": "higher"},
        "satisfaction_index":              {"green": [80, 100], "yellow": [[70, 80]], "direction": "higher", "scale": 100},
        "absenteeism_pct":                 {"green": [0, 3], "yellow": [[3, 5]], "direction": "lower"},
        "gender_female_pct":               {"green": [40, 60], "yellow": [[30, 40], [60, 70]], "direction": "center"},
        "accidents_per_1000h":             {"green": [0, 0.5], "yellow": [[0.5, 1.0]], "direction": "lower"},
        "salary_vs_benchmark_pct":         {"green": [100, 1000], "yellow": [[95, 100]], "direction": "higher"},
        "ethical_suppliers_pct":           {"green": [80, 100], "yellow": [[60, 80]], "direction": "higher"},
        "overtime_hours_per_employee_m":   {"green": [0, 10], "yellow": [[10, 20]], "direction": "lower"},
        "community_projects_count":        {"green": [2, 1000], "yellow": [[1, 1]], "direction": "higher_integer"},
        "trend_epsilon": 0.1,
        "trend_window_n": 3
    }
}

def _ensure_targets_file():
    os.makedirs(os.path.dirname(KPI_TARGETS_PATH) or ".", exist_ok=True)
    if not os.path.exists(KPI_TARGETS_PATH):
        with open(KPI_TARGETS_PATH, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_TARGETS, f, ensure_ascii=False, indent=2)

def _load_targets() -> Dict[str, Any]:
    _ensure_targets_file()
    with open(KPI_TARGETS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS COMUNI
# ─────────────────────────────────────────────────────────────────────────────
def _parse_ts(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))

def _trend(vals: List[float], eps: float) -> str:
    seq = [v for v in vals if isinstance(v, (int, float))]
    if len(seq) < 2:
        return "→"
    slope = seq[-1] - seq[0]
    return "↗" if slope > eps else ("↘" if slope < -eps else "→")

def _score_from_status(status: str) -> int:
    if status == "🟢": return 10
    if status == "🟡": return 7
    if status == "🔴": return 3
    return -1  # INDEFINITO → escluso

def _final_score(statuses: List[str]) -> Optional[float]:
    pts = [p for s in statuses if (p := _score_from_status(s)) >= 0]
    if not pts:
        return None
    return sum(pts) / len(pts) * 10  # media (0–10) → 0–100

def _filter_fields(rows: List[Dict[str, Any]], fields: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not fields:
        return rows
    keep = set(fields + ["timestamp"])
    return [{k: v for k, v in r.items() if k in keep} for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# AMBIENTALE
# ─────────────────────────────────────────────────────────────────────────────
def _load_sensor_rows() -> List[Dict[str, Any]]:
    if not os.path.exists(SENSOR_DATA_PATH):
        raise FileNotFoundError(f"Dataset ambientale non trovato: {SENSOR_DATA_PATH}")
    with open(SENSOR_DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Il dataset ambientale deve essere una lista JSON.")
    data = [r for r in data if isinstance(r, dict) and "timestamp" in r]
    data.sort(key=lambda r: r["timestamp"])
    return data

def _status_env(metric: str, value: Optional[float], tgt: Dict[str, Any]) -> str:
    if value is None:
        return "INDEFINITO"
    # distance: target ± tol con fascia gialla extra
    if metric == "distance_mm" and "target" in tgt:
        center, tol = tgt["target"], tgt.get("tol", 0)
        extra = tgt.get("yellow_extra", 0)
        green = (center - tol, center + tol)
        yellow = [(center - tol - extra, center - tol), (center + tol, center + tol + extra)]
        if green[0] <= value <= green[1]:
            return "🟢"
        for lo, hi in yellow:
            if lo <= value <= hi:
                return "🟡"
        return "🔴"
    # generic with green range and optional yellow ranges
    g = tgt.get("green")
    y = tgt.get("yellow")
    lim = tgt.get("limits")
    if g and g[0] <= value <= g[1]:
        return "🟢"
    if y:
        for r in (y if isinstance(y[0], list) else [y]):
            if r[0] <= value <= r[1]:
                return "🟡"
    if lim and not (lim[0] <= value <= lim[1]):
        return "🔴"
    return "🔴"

# Storico per periodo
class ReadByTimeArgs(BaseModel):
    start_ts: Optional[str] = Field(None, description="Timestamp ISO8601 inclusivo (es. 2025-09-15T23:34:10Z)")
    end_ts: Optional[str] = Field(None, description="Timestamp ISO8601 inclusivo (es. 2025-09-15T23:39:59Z)")
    fields: Optional[List[str]] = Field(None, description="Campi opzionali oltre a 'timestamp'.")

def read_data_by_time(start_ts: Optional[str] = None,
                      end_ts: Optional[str] = None,
                      fields: Optional[List[str]] = None) -> Dict[str, Any]:
    rows = _load_sensor_rows()
    if start_ts:
        t0 = _parse_ts(start_ts); rows = [r for r in rows if _parse_ts(r["timestamp"]) >= t0]
    if end_ts:
        t1 = _parse_ts(end_ts);   rows = [r for r in rows if _parse_ts(r["timestamp"]) <= t1]
    out_rows = _filter_fields(rows, fields)
    return {"source": SENSOR_DATA_PATH, "count": len(out_rows), "start_ts": start_ts, "end_ts": end_ts,
            "fields": fields or "all", "records": out_rows}

read_data_by_time_tool = StructuredTool.from_function(
    func=read_data_by_time,
    name="read_data_by_time",
    description="(Ambientale) Legge lo storico filtrando per intervallo temporale (timestamp ISO8601).",
    args_schema=ReadByTimeArgs,
)

# Ultime N
class ReadLastNArgs(BaseModel):
    n: int = Field(5, ge=1, le=1000, description="Quante misure più recenti restituire.")
    fields: Optional[List[str]] = Field(None, description="Campi opzionali oltre a 'timestamp'.")

def read_last_n(n: int = 5, fields: Optional[List[str]] = None) -> Dict[str, Any]:
    rows = _load_sensor_rows()
    out_rows = _filter_fields(rows[-n:], fields)
    return {"source": SENSOR_DATA_PATH, "count": len(out_rows), "fields": fields or "all",
            "records": out_rows,
            "window": {"from": out_rows[0]["timestamp"] if out_rows else None,
                       "to": out_rows[-1]["timestamp"] if out_rows else None}}

read_last_n_tool = StructuredTool.from_function(
    func=read_last_n,
    name="read_last_n",
    description="(Ambientale) Restituisce le ultime n misurazioni.",
    args_schema=ReadLastNArgs,
)

# KPI snapshot ambientale (usa target da JSON)
class KpiSnapshotArgs(BaseModel):
    window_n: int = Field(None, ge=1, le=200, description="Se None usa trend_window_n dai target.")
    co2_field: Optional[str] = Field(None, description="Nome campo CO2 ppm se disponibile (es. 'co2_ppm').")

def kpi_snapshot(window_n: Optional[int] = None,
                 co2_field: Optional[str] = None) -> Dict[str, Any]:
    targets = _load_targets()["environment"]
    eps = float(targets.get("trend_epsilon", 0.1))
    win_n = int(window_n or targets.get("trend_window_n", 5))

    rows = _load_sensor_rows()
    if not rows:
        return {"error": "Dataset ambientale vuoto", "source": SENSOR_DATA_PATH}

    win = rows[-win_n:] if len(rows) >= win_n else rows[:]
    last = win[-1]

    def _series(key: str) -> List[float]:
        return [r.get(key) for r in win if isinstance(r.get(key), (int, float))]

    temp = last.get("temperature")
    hum  = last.get("humidity")
    lux  = last.get("light")
    dist = last.get("distance_mm")
    acc  = last.get("acceleration")
    acc_g = None
    if isinstance(acc, (int, float)):
        acc_g = acc/9.806 if acc > 3 else acc

    co2ppm = last.get(co2_field) if co2_field else None
    air_raw = last.get("air_quality_raw")

    # Status dinamico da targets
    st_temp = _status_env("temperature", temp, targets["temperature"])
    st_hum  = _status_env("humidity",    hum,  targets["humidity"])
    st_lux  = _status_env("light",       lux,  targets["light"])
    st_dist = _status_env("distance_mm", dist, targets["distance_mm"])
    st_vib  = _status_env("vibration_g", acc_g, targets["vibration_g"]) if acc_g is not None else "INDEFINITO"

    if co2ppm is not None:
        st_co2 = _status_env("co2_ppm", co2ppm, targets["co2_ppm"])
        co2_value, co2_unit = co2ppm, "ppm"
    else:
        st_co2 = "INDEFINITO"
        co2_value, co2_unit = air_raw, "idx_raw"

    statuses = [st_temp, st_hum, st_lux, st_dist, st_vib, st_co2]
    score = _final_score(statuses)  # None ⇒ INDEFINITO
    rating = None if score is None else ("🟢" if score >= 90 else ("🟡" if score >= 70 else "🔴"))

    return {
        "source": SENSOR_DATA_PATH,
        "targets_used": targets,
        "window": {"from": win[0]["timestamp"], "to": win[-1]["timestamp"], "used_last_n": len(win)},
        "current": {
            "temperature": {"value": temp, "unit": targets["temperature"].get("unit","°C"),
                            "status": st_temp, "trend": _trend(_series("temperature"), eps)},
            "humidity":    {"value": hum,  "unit": targets["humidity"].get("unit","%"),
                            "status": st_hum,  "trend": _trend(_series("humidity"), eps)},
            "light":       {"value": lux,  "unit": targets["light"].get("unit","lux"),
                            "status": st_lux,  "trend": _trend(_series("light"), eps)},
            "distance_mm": {"value": dist, "unit": targets["distance_mm"].get("unit","mm"),
                            "status": st_dist, "trend": _trend(_series("distance_mm"), eps)},
            "vibration":   {"value": acc_g,"unit": targets["vibration_g"].get("unit","g"),
                            "status": st_vib,  "trend": _trend(_series("acceleration"), eps)},
            "co2":         {"value": co2_value, "unit": co2_unit,
                            "status": st_co2, "trend": _trend(_series(co2_field), eps) if co2_field else "→"},
            # Altri KPI ambientali opzionali → lasciati a INDEFINITO lato report
            "energy_specific": None,
            "water_specific":  None,
            "co2eq_ratio":     None,
        },
        "score": {"value": score, "rating": rating},
    }

kpi_snapshot_tool = StructuredTool.from_function(
    func=kpi_snapshot,
    name="kpi_snapshot",
    description="(Ambientale) KPI correnti e trend su finestra (targets letti dal JSON esterno).",
    args_schema=KpiSnapshotArgs,
)

# ─────────────────────────────────────────────────────────────────────────────
# SOCIALE
# ─────────────────────────────────────────────────────────────────────────────
def _load_social_rows() -> List[Dict[str, Any]]:
    if not os.path.exists(SOCIAL_DATA_PATH):
        return []
    with open(SOCIAL_DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []

def _save_social_rows(rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(SOCIAL_DATA_PATH) or ".", exist_ok=True)
    with open(SOCIAL_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

# Elenco campi richiesti
class SocialRequirementsArgs(BaseModel):
    pass

def social_requirements() -> Dict[str, Any]:
    return {
        "required_fields": [
            "facility", "period_start", "period_end",
            "turnover_pct", "training_hours_per_employee_y", "satisfaction_index", "satisfaction_scale",
            "absenteeism_pct", "gender_female_pct", "accidents_per_1000h",
            "salary_vs_benchmark_pct", "ethical_suppliers_pct",
            "overtime_hours_per_employee_m", "community_projects_count",
        ],
        "notes": "Fornisci numeri; se mancano resteranno INDEFINITI nel report (esclusi dallo score).",
    }

social_requirements_tool = StructuredTool.from_function(
    func=social_requirements,
    name="social_requirements",
    description="(Sociale) Elenco dei campi da chiedere all'utente prima del report sociale.",
    args_schema=SocialRequirementsArgs,
)

# Upsert KPI sociali
class SocialUpsertArgs(BaseModel):
    facility: str = Field(..., description="Nome stabilimento")
    period_start: str = Field(..., description="Inizio periodo ISO8601 (es. 2025-01-01)")
    period_end: str = Field(..., description="Fine periodo ISO8601 (es. 2025-03-31)")
    turnover_pct: Optional[float] = None
    training_hours_per_employee_y: Optional[float] = None
    satisfaction_index: Optional[float] = None
    satisfaction_scale: Optional[int] = Field(100, description="Scala indice soddisfazione: 100 (default) o 10")
    absenteeism_pct: Optional[float] = None
    gender_female_pct: Optional[float] = None
    accidents_per_1000h: Optional[float] = None
    salary_vs_benchmark_pct: Optional[float] = None
    ethical_suppliers_pct: Optional[float] = None
    overtime_hours_per_employee_m: Optional[float] = None
    community_projects_count: Optional[int] = None

    @validator("satisfaction_scale")
    def _check_scale(cls, v):
        if v not in (10, 100):
            raise ValueError("satisfaction_scale deve essere 10 o 100")
        return v

def upsert_social_kpis(**payload) -> Dict[str, Any]:
    rows = _load_social_rows()
    payload["saved_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    rows = [r for r in rows if not (
        r.get("facility") == payload["facility"]
        and r.get("period_start") == payload["period_start"]
        and r.get("period_end") == payload["period_end"]
    )]
    rows.append(payload)
    _save_social_rows(rows)
    return {"stored": True, "path": SOCIAL_DATA_PATH, "count": len(rows), "last": payload}

upsert_social_kpis_tool = StructuredTool.from_function(
    func=upsert_social_kpis,
    name="upsert_social_kpis",
    description="(Sociale) Registra/aggiorna KPI per periodo e stabilimento.",
    args_schema=SocialUpsertArgs,
)

# Lettura KPI sociali
class ReadSocialArgs(BaseModel):
    facility: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    latest: bool = Field(True, description="Se True e non fornisci periodo, restituisce l'ultimo record per facility.")

def read_social_kpis(facility: Optional[str] = None,
                     period_start: Optional[str] = None,
                     period_end: Optional[str] = None,
                     latest: bool = True) -> Dict[str, Any]:
    rows = _load_social_rows()
    if facility:
        rows = [r for r in rows if r.get("facility") == facility]
    if period_start:
        rows = [r for r in rows if r.get("period_start") == period_start]
    if period_end:
        rows = [r for r in rows if r.get("period_end") == period_end]
    rows.sort(key=lambda r: r.get("saved_at", ""), reverse=True)
    if latest and rows:
        rows = [rows[0]]
    return {"source": SOCIAL_DATA_PATH, "count": len(rows), "records": rows}

read_social_kpis_tool = StructuredTool.from_function(
    func=read_social_kpis,
    name="read_social_kpis",
    description="(Sociale) Legge KPI sociali salvati (per periodo/facility o ultimo disponibile).",
    args_schema=ReadSocialArgs,
)

# Snapshot/score/trend sociale (usa target da JSON)
class SocialSnapshotArgs(BaseModel):
    facility: Optional[str] = None
    window_n: int = Field(None, ge=1, le=48, description="Se None usa trend_window_n dai target.")

def _status_social(metric: str, v: Optional[float], tgt: Dict[str, Any], scale: Optional[int] = None) -> str:
    if v is None:
        return "INDEFINITO"
    # normalizza eventuale scala 0–10 su 0–100 per il confronto con target
    if metric == "satisfaction_index" and scale == 10:
        v = v * 10
    # center (intervallo verde in mezzo)
    if tgt.get("direction") == "center":
        g = tgt.get("green"); y = tgt.get("yellow")
        if g and g[0] <= v <= g[1]: return "🟢"
        for r in (y if isinstance(y[0], list) else [y]):
            if r and r[0] <= v <= r[1]: return "🟡"
        return "🔴"
    # higher_better
    if tgt.get("direction") in ("higher", "higher_integer"):
        g = tgt.get("green"); y = tgt.get("yellow")
        if g and v >= g[0]: return "🟢"
        if y:
            for r in (y if isinstance(y[0], list) else [y]):
                if r and r[0] <= v <= r[1]: return "🟡"
        return "🔴"
    # lower_better
    if tgt.get("direction") == "lower":
        g = tgt.get("green"); y = tgt.get("yellow")
        if g and v <= g[1]: return "🟢"
        if y:
            for r in (y if isinstance(y[0], list) else [y]):
                if r and r[0] <= v <= r[1]: return "🟡"
        return "🔴"
    return "INDEFINITO"

def social_kpi_snapshot(facility: Optional[str] = None, window_n: Optional[int] = None) -> Dict[str, Any]:
    targets_all = _load_targets()["social"]
    eps = float(targets_all.get("trend_epsilon", 0.1))
    win_n = int(window_n or targets_all.get("trend_window_n", 3))

    rows = _load_social_rows()
    if facility:
        rows = [r for r in rows if r.get("facility") == facility]
    rows.sort(key=lambda r: r.get("saved_at", ""), reverse=True)
    if not rows:
        return {"error": "Nessun dato sociale disponibile", "source": SOCIAL_DATA_PATH}

    win = rows[:win_n]
    current = win[0]
    scale = current.get("satisfaction_scale", 100)

    metrics = [
        "turnover_pct",
        "training_hours_per_employee_y",
        "satisfaction_index",
        "absenteeism_pct",
        "gender_female_pct",
        "accidents_per_1000h",
        "salary_vs_benchmark_pct",
        "ethical_suppliers_pct",
        "overtime_hours_per_employee_m",
        "community_projects_count",
    ]

    def series(key: str) -> List[float]:
        out = []
        for r in reversed(win):
            v = r.get(key)
            if isinstance(v, (int, float)):
                out.append(v * 10 if (key == "satisfaction_index" and r.get("satisfaction_scale", 100) == 10) else v)
        return out

    current_out: Dict[str, Any] = {}
    statuses: List[str] = []
    missing: List[str] = []

    for m in metrics:
        val = current.get(m)
        tgt = targets_all.get(m, {})
        status = _status_social(m, val, tgt, scale)
        trend = _trend(series(m), eps)
        if val is None:
            missing.append(m)
        current_out[m] = {"value": val, "status": status, "trend": trend}
        statuses.append(status)

    score = _final_score(statuses)  # None ⇒ INDEFINITO
    rating = None if score is None else ("🟢" if score >= 90 else ("🟡" if score >= 70 else "🔴"))

    return {
        "source": SOCIAL_DATA_PATH,
        "targets_used": targets_all,
        "facility": current.get("facility"),
        "period": {"start": current.get("period_start"), "end": current.get("period_end")},
        "window_used": len(win),
        "current": current_out,
        "score": {"value": score, "rating": rating},
        "missing_fields": missing,
    }

social_kpi_snapshot_tool = StructuredTool.from_function(
    func=social_kpi_snapshot,
    name="social_kpi_snapshot",
    description="(Sociale) Stato/trend KPI sociali con punteggio esclusivo dei soli KPI definiti (targets da JSON).",
    args_schema=SocialSnapshotArgs,
)

# --- subito dopo gli altri import/args ---
class ReadKpiTargetsArgs(BaseModel):
    section: Optional[str] = Field(
        None,
        description="Seleziona una sezione del JSON: 'environment' oppure 'social'. Se None, restituisce tutto."
    )
    metrics: Optional[List[str]] = Field(
        None,
        description="Facoltativo: elenco di metriche da filtrare (es. ['temperature','humidity']). Valido solo se section è impostata."
    )

def read_kpi_targets(section: Optional[str] = None,
                     metrics: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Legge i target KPI dal file JSON esterno (auto-bootstrap con default se mancante).
    Opzioni:
      - section: 'environment' | 'social' | None (tutto)
      - metrics: lista di metriche da filtrare (valida solo se section è specificata)
    """
    try:
        data = _load_targets()  # garantisce bootstrap su file assente
    except json.JSONDecodeError as e:
        return {"error": f"File target non valido: {e}", "source": KPI_TARGETS_PATH}

    out: Dict[str, Any] = {"source": KPI_TARGETS_PATH}

    # Nessun filtro → ritorna tutto
    if section is None:
        out["targets"] = data
        return out

    # Sezione specifica
    if section not in data:
        return {"error": f"Sezione '{section}' non trovata nei target.", "available": list(data.keys()),
                "source": KPI_TARGETS_PATH}

    section_dict = data[section]

    # Se non richiedo filtri metriche → ritorna intera sezione
    if not metrics:
        out["targets"] = {section: section_dict}
        return out

    # Filtra per metriche richieste
    filtered = {m: section_dict[m] for m in metrics if m in section_dict}
    missing = [m for m in metrics if m not in section_dict]

    out["targets"] = {section: filtered}
    if missing:
        out["missing_metrics"] = missing
        out["available_metrics"] = sorted(section_dict.keys())
    return out

read_kpi_targets_tool = StructuredTool.from_function(
    func=read_kpi_targets,
    name="read_kpi_targets",
    description="Legge i target KPI dal JSON esterno (auto-bootstrap). Opzionale filtro per sezione ('environment'|'social') e metriche.",
    args_schema=ReadKpiTargetsArgs,
)

# ─────────────────────────────────────────────────────────────────────────────
# ESPORTA tool list
# ─────────────────────────────────────────────────────────────────────────────
TOOLS = [
    # Ambientale
    read_data_by_time_tool,
    read_last_n_tool,
    kpi_snapshot_tool,
    # Sociale
    social_requirements_tool,
    upsert_social_kpis_tool,
    read_social_kpis_tool,
    social_kpi_snapshot_tool,
    read_kpi_targets_tool
]
