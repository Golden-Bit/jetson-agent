# -*- coding: utf-8 -*-
"""
Structured Tools per agente ESG (monitoraggio e reportistica).

Contenuto:
- Lettura dataset ambientale/sociale con filtro per indici o date.
- Generazione report Ambientale (markdown o JSON).
- Generazione report Sociale (markdown o JSON).
- Lettura targets da file esterno (bootstrap automatico se mancante).

ENV richieste & default:
- SENSOR_DATA_PATH  (default: ./data/dati_sensori.json)
- SOCIAL_DATA_PATH  (default: ./data/social_kpis.json)
- KPI_TARGETS_PATH  (default: ./data/kpi_targets.json)

NOTE OPERATIVE:
- Gli indici si riferiscono SEMPRE alla vista **decrescente** (più recente = indice 0).
- Se l’intervallo richiesto eccede i dati disponibili, si ritorna il massimo ottenibile (nessun errore).
- Le valutazioni (🟢/🟡/🔴) usano i targets del file; in assenza di target/campo → status "⚪" (N/D).
"""

from __future__ import annotations
import os
import json
import math
from pathlib import Path
import statistics
from typing import List, Dict, Any, Optional, Tuple, Literal
from datetime import datetime, date
from pydantic import BaseModel, Field, validator
from langchain_core.tools import StructuredTool

from .dss_utils import _status_to_norm01, FIN_KPI_ORDER, _ahp_weights_and_cr, ENV_KPI_FOR_DSS, \
    _pairwise_equal_matrix, _has_thresholds

# ─────────────────────────────────────────────────────────────────────────────
# PATH (puoi sovrascrivere via ENV)
# ─────────────────────────────────────────────────────────────────────────────
def _guess_project_root() -> Path:
    """
    Heuristics:
    1) PROJECT_ROOT se definita in ENV
    2) directory corrente (WorkingDirectory del servizio systemd)
    3) cammina su per le cartelle da __file__ cercando .env o 'app/data'
    4) fallback: prima cartella antenata chiamata 'jetson-agent', se esiste
    """
    # 1) ENV esplicita
    pr_env = os.getenv("PROJECT_ROOT")
    if pr_env:
        p = Path(pr_env).expanduser().resolve()
        if p.exists():
            return p

    # 2) CWD (utile con systemd: WorkingDirectory=/home/administrator/jetson-agent)
    cwd = Path.cwd().resolve()
    if (cwd / "app" / "data").exists() or (cwd / ".env").exists():
        return cwd

    # 3) Cammina dai file sorgente
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "app" / "data").exists() or (p / ".env").exists():
            return p

    # 4) Repo name
    for p in here.parents:
        if p.name.lower() == "jetson-agent":
            return p

    return cwd  # extrema ratio

PROJECT_ROOT = _guess_project_root()
DATA_DIR = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "app" / "data"))

SENSOR_DATA_PATH = Path(os.getenv("SENSOR_DATA_PATH", DATA_DIR / "dati_sensori.json"))
SOCIAL_DATA_PATH = Path(os.getenv("SOCIAL_DATA_PATH", DATA_DIR / "social_kpis.json"))
KPI_TARGETS_PATH = Path(os.getenv("KPI_TARGETS_PATH", DATA_DIR / "kpi_targets.json"))

# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT TARGETS (bootstrap se file mancante)
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_TARGETS: Dict[str, Any] = {
    "environment": {
        "temperature":   {"green": [24, 30], "yellow": [[20, 24], [30, 32]], "limits": [20, 35], "unit": "°C"},
        "humidity":      {"green": [50, 65], "yellow": [[45, 50], [65, 70]], "limits": [30, 80], "unit": "%"},
        "light":         {"green": [80, 100], "yellow": [[70, 80], [100, 110]], "unit": "lux"},
        "distance_mm":   {"target": 120, "tol": 5, "yellow_extra": 5, "unit": "mm"},
        "vibration_g":   {"green": [0.2, 1.0], "yellow": [[0.0, 0.2], [1.0, 1.5]], "limits": [0, 99], "unit": "g"},
        "co2_ppm":       {"green": [0, 700], "yellow": [[700, 1000]], "limits": [0, 100000], "unit": "ppm"},
        # opzionali (per riepilogo footprint, se disponibili a valle):
        "energy_specific": {"unit": "kWh/kg"},
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

# ─────────────────────────────────────────────────────────────────────────────
# Wrapper per adattare funzioni "fn(args: Model)" a StructuredTool con kwargs
# ─────────────────────────────────────────────────────────────────────────────
def _wrap_args(model_cls, impl_fn):
    """
    Converte kwargs (o un dict sotto la chiave 'args') nel Pydantic model richiesto
    dalla funzione implementativa, poi invoca impl_fn(args_model).
    """
    def _inner(**kwargs):
        payload = kwargs.get("args", kwargs)  # supporta sia {...} che {"args": {...}}
        args_obj = model_cls(**(payload or {}))
        return impl_fn(args_obj)
    return _inner


# ─────────────────────────────────────────────────────────────────────────────
# Utility: file targets
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_targets_file():
    KPI_TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not KPI_TARGETS_PATH.exists():
        KPI_TARGETS_PATH.write_text(json.dumps(_DEFAULT_TARGETS, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_targets() -> Dict[str, Any]:
    _ensure_targets_file()
    with open(KPI_TARGETS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# ─────────────────────────────────────────────────────────────────────────────
# Utility: caricamento dataset
# ─────────────────────────────────────────────────────────────────────────────
def _load_env_rows() -> List[Dict[str, Any]]:
    if not SENSOR_DATA_PATH.exists():
        return []
    with SENSOR_DATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    data = [r for r in data if isinstance(r, dict) and "timestamp" in r]
    data.sort(key=lambda r: r["timestamp"])
    return data


def _load_social_rows() -> List[Dict[str, Any]]:
    if not SOCIAL_DATA_PATH.exists():
        return []
    with SOCIAL_DATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []

# ─────────────────────────────────────────────────────────────────────────────
# Utility: date/filtri/formatting
# ─────────────────────────────────────────────────────────────────────────────
def _parse_dt(s: str) -> datetime:
    # accetta ISO pieno, 'YYYY-MM-DD' o 'YYYY-MM'
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        pass
    return datetime.strptime(s, "%Y-%m")

def _fmt_num(v: Any, decimals: int = 1) -> str:
    if v is None:
        return "N/D"
    if isinstance(v, (int,)) or (isinstance(v, float) and float(v).is_integer()):
        return f"{int(v)}"
    try:
        return f"{float(v):.{decimals}f}"
    except Exception:
        return str(v)

def _range_contains(value: float, rng: List[float]) -> bool:
    return value >= float(rng[0]) and value <= float(rng[1])

def _status_from_targets(value: Optional[float], tdef: Dict[str, Any]) -> str:
    """
    Ritorna 'green' | 'yellow' | 'red' | 'na'
    Regole:
    - Se esiste schema 'target±tol' → costruisce green/yellow.
    - Se esistono 'green' (range) e 'yellow' (lista di range) → calcola in-range.
    - Se value è None → 'na'
    """
    if value is None:
        return "na"

    # target ± tol (caso distance_mm)
    if "target" in tdef and "tol" in tdef:
        tgt, tol = float(tdef["target"]), float(tdef["tol"])
        yextra = float(tdef.get("yellow_extra", 0))
        green = [tgt - tol, tgt + tol]
        yellow = [[tgt - tol - yextra, tgt - tol], [tgt + tol, tgt + tol + yextra]]
        if _range_contains(value, green):
            return "green"
        if any(_range_contains(value, r) for r in yellow):
            return "yellow"
        return "red"

    # range standard
    green = tdef.get("green")
    yellow = tdef.get("yellow", [])
    if green and _range_contains(value, green):
        return "green"
    if any(_range_contains(value, r) for r in yellow):
        return "yellow"
    return "red"

def _status_emoji(s: str) -> str:
    return {"green": "🟢", "yellow": "🟡", "red": "🔴", "na": "⚪"}.get(s, "⚪")

def _trend_arrow(delta: Optional[float], eps: float = 0.1) -> str:
    if delta is None:
        return "—"
    if delta > eps:
        return "↗"
    if delta < -eps:
        return "↘"
    return "→"

def _score_from_status(s: str) -> int:
    return {"green": 100, "yellow": 80, "red": 50}.get(s, 0)

def _mk_target_str_env(k: str, t: Dict[str, Any]) -> str:
    unit = t.get("unit", "")
    if "target" in t and "tol" in t:
        return f"{_fmt_num(t['target'], 0)}±{_fmt_num(t['tol'], 0)} {unit}".strip()
    if "green" in t and isinstance(t["green"], list) and len(t["green"]) == 2:
        return f"{_fmt_num(t['green'][0], 0)}–{_fmt_num(t['green'][1], 0)}{unit}".strip()
    if unit:
        return f"[{unit}]"
    return "—"

def _mk_target_str_soc(k: str, t: Dict[str, Any]) -> str:
    """
    Rende il target sociale in forma leggibile, usando i range 'green' e la 'direction':
    - lower  → ≤ upper_green
    - higher → ≥ lower_green
    - center → lower_green–upper_green
    - higher_integer → ≥ lower_green (intero) [+ unità]
    Caso speciale: satisfaction_index (targets su scala 0–100) viene mostrato su /10.
    """
    if not t:
        return "—"

    # unità per visualizzazione (senza placeholder)
    unit_map = {
        "turnover_pct": "%",
        "training_hours_per_employee_y": "h/anno",
        "satisfaction_index": "/10",
        "absenteeism_pct": "%",
        "gender_female_pct": "%",
        "accidents_per_1000h": "",
        "salary_vs_benchmark_pct": "%",
        "ethical_suppliers_pct": "%",
        "overtime_hours_per_employee_m": "h/mese",
        "community_projects_count": "progetti",
    }
    unit = unit_map.get(k, "")

    green = t.get("green")
    direction = t.get("direction", "")

    # Caso speciale: satisfaction_index (green su scala 0–100, display su /10)
    if k == "satisfaction_index":
        if isinstance(green, list) and len(green) == 2:
            low = float(green[0])  # es. 80 → 8.0/10
            try:
                thr10 = low / 10.0
                return f"≥ {_fmt_num(thr10, 1)}/10"
            except Exception:
                return "—"
        return "—"

    # Altri KPI sociali
    if isinstance(green, list) and len(green) == 2:
        lo, hi = float(green[0]), float(green[1])
        if direction == "lower":
            # es. absenteeism_pct green [0,3] → ≤ 3%
            return f"≤ {_fmt_num(hi, 0)}{(' ' + unit) if unit and unit not in ['%', '/10'] else unit}"
        elif direction == "higher":
            # es. ethical_suppliers_pct green [80,100] → ≥ 80%
            return f"≥ {_fmt_num(lo, 0)}{(' ' + unit) if unit and unit not in ['%', '/10'] else unit}"
        elif direction == "center":
            # es. gender_female_pct green [40,60] → 40–60%
            sep = "–"
            return f"{_fmt_num(lo, 0)}{unit if unit=='%' else (' ' + unit if unit else '')}{sep}{_fmt_num(hi, 0)}{unit if unit=='%' else (' ' + unit if unit else '')}"
        elif direction == "higher_integer":
            # es. community_projects_count green [2,1000] → ≥ 2 progetti
            return f"≥ {int(math.ceil(lo))}{(' ' + unit) if unit else ''}"

        # fallback: range pieno
        sep = "–"
        return f"{_fmt_num(lo, 0)}{unit if unit=='%' else (' ' + unit if unit else '')}{sep}{_fmt_num(hi, 0)}{unit if unit=='%' else (' ' + unit if unit else '')}"

    # Nessuna info utile → unità o trattino
    return unit or "—"

# ─────────────────────────────────────────────────────────────────────────────
# TOOL 1 — Lettura dati ENV/SOCIAL
# ─────────────────────────────────────────────────────────────────────────────

class ReadKpiDataArgs(BaseModel):
    """Input per lettura dati ENV/SOC con filtro per indici o date."""
    kind: Literal["env", "social"] = Field(
        default="env",
        description="Sorgente dati.",
        json_schema_extra={"example": "env"},
    )
    by: Literal["index", "date"] = Field(
        default="index",
        description="Modalità di selezione.",
        json_schema_extra={"example": "index"},
    )
    idx_start: Optional[int] = Field(
        default=0,
        ge=0,
        description="Indice di inizio (0 = più recente, solo by='index').",
        json_schema_extra={"example": 0},
    )
    idx_end: Optional[int] = Field(
        default=0,
        ge=0,
        description="Indice di fine incluso (solo by='index').",
        json_schema_extra={"example": 49},
    )
    date_start: Optional[str] = Field(
        default=None,
        description="Data/DateTime inizio (solo by='date', ISO o YYYY-MM[-DD]).",
        json_schema_extra={"example": "2025-04-01"},
    )
    date_end: Optional[str] = Field(
        default=None,
        description="Data/DateTime fine inclusiva (solo by='date').",
        json_schema_extra={"example": "2025-06-30"},
    )
    facility: Optional[str] = Field(
        default="",
        description="Filtro stabilimento (solo SOCIAL).",
        json_schema_extra={"example": "Stabilimento_Lino_B"},
    )
    #fields: Optional[List[str]] = Field(
    #    default_factory=list,
    #    description="Proiezione campi (vuoto = tutti).",
    #    json_schema_extra={"example": ["timestamp", "temperature", "humidity"]},
    #)
    #order: Literal["desc", "asc"] = Field(
    #    default="desc",
    #    description="Ordinamento del risultato.",
    #    json_schema_extra={"example": "desc"},
    #)

    class Config:
        schema_extra = {
            "examples": [
                {"kind":"env","by":"index","idx_start":0,"idx_end":100},
                {"kind":"social","by":"date","date_start":"2025-01-01","date_end":"2025-03-31","facility":"Stabilimento_Lino_B"},
            ]
        }


def read_kpi_data_tool(args: ReadKpiDataArgs) -> Dict[str, Any]:
    """
    Descrizione:
    Legge il dataset ambientale o sociale applicando un filtro per **indici** (0 = più recente)
    oppure per **date**. Non genera errore se l'intervallo eccede i dati disponibili.

    Output (dict):
    {
      "kind": "env" | "social",
      "count": <int>,
      "items": [ { ... }, ... ]   # ordinati secondo 'order'
    }
    """
    kind = args.kind
    fields = args.fields or []
    order = args.order or "desc"

    if kind == "env":
        base = _load_env_rows()
        # convertiamo 'acceleration' (m/s^2) in 'vibration_g' se presente
        for r in base:
            if "acceleration" in r and "vibration_g" not in r:
                try:
                    r["vibration_g"] = float(r["acceleration"]) / 9.81
                except Exception:
                    pass
        key_dt = "timestamp"
        # dataset ascendente per timestamp → per indici invertiamo
    else:
        base = _load_social_rows()
        key_dt = "saved_at" if any("saved_at" in r for r in base) else "period_end"
        # filtro facility se richiesto
        if args.facility:
            base = [r for r in base if r.get("facility") == args.facility]

    if not base:
        items: List[Dict[str, Any]] = []
    else:
        # vista discendente (più recente per primo)
        desc = list(reversed(base))

        if args.by == "index":
            i0 = args.idx_start or 0
            i1 = args.idx_end if args.idx_end is not None else i0
            i0, i1 = max(0, i0), max(0, i1)
            i0, i1 = min(i0, len(desc)-1), min(i1, len(desc)-1)
            if i0 <= i1:
                subset = desc[i0:i1+1]
            else:
                subset = desc[i1:i0+1]

        else:  # by == date
            d0 = _parse_dt(args.date_start) if args.date_start else datetime.min
            d1 = _parse_dt(args.date_end) if args.date_end else datetime.max
            subset = []
            for r in desc:
                try:
                    rd = _parse_dt(str(r.get(key_dt)))
                except Exception:
                    continue
                # inclusivo
                if rd >= d0 and rd <= d1:
                    subset.append(r)

        items = subset if order == "desc" else list(reversed(subset))

    # proiezione campi se richiesto
    if fields:
        items = [{k: v for k, v in r.items() if k in fields} for r in items]

    return {"kind": kind, "count": len(items), "items": items}

# ─────────────────────────────────────────────────────────────────────────────
# KPI helpers (ambiente & sociale)
# ─────────────────────────────────────────────────────────────────────────────
def _avg(values: List[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None

def _latest_value(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    for r in rows:  # rows già discendenti
        if key in r and r[key] is not None:
            try:
                return float(r[key])
            except Exception:
                return None
    return None

def _trend_delta(rows_desc: List[Dict[str, Any]], key: str, win: int) -> Optional[float]:
    """
    Calcola (current - mean(prev_win)).
    Ritorna None se non ci sono abbastanza dati.
    """
    vals = []
    for r in rows_desc:
        if key in r and r[key] is not None:
            try:
                vals.append(float(r[key]))
            except Exception:
                vals.append(None)
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    cur = vals[0]
    prev = vals[1:1+win]
    if not prev:
        return None
    return cur - (sum(prev) / len(prev))

# ─────────────────────────────────────────────────────────────────────────────
# TOOL 2 — Report Ambientale
# ─────────────────────────────────────────────────────────────────────────────
ENV_KPI_ORDER = [
    ("temperature", "Temperatura media ambiente"),
    ("humidity", "Umidità relativa media"),
    ("energy_specific", "Consumo energetico specifico"),
    ("water_specific", "Consumo idrico specifico"),
    ("vibration_g", "Livello vibrazioni macchine"),
    ("light", "Luminosità ambientale"),
    ("co2eq_ratio", "CO2eq.ris./CO2eq.tot"),
]
class EnvReportArgs(BaseModel):
    """Input per generare il Report Ambientale (markdown o json)."""
    by: Literal["index", "date"] = Field(
        default="index",
        description="Selezione dati.",
        json_schema_extra={"example": "date"},
    )
    idx_start: Optional[int] = Field(
        default=0,
        ge=0,
        description="Indice di inizio (solo by='index').",
        json_schema_extra={"example": 0},
    )
    idx_end: Optional[int] = Field(
        default=0,
        ge=0,
        description="Indice di fine incluso (solo by='index').",
        json_schema_extra={"example": 1440},
    )
    date_start: Optional[str] = Field(
        default=None,
        description="Data/DateTime inizio (solo by='date').",
        json_schema_extra={"example": "2025-09-01"},
    )
    date_end: Optional[str] = Field(
        default=None,
        description="Data/DateTime fine inclusiva (solo by='date').",
        json_schema_extra={"example": "2025-09-10T23:59:59"},
    )
    output_mode: Literal["text", "json"] = Field(
        default="text",
        description="Formato report.",
        json_schema_extra={"example": "json"},
    )
    facility: Optional[str] = Field(
        default="",
        description="Nome stabilimento mostrato in testata (non filtra ENV).",
        json_schema_extra={"example": "Stabilimento_Lino_B"},
    )
    decimals: int = Field(
        default=1,
        ge=0,
        le=6,
        description="Decimali per resa testuale.",
        json_schema_extra={"example": 1},
    )

    class Config:
        schema_extra = {
            "examples": [
                {"by":"index","idx_start":0,"idx_end":500,"output_mode":"text"},
                {"by":"date","date_start":"2025-09-01","date_end":"2025-09-07","output_mode":"json","facility":"Stabilimento_Lino_B"},
            ]
        }
def generate_environment_report_tool(args: EnvReportArgs) -> Any:
    """
    Descrizione:
    Genera il **Report di Sostenibilità Ambientale** nel formato del template.
    Valuta ogni KPI contro i target e calcola lo score complessivo e le aree
    di eccellenza/miglioramento. Supporta output 'text' (markdown) o 'json'.

    Ritorna:
    - Se output_mode='text' → stringa markdown.
    - Se output_mode='json' → dict strutturato.
    """
    targets = _load_targets().get("environment", {})
    trend_eps = float(targets.get("trend_epsilon", 0.1))
    win_n = int(targets.get("trend_window_n", 5))

    base = _load_env_rows()
    desc = list(reversed(base))  # più recente → prima
    # conversione vibrazioni
    for r in desc:
        if "acceleration" in r and "vibration_g" not in r:
            try:
                r["vibration_g"] = float(r["acceleration"]) / 9.81
            except Exception:
                pass

    # selezione
    if args.by == "index":
        i0 = args.idx_start or 0
        i1 = args.idx_end if args.idx_end is not None else i0
        i0 = min(max(i0, 0), max(len(desc)-1, 0))
        i1 = min(max(i1, 0), max(len(desc)-1, 0))
        subset = desc[min(i0, i1):max(i0, i1)+1]
    else:
        d0 = _parse_dt(args.date_start) if args.date_start else datetime.min
        d1 = _parse_dt(args.date_end) if args.date_end else datetime.max
        subset = []
        for r in desc:
            try:
                rd = _parse_dt(str(r.get("timestamp")))
            except Exception:
                continue
            if rd >= d0 and rd <= d1:
                subset.append(r)

    period_start = subset[-1]["timestamp"] if subset else (base[0]["timestamp"] if base else "N/D")
    period_end   = subset[0]["timestamp"] if subset else (base[-1]["timestamp"] if base else "N/D")

    # calcolo KPI
    rows = []
    scores = []
    areas_green, areas_red_or_yellow = [], []

    for k, label in ENV_KPI_ORDER:
        tdef = targets.get(k, {})
        unit = tdef.get("unit", "")

        # value corrente: media nel subset per KPI ambientali
        if subset:
            vals = [r.get(k) for r in subset if r.get(k) is not None]
            # fallback su nomi alternativi
            if not vals and k == "vibration_g":
                vals = [(r.get("acceleration")/9.81) for r in subset if r.get("acceleration") is not None]
            value = _avg(vals)
        else:
            value = None

        # trend
        delta = _trend_delta(desc, k, win_n)
        trend = _trend_arrow(delta, trend_eps)

        # status
        status = _status_from_targets(value, tdef) if tdef else ("na" if value is None else "na")
        scores.append(_score_from_status(status))

        if status == "green":
            areas_green.append(label)
        elif status in ("yellow", "red"):
            areas_red_or_yellow.append(label)

        # target string
        target_str = _mk_target_str_env(k, tdef) if tdef else "—"

        rows.append({
            "key": k,
            "label": label,
            "value": value,
            "value_str": f"{_fmt_num(value, args.decimals)}{(' ' + unit) if unit and value is not None else ''}" if value is not None else "N/D",
            "target": target_str,
            "status": status,
            "status_emoji": _status_emoji(status),
            "trend": trend
        })

    # score & sintesi
    scores_eff = [s for s in scores if s is not None]
    overall = round(sum(scores_eff)/len(scores_eff), 1) if scores_eff else 0.0
    fascia = "Eccellente 90-100" if overall >= 90 else ("Buono 70-89" if overall >= 70 else "Critico <70")

    # raccomandazioni (top 3 KPI peggiori)
    worst = sorted(rows, key=lambda r: {"green":3, "yellow":2, "red":1, "na":0}[r["status"]])[:3]
    suggest_map = {
        "Temperatura media ambiente": "Ottimizzare setpoint HVAC e manutenzione filtri/ventilazione.",
        "Umidità relativa media": "Regolare deumidificazione/umidificazione per la finestra ottimale del lino.",
        "Luminosità ambientale": "Tarare livelli luce e sensori presenza; sfruttare luce naturale.",
        "Livello vibrazioni macchine": "Eseguire analisi vibrazionale e manutenzione predittiva (cuscinetti/allineamenti).",
        "Consumo energetico specifico": "Audit energetico su compressori, trazione telai e HVAC.",
        "Consumo idrico specifico": "Ricircolo/processi a umido efficienti e monitoraggio perdite.",
        "CO2eq.ris./CO2eq.tot": "Valutare opportunità di recupero calore e materie prime a minor footprint.",
    }
    recs = []
    if worst:
        for w in worst:
            recs.append({
                "azione": f"Priorità su: {w['label']}",
                "impatto_stimato": "Medio/Alto" if w["status"] in ("red",) else "Medio",
                "nota": suggest_map.get(w["label"], "Intervento mirato per rientrare nel target.")
            })
    # almeno 3 righe, anche se ripetitive
    while len(recs) < 3:
        recs.append({"azione":"Ottimizzazione operativa mirata","impatto_stimato":"Medio","nota":"Azioni di breve termine su parametri fuori soglia."})

    if args.output_mode == "json":
        return {
            "period": {"start": period_start, "end": period_end},
            "facility": args.facility or "N/D",
            "kpis": {
                r["key"]: {
                    "label": r["label"],
                    "current": r["value"],
                    "display": r["value_str"],
                    "target": r["target"],
                    "status": r["status"],
                    "trend": r["trend"],
                } for r in rows
            },
            "score_overall": overall,
            "score_band": fascia,
            "areas_of_excellence": areas_green,
            "areas_of_improvement": areas_red_or_yellow,
            "recommendations": recs
        }

    # output markdown (template)
    def _mk_row(r: Dict[str, Any]) -> str:
        return f"| {r['label']} | {r['value_str']} | {r['target']} | {r['status_emoji']} | {r['trend']} |"

    md = []
    md.append("**OUTPUT - REPORT SOSTENIBILITÀ AMBIENTALE (DRAFT)**")
    md.append(f"\n**Periodo di riferimento:** `{period_start}` – `{period_end}`  •  **Stabilimento:** `{args.facility or 'None'}`\n")
    md.append("**INDICATORI CHIAVE DI PERFORMANCE (KPI)**")
    md.append("\n| Parametro | Valore Attuale | Target | Status | Trend |")
    md.append("|---|---:|---:|:--:|:--:|")
    for r in rows:
        md.append(_mk_row(r))

    md.append("\n**SINTESI PERFORMANCE AMBIENTALI**")
    md.append(f"\nPunteggio complessivo sostenibilità: **{overall}/100**  •  Fascia: **{fascia}**")
    if areas_green:
        md.append(f"\n**Aree di eccellenza:** {', '.join(areas_green)}")
    else:
        md.append("\n**Aree di eccellenza:** N/D")
    if areas_red_or_yellow:
        md.append(f"\n**Aree di miglioramento:** {', '.join(areas_red_or_yellow)}")
    else:
        md.append("\n**Aree di miglioramento:** N/D")

    md.append("\n**RACCOMANDAZIONI PRIORITARIE**")
    for i, r in enumerate(recs, 1):
        md.append(f"{i}. **[Azione]** {r['azione']} — Impatto stimato: *{r['impatto_stimato']}*. {r['nota']}")

    return "\n".join(md)

# ─────────────────────────────────────────────────────────────────────────────
# TOOL 3 — Report Sociale
# ─────────────────────────────────────────────────────────────────────────────
SOC_KPI_ORDER = [
    ("turnover_pct", "Tasso di turnover del personale"),
    ("training_hours_per_employee_y", "Ore di formazione per dipendente"),
    ("satisfaction_index", "Indice di soddisfazione dipendenti"),
    ("absenteeism_pct", "Tasso di assenteismo"),
    ("gender_female_pct", "Diversità di genere (% donne)"),
    ("accidents_per_1000h", "Infortuni sul lavoro (per 1000 ore)"),
    ("salary_vs_benchmark_pct", "Salario medio vs. benchmark settore"),
    ("ethical_suppliers_pct", "Fornitori certificati eticamente"),
    ("overtime_hours_per_employee_m", "Ore straordinarie per dipendente"),
    ("community_projects_count", "Coinvolgimento comunità locale"),
]

class SocialReportArgs(BaseModel):
    """Input per generare il Report Sociale (markdown o json)."""
    by: Literal["index", "date"] = Field(
        default="index",
        description="Selezione dati.",
        json_schema_extra={"example": "index"},
    )
    idx_start: Optional[int] = Field(
        default=0,
        ge=0,
        description="Indice di inizio (solo by='index').",
        json_schema_extra={"example": 0},
    )
    idx_end: Optional[int] = Field(
        default=0,
        ge=0,
        description="Indice di fine incluso (solo by='index').",
        json_schema_extra={"example": 3},
    )
    date_start: Optional[str] = Field(
        default=None,
        description="Data/DateTime inizio (solo by='date').",
        json_schema_extra={"example": "2025-04-01"},
    )
    date_end: Optional[str] = Field(
        default=None,
        description="Data/DateTime fine inclusiva (solo by='date').",
        json_schema_extra={"example": "2025-06-30"},
    )
    facility: Optional[str] = Field(
        default="",
        description="Filtro stabilimento (solo SOCIAL).",
        json_schema_extra={"example": "Stabilimento_Lino_B"},
    )
    output_mode: Literal["text", "json"] = Field(
        default="text",
        description="Formato report.",
        json_schema_extra={"example": "text"},
    )
    decimals: int = Field(
        default=1,
        ge=0,
        le=6,
        description="Decimali per resa testuale.",
        json_schema_extra={"example": 1},
    )

    class Config:
        schema_extra = {
            "examples": [
                {"by":"index","idx_start":0,"idx_end":0,"facility":"Stabilimento_Lino_B","output_mode":"text"},
                {"by":"date","date_start":"2025-04-01","date_end":"2025-06-30","output_mode":"json"},
            ]
        }

def _normalize_satisfaction(v: Optional[float], scale_in: Optional[float], targets: Dict[str, Any]) -> Optional[float]:
    if v is None:
        return None
    # i targets sono su scala 0-100; i dati possono arrivare su 0-10
    scale_tgt = float(targets.get("satisfaction_index", {}).get("scale", 100))
    if not scale_in:
        return v  # assume già compatibile
    try:
        return float(v) * (scale_tgt / float(scale_in))
    except Exception:
        return None

def generate_social_report_tool(args: SocialReportArgs) -> Any:
    """
    Descrizione:
    Genera il **Report di Sostenibilità Sociale** nel formato del template.
    Valuta ogni KPI contro i target e calcola score, aree di eccellenza/miglioramento.
    Supporta output 'text' (markdown) o 'json'.

    Ritorna:
    - Se output_mode='text' → stringa markdown.
    - Se output_mode='json' → dict strutturato.
    """
    targets_all = _load_targets()
    targets = targets_all.get("social", {})
    trend_eps = float(targets.get("trend_epsilon", 0.1))
    win_n = int(targets.get("trend_window_n", 3))

    base = _load_social_rows()
    if args.facility:
        base = [r for r in base if r.get("facility") == args.facility]
    desc = list(reversed(base))  # più recente per primo

    # selezione
    if args.by == "index":
        i0 = args.idx_start or 0
        i1 = args.idx_end if args.idx_end is not None else i0
        i0 = min(max(i0, 0), max(len(desc)-1, 0))
        i1 = min(max(i1, 0), max(len(desc)-1, 0))
        subset = desc[min(i0, i1):max(i0, i1)+1]
    else:
        key_dt = "saved_at" if any("saved_at" in r for r in desc) else "period_end"
        d0 = _parse_dt(args.date_start) if args.date_start else datetime.min
        d1 = _parse_dt(args.date_end) if args.date_end else datetime.max
        subset = []
        for r in desc:
            try:
                rd = _parse_dt(str(r.get(key_dt)))
            except Exception:
                continue
            if rd >= d0 and rd <= d1:
                subset.append(r)

    # periodo
    if subset:
        period_start = subset[-1].get("period_start", "N/D")
        period_end   = subset[0].get("period_end", "N/D")
        facility = subset[0].get("facility", args.facility or "N/D")
    else:
        period_start = period_end = "N/D"
        facility = args.facility or "N/D"

    # trends: confronto con righe precedenti
    def _trend_key(key: str) -> Optional[float]:
        prev = [r.get(key) for r in desc[1:1+win_n] if r.get(key) is not None]
        cur = desc[0].get(key) if desc else None
        if cur is None or not prev:
            return None
        try:
            return float(cur) - (sum(float(x) for x in prev) / len(prev))
        except Exception:
            return None

    # riga corrente (uso l’ultimo record del subset, cioè il più recente)
    current = subset[0] if subset else {}

    rows = []
    scores = []
    areas_green, areas_red_or_yellow = [], []

    for k, label in SOC_KPI_ORDER:
        tdef = targets.get(k, {})
        unit_disp = {
            "turnover_pct": "%",
            "training_hours_per_employee_y": "h/anno",
            "satisfaction_index": "/10",
            "absenteeism_pct": "%",
            "gender_female_pct": "%",
            "accidents_per_1000h": "",
            "salary_vs_benchmark_pct": "%",
            "ethical_suppliers_pct": "%",
            "overtime_hours_per_employee_m": "h/mese",
            "community_projects_count": "progetti",
        }.get(k, "")

        raw_val = current.get(k)
        if k == "satisfaction_index":
            # normalizza per la valutazione (scala 0-100)
            scaled = _normalize_satisfaction(
                raw_val,
                current.get("satisfaction_scale"),
                targets_all.get("social", {})
            )
            value_for_status = scaled
        else:
            value_for_status = float(raw_val) if raw_val is not None else None

        status = _status_from_targets(value_for_status, tdef) if tdef else ("na" if value_for_status is None else "na")
        scores.append(_score_from_status(status))

        trend = _trend_arrow(_trend_key(k), trend_eps)

        if status == "green":
            areas_green.append(label)
        elif status in ("yellow", "red"):
            areas_red_or_yellow.append(label)

        # visualizzazione valore attuale coerente al template
        if raw_val is None:
            val_str = "N/D"
        elif k == "satisfaction_index":
            # mostra su scala originale /10 se fornita
            sc = current.get("satisfaction_scale", 10)
            if float(sc) == 10:
                val_str = f"{_fmt_num(raw_val, args.decimals)}/10"
            else:
                val_str = f"{_fmt_num(raw_val, args.decimals)}"
        else:
            val_str = f"{_fmt_num(raw_val, args.decimals)}{(' ' + unit_disp) if unit_disp else ''}".strip()

        rows.append({
            "key": k,
            "label": label,
            "value": raw_val,
            "display": val_str,
            "target": _mk_target_str_soc(k, tdef),
            "status": status,
            "status_emoji": _status_emoji(status),
            "trend": trend,
        })

    # score & sintesi
    scores_eff = [s for s in scores if s is not None]
    overall = round(sum(scores_eff)/len(scores_eff), 1) if scores_eff else 0.0
    fascia = "Eccellente 90-100" if overall >= 90 else ("Buono 70-89" if overall >= 70 else "Critico <70")

    # raccomandazioni (top 3 peggiori KPI)
    worst = sorted(rows, key=lambda r: {"green":3, "yellow":2, "red":1, "na":0}[r["status"]])[:3]
    suggest_map = {
        "Tasso di turnover del personale": "Programmi di retention e piani di carriera.",
        "Ore di formazione per dipendente": "Aumentare formazione tecnica/sicurezza (>24h/anno).",
        "Indice di soddisfazione dipendenti": "Survey mirate e azioni su feedback critici.",
        "Tasso di assenteismo": "Welfare, flessibilità e prevenzione infortuni.",
        "Diversità di genere (% donne)": "Recruiting inclusivo e mentoring.",
        "Infortuni sul lavoro (per 1000 ore)": "Safety walk, formazione e manutenzione preventiva.",
        "Salario medio vs. benchmark settore": "Allineamento retributivo e leve non monetarie.",
        "Fornitori certificati eticamente": "Qualifica fornitori e clausole ESG.",
        "Ore straordinarie per dipendente": "Bilanciamento turni e automazione.",
        "Coinvolgimento comunità locale": "Programmi CSR con partner territoriali.",
    }
    recs = []
    for w in worst:
        recs.append({
            "azione": f"Priorità su: {w['label']}",
            "impatto_stimato": "Alto" if w["status"] == "red" else "Medio",
            "nota": suggest_map.get(w["label"], "Intervento mirato per rientrare nel target.")
        })
    while len(recs) < 3:
        recs.append({"azione":"Azioni organizzative e formative","impatto_stimato":"Medio","nota":"Migliorare gli indicatori sotto target."})

    if args.output_mode == "json":
        return {
            "period": {"start": period_start, "end": period_end},
            "facility": facility,
            "kpis": {
                r["key"]: {
                    "label": r["label"],
                    "current": r["value"],
                    "display": r["display"],
                    "target": r["target"],
                    "status": r["status"],
                    "trend": r["trend"],
                } for r in rows
            },
            "score_overall": overall,
            "score_band": fascia,
            "areas_of_excellence": areas_green,
            "areas_of_improvement": areas_red_or_yellow,
            "recommendations": recs
        }

    # markdown (template)
    def _mk_row(r: Dict[str, Any]) -> str:
        return f"| {r['label']} | {r['display']} | {r['target']} | {r['status_emoji']} | {r['trend']} |"

    md = []
    md.append("**OUTPUT - REPORT SOSTENIBILITÀ SOCIALE (DRAFT)**")
    md.append(f"\n**Periodo di riferimento:** `{period_start}` – `{period_end}`  •  **Stabilimento:** `{facility}`\n")
    md.append("**INDICATORI CHIAVE DI PERFORMANCE (KPI)**")
    md.append("\n| Parametro | Valore Attuale | Target | Status | Trend |")
    md.append("|---|---:|---:|:--:|:--:|")
    for r in rows:
        md.append(_mk_row(r))

    md.append("\n**SINTESI PERFORMANCE SOCIALI**")
    md.append(f"\nPunteggio complessivo sostenibilità sociale: **{overall}/100**  •  Fascia: **{fascia}**")
    md.append(f"\n**Aree di eccellenza:** {', '.join(areas_green) if areas_green else 'N/D'}")
    md.append(f"\n**Aree di miglioramento:** {', '.join(areas_red_or_yellow) if areas_red_or_yellow else 'N/D'}")

    md.append("\n**RACCOMANDAZIONI PRIORITARIE**")
    for i, r in enumerate(recs, 1):
        md.append(f"{i}. **[Azione]** {r['azione']} — Impatto stimato: *{r['impatto_stimato']}*. {r['nota']}")

    return "\n".join(md)

# ─────────────────────────────────────────────────────────────────────────────
# TOOL 4 — Lettura targets
# ─────────────────────────────────────────────────────────────────────────────
class GetTargetsArgs(BaseModel):
    """Input per leggere i targets dal file di configurazione."""
    section: Literal["environment", "social", "all"] = Field(
        default="all",
        description="Sezione richiesta.",
        json_schema_extra={"example": "all"},
    )

    class Config:
        schema_extra = {"examples": [{"section":"all"},{"section":"environment"},{"section":"social"}]}


def get_kpi_targets_tool(args: GetTargetsArgs) -> Dict[str, Any]:
    """
    Descrizione:
    Restituisce i targets correnti dal file configurato. Se il file non esiste
    viene creato automaticamente con i default.

    Output:
    - section richiesta (o entrambe) come dict Python (JSON-serializable).
    """
    t = _load_targets()
    if args.section == "all":
        return t
    return {args.section: t.get(args.section, {})}

# ─────────────────────────────────────────────────────────────────────────────
# ✨ NEW: MODELLI + TOOL DI LETTURA SPECIALIZZATI (ENV / SOCIAL)
#     - Wrapper leggeri sopra read_kpi_data_tool
#     - Stesse regole: indice 0 = più recente; filtri data inclusivi
# ─────────────────────────────────────────────────────────────────────────────

# --- Pydantic models ---------------------------------------------------------

class ReadEnvDataArgs(BaseModel):
    """
    Input per lettura **solo dati ambientali** (ENV).
    Selezione per indici (0 = più recente) o per date (intervallo inclusivo).
    """
    by: Literal["index", "date"] = Field(
        default="index", description="Modalità di selezione (index | date).",
        json_schema_extra={"example": "index"},
    )
    idx_start: Optional[int] = Field(
        default=0, ge=0, description="Indice di inizio (solo by='index').",
        json_schema_extra={"example": 0},
    )
    idx_end: Optional[int] = Field(
        default=0, ge=0, description="Indice di fine incluso (solo by='index').",
        json_schema_extra={"example": 300},
    )
    date_start: Optional[str] = Field(
        default=None, description="Data/DateTime inizio (solo by='date').",
        json_schema_extra={"example": "2025-09-01"},
    )
    date_end: Optional[str] = Field(
        default=None, description="Data/DateTime fine inclusiva (solo by='date').",
        json_schema_extra={"example": "2025-09-15T23:59:59"},
    )
    #fields: Optional[List[str]] = Field(
    #    default_factory=list, description="Proiezione campi (vuoto = tutti).",
    #    json_schema_extra={"example": ["timestamp", "temperature", "humidity", "vibration_g"]},
    #)
    #order: Literal["desc", "asc"] = Field(
    #    default="desc", description="Ordinamento del risultato.",
    #    json_schema_extra={"example": "desc"},
    #)

    class Config:
        schema_extra = {
            "examples": [
                {"by":"index","idx_start":0,"idx_end":100},
                {"by":"date","date_start":"2025-09-01","date_end":"2025-09-07"},
            ]
        }


class ReadSocialDataArgs(BaseModel):
    """
    Input per lettura **solo dati sociali** (SOCIAL).
    Selezione per indici (0 = più recente) o per date (intervallo inclusivo) + filtro facility.
    """
    by: Literal["index", "date"] = Field(
        default="index", description="Modalità di selezione (index | date).",
        json_schema_extra={"example": "date"},
    )
    idx_start: Optional[int] = Field(
        default=0, ge=0, description="Indice di inizio (solo by='index').",
        json_schema_extra={"example": 0},
    )
    idx_end: Optional[int] = Field(
        default=0, ge=0, description="Indice di fine incluso (solo by='index').",
        json_schema_extra={"example": 2},
    )
    date_start: Optional[str] = Field(
        default=None, description="Data/DateTime inizio (solo by='date').",
        json_schema_extra={"example": "2025-04-01"},
    )
    date_end: Optional[str] = Field(
        default=None, description="Data/DateTime fine inclusiva (solo by='date').",
        json_schema_extra={"example": "2025-06-30"},
    )
    facility: Optional[str] = Field(
        default="", description="Filtro stabilimento (es. 'Stabilimento_Lino_B').",
        json_schema_extra={"example": "Stabilimento_Lino_B"},
    )
    #fields: Optional[List[str]] = Field(
    #    default_factory=list, description="Proiezione campi (vuoto = tutti).",
    #    json_schema_extra={"example": ["period_start","period_end","turnover_pct","satisfaction_index"]},
    #)
    #order: Literal["desc", "asc"] = Field(
    #    default="desc", description="Ordinamento del risultato.",
    #    json_schema_extra={"example": "desc"},
    #)

    class Config:
        schema_extra = {
            "examples": [
                {"by":"index","idx_start":0,"idx_end":0,"facility":"Stabilimento_Lino_B"},
                {"by":"date","date_start":"2025-01-01","date_end":"2025-03-31"},
            ]
        }


# --- Wrapper functions (richiamano read_kpi_data_tool) -----------------------

def read_env_data_tool(args: ReadEnvDataArgs) -> Dict[str, Any]:
    """
    Lettura **specializzata ENV**.
    Reindirizza a `read_kpi_data_tool` impostando `kind='env'`.
    Output: {'kind':'env','count':<int>,'items':[...]} (ordinati secondo `order`).
    """
    # Riusa lo schema già definito per il tool generico
    generic = ReadKpiDataArgs(
        kind="env",
        by=args.by,
        idx_start=args.idx_start,
        idx_end=args.idx_end,
        date_start=args.date_start,
        date_end=args.date_end,
        #fields=args.fields,
        #order=args.order,
    )
    return read_kpi_data_tool(generic)


def read_social_data_tool(args: ReadSocialDataArgs) -> Dict[str, Any]:
    """
    Lettura **specializzata SOCIAL**.
    Reindirizza a `read_kpi_data_tool` impostando `kind='social'` e applicando `facility`.
    Output: {'kind':'social','count':<int>,'items':[...]} (ordinati secondo `order`).
    """
    generic = ReadKpiDataArgs(
        kind="social",
        by=args.by,
        idx_start=args.idx_start,
        idx_end=args.idx_end,
        date_start=args.date_start,
        date_end=args.date_end,
        facility=args.facility,
        #fields=args.fields,
        #order=args.order,
    )
    return read_kpi_data_tool(generic)

# ─────────────────────────────────────────────────────────────────────────────
# TOOL 5 — DSS/AHP Report (Semplificato: solo default interni)
# ─────────────────────────────────────────────────────────────────────────────
class DSSReportArgs(BaseModel):
    """Input per generare report DSS (AHP) con categorie ENV/SOC/FIN usando solo default interni."""
    by: Literal["index", "date"] = Field(default="index", description="Selezione dati ENV/SOC.")
    idx_start: Optional[int] = Field(default=0, ge=0, description="Indice inizio (solo by='index').")
    idx_end: Optional[int] = Field(default=0, ge=0, description="Indice fine incluso (solo by='index').")
    date_start: Optional[str] = Field(default=None, description="Data/DateTime inizio (solo by='date').")
    date_end: Optional[str] = Field(default=None, description="Data/DateTime fine inclusiva (solo by='date').")
    facility: Optional[str] = Field(default="", description="Filtro stabilimento per SOCIAL (opzionale).")
    output_mode: Literal["text", "json"] = Field(default="text", description="Formato output.")
    decimals: int = Field(default=2, ge=0, le=6, description="Decimali per valori/score.")

    class Config:
        schema_extra = {
            "examples": [
                {"by": "index", "idx_start": 0, "idx_end": 200, "output_mode": "text"},
                {"by": "date", "date_start": "2025-01-01", "date_end": "2025-03-31", "facility": "Stabilimento_Lino_B", "output_mode": "json"}
            ]
        }

def generate_dss_report_tool(args: DSSReportArgs) -> Any:
    """
    Genera un **Report DSS/AHP** combinando categorie (ENV/SOC/FIN) e indicatori interni con soli default.
    - Recupera i KPI ENV e SOCIAL dal dataset (finestra per indici o per date, filtri inclusivi).
    - Normalizza i KPI in 0–1 dal loro **status**: 🟢=1.0, 🟡=0.8, 🔴=0.5, ⚪=0.0.
    - KPI FINANZIARI: usa **valori di default interni** (non configurabili da input).
    - Pesi AHP:
        • Matrice categorie (A) fissa (ENV,SOC,FIN) dal documento DSS.
        • Matrici interne (B) = equal-weight (costruite con _pairwise_equal_matrix(n)).
    - Score finale = Σ_c ( w_cat[c] × Σ_i ( w_intra[c,i] × norm[c,i] ) ).

    Output:
      - 'text' → markdown template DSS.
      - 'json' → struttura JSON con pesi, CR, dettagli e ranking.
    """
    # ---------- 1) Targets e dati base ----------
    targets_all = _load_targets()
    t_env = targets_all.get("environment", {})
    t_soc = targets_all.get("social", {})

    # ENV: carica e seleziona subset
    env_base = _load_env_rows()
    env_desc = list(reversed(env_base))
    if args.by == "index":
        i0 = args.idx_start or 0
        i1 = args.idx_end if args.idx_end is not None else i0
        i0 = min(max(i0, 0), max(len(env_desc)-1, 0))
        i1 = min(max(i1, 0), max(len(env_desc)-1, 0))
        env_subset = env_desc[min(i0, i1):max(i0, i1)+1]
    else:
        d0 = _parse_dt(args.date_start) if args.date_start else datetime.min
        d1 = _parse_dt(args.date_end) if args.date_end else datetime.max
        env_subset = []
        for r in env_desc:
            try:
                rd = _parse_dt(str(r.get("timestamp")))
            except Exception:
                continue
            if d0 <= rd <= d1:
                env_subset.append(r)
    # conversione vibrazioni se serve
    for r in env_subset:
        if "acceleration" in r and "vibration_g" not in r:
            try:
                r["vibration_g"] = float(r["acceleration"]) / 9.81
            except Exception:
                pass

    # SOCIAL: carica e seleziona subset
    soc_base = _load_social_rows()
    if args.facility:
        soc_base = [r for r in soc_base if r.get("facility") == args.facility]
    soc_desc = list(reversed(soc_base))
    if args.by == "index":
        j0 = args.idx_start or 0
        j1 = args.idx_end if args.idx_end is not None else j0
        j0 = min(max(j0, 0), max(len(soc_desc)-1, 0))
        j1 = min(max(j1, 0), max(len(soc_desc)-1, 0))
        soc_subset = soc_desc[min(j0, j1):max(j0, j1)+1]
    else:
        key_dt = "saved_at" if any("saved_at" in r for r in soc_desc) else "period_end"
        d0 = _parse_dt(args.date_start) if args.date_start else datetime.min
        d1 = _parse_dt(args.date_end) if args.date_end else datetime.max
        soc_subset = []
        for r in soc_desc:
            try:
                rd = _parse_dt(str(r.get(key_dt)))
            except Exception:
                continue
            if d0 <= rd <= d1:
                soc_subset.append(r)

    # Periodo/facility
    period_start = (env_subset[-1]["timestamp"] if env_subset else (soc_subset[-1].get("period_start") if soc_subset else "N/D")) if (env_subset or soc_subset) else "N/D"
    period_end   = (env_subset[0]["timestamp"]  if env_subset else (soc_subset[0].get("period_end")   if soc_subset else "N/D")) if (env_subset or soc_subset) else "N/D"
    facility = (soc_subset[0].get("facility") if soc_subset and soc_subset[0].get("facility") else (args.facility or "N/D"))

    # ---------- 2) Aggregazione & normalizzazione (0–1) ----------
    # ENV: media sul subset per ciascun KPI con soglia
    env_values = {}
    for k in ENV_KPI_FOR_DSS:
        tdef = t_env.get(k, {})
        if not _has_thresholds(tdef):
            continue
        vals = [r.get(k) for r in env_subset if r.get(k) is not None]
        if not vals and k == "vibration_g":
            vals = [(r.get("acceleration")/9.81) for r in env_subset if r.get("acceleration") is not None]
        value = _avg(vals) if vals else None
        status = _status_from_targets(value, tdef) if value is not None else "na"
        env_values[k] = {"value": value, "status": status, "norm": _status_to_norm01(status)}

    # SOCIAL: media sul subset; satisfaction normalizzata su 0–100 per lo status
    soc_values = {}
    for k, _label in SOC_KPI_ORDER:
        tdef = t_soc.get(k, {})
        if not _has_thresholds(tdef):
            continue
        raw_list = [r.get(k) for r in soc_subset if r.get(k) is not None]
        if k == "satisfaction_index":
            scaled_list = []
            for r in soc_subset:
                v = r.get("satisfaction_index")
                if v is None:
                    continue
                sc = r.get("satisfaction_scale")
                scaled = _normalize_satisfaction(v, sc, t_soc)
                if scaled is not None:
                    scaled_list.append(scaled)
            v_mean_for_status = _avg(scaled_list) if scaled_list else None
            value = _avg(raw_list) if raw_list else None
            status = _status_from_targets(v_mean_for_status, tdef) if v_mean_for_status is not None else "na"
        else:
            value = _avg(raw_list) if raw_list else None
            status = _status_from_targets(value, tdef) if value is not None else "na"
        soc_values[k] = {"value": value, "status": status, "norm": _status_to_norm01(status)}

    # FIN: valori 0–1 di default interni (nessun input)
    fin_values = {}
    _FIN_DEFAULTS = {
        "sustainable_cost_index": 0.60,
        "energy_efficiency_index": 0.70,
        "revenue_impact_index": 0.55,
    }
    for k, _label in FIN_KPI_ORDER:
        v = float(_FIN_DEFAULTS[k])
        status = "green" if v >= 0.8 else ("yellow" if v >= 0.6 else "red")
        fin_values[k] = {"value": v, "status": status, "norm": v}

    # ---------- 3) Pesi AHP (solo default interni) ----------
    # Matrice categorie (A) fissa dal documento DSS
    cat_A = [
        [1.0, 3.0, 2.0],
        [1.0/3.0, 1.0, 0.5],
        [0.5, 2.0, 1.0],
    ]
    w_cat, cr_cat = _ahp_weights_and_cr(cat_A)

    # Matrici interne (B) = equal-weight in base ai KPI disponibili
    env_keys = [k for k in ENV_KPI_FOR_DSS if k in env_values]
    soc_keys = [k for k, _ in SOC_KPI_ORDER if k in soc_values]
    fin_keys = [k for k, _ in FIN_KPI_ORDER if k in fin_values]

    def _equal_weights_for(keys):
        n = len(keys)
        if n == 0:
            return [], 0.0
        return _ahp_weights_and_cr(_pairwise_equal_matrix(n))

    w_env, cr_env = _equal_weights_for(env_keys)
    w_soc, cr_soc = _equal_weights_for(soc_keys)
    w_fin, cr_fin = _equal_weights_for(fin_keys)

    # ---------- 4) Score per categoria e globale ----------
    def _cat_score(keys, weights, bucket):
        if not keys or not weights:
            return 0.0
        return sum(weights[i] * float(bucket[keys[i]]["norm"]) for i in range(len(keys)))

    score_env = _cat_score(env_keys, w_env, env_values)
    score_soc = _cat_score(soc_keys, w_soc, soc_values)
    score_fin = _cat_score(fin_keys, w_fin, fin_values)

    overall = (
        (w_cat[0] if len(w_cat) > 0 else 0.0) * score_env +
        (w_cat[1] if len(w_cat) > 1 else 0.0) * score_soc +
        (w_cat[2] if len(w_cat) > 2 else 0.0) * score_fin
    )

    ranking = sorted(
        [
            {"category": "Ambientale", "score": round(score_env, args.decimals)},
            {"category": "Sociale", "score": round(score_soc, args.decimals)},
            {"category": "Economico", "score": round(score_fin, args.decimals)},
        ],
        key=lambda x: x["score"],
        reverse=True
    )

    notes = []
    if cr_cat > 0.1: notes.append(f"Attenzione: CR Matrice Categorie = {cr_cat:.3f} > 0.1")
    if cr_env > 0.1 and env_keys: notes.append(f"CR ENV = {cr_env:.3f} > 0.1")
    if cr_soc > 0.1 and soc_keys: notes.append(f"CR SOCIAL = {cr_soc:.3f} > 0.1")
    if cr_fin > 0.1 and fin_keys: notes.append(f"CR FIN = {cr_fin:.3f} > 0.1")
    if not (env_subset or soc_subset):
        notes.append("Attenzione: nessun record nel range selezionato (score derivato da default FIN).")
    else:
        notes.append("Indicatori FIN calcolati da default interni (non personalizzati da input).")

    # ---------- 5) Output ----------
    if args.output_mode == "json":
        return {
            "period": {"start": period_start, "end": period_end},
            "facility": facility,
            "ahp": {
                "category": {"weights": {"ENV": w_cat[0], "SOC": w_cat[1], "FIN": w_cat[2]}, "cr": cr_cat},
                "environment": {"indicators": {k: w_env[i] for i, k in enumerate(env_keys)}, "cr": cr_env},
                "social": {"indicators": {k: w_soc[i] for i, k in enumerate(soc_keys)}, "cr": cr_soc},
                "financial": {"indicators": {k: w_fin[i] for i, k in enumerate(fin_keys)}, "cr": cr_fin},
            },
            "indicators": {
                "environment": env_values,
                "social": soc_values,
                "financial": fin_values,
            },
            "scores": {
                "environment": round(score_env, args.decimals),
                "social": round(score_soc, args.decimals),
                "financial": round(score_fin, args.decimals),
                "overall": round(overall, args.decimals),
            },
            "ranking": ranking,
            "notes": notes,
        }

    # Markdown
    def _mk_table(keys, weights, bucket, header):
        lines = [
            f"\n**{header}**",
            "\n| Indicatore | Norm. (0–1) | Peso interno | Score parziale |",
            "|---|---:|---:|---:|",
        ]
        for i, k in enumerate(keys):
            norm = bucket[k]["norm"]
            sc = (weights[i] * norm) if weights else 0.0
            lines.append(f"| {k} | {norm:.{args.decimals}f} | {(weights[i] if weights else 0.0):.{args.decimals}f} | {sc:.{args.decimals}f} |")
        return "\n".join(lines)

    md = []
    md.append("**OUTPUT - REPORT DSS / AHP (DRAFT)**")
    md.append(f"\n**Periodo di riferimento:** `{period_start}` – `{period_end}`  •  **Stabilimento:** `{facility}`\n")
    md.append(f"**Pesi categorie (A) [CR={cr_cat:.3f}]**  \nENV: {w_cat[0]:.{args.decimals}f}  •  SOC: {w_cat[1]:.{args.decimals}f}  •  FIN: {w_cat[2]:.{args.decimals}f}")

    md.append(_mk_table(env_keys, w_env, env_values, f"Categoria Ambientale (CR={cr_env:.3f})"))
    md.append(_mk_table(soc_keys, w_soc, soc_values, f"Categoria Sociale (CR={cr_soc:.3f})"))
    md.append(_mk_table(fin_keys, w_fin, fin_values, f"Categoria Economica/Finanziaria (CR={cr_fin:.3f})"))

    md.append("\n**SCORE DI CATEGORIA**")
    md.append(f"- Ambientale: **{score_env:.{args.decimals}f}**")
    md.append(f"- Sociale: **{score_soc:.{args.decimals}f}**")
    md.append(f"- Economico: **{score_fin:.{args.decimals}f}**")

    md.append(f"\n**SCORE FINALE (AHP)**: **{overall:.{args.decimals}f}**")

    md.append("\n**RANKING PRIORITÀ**")
    for i, r in enumerate(ranking, 1):
        md.append(f"{i}. {r['category']} — score {r['score']:.{args.decimals}f}")

    if notes:
        md.append("\n**NOTE**")
        for n in notes:
            md.append(f"- {n}")

    return "\n".join(md)


# ─────────────────────────────────────────────────────────────────────────────
# Esportazione tool in formato LangChain
# (Puoi scegliere subset per ogni agente nel tuo core.)
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Re-binding dei Structured Tools con wrapper (nessuna logica cambiata)
# ─────────────────────────────────────────────────────────────────────────────

read_kpi_data = StructuredTool.from_function(
    func=_wrap_args(ReadKpiDataArgs, read_kpi_data_tool),
    name="read_kpi_data",
    description=(
        "Legge dataset 'env' o 'social'. Invarianti: indice 0 = più recente; filtro date inclusivo; "
        "facility solo per social; intervalli fuori range → restituisce ciò che è disponibile.\n"
        "Esempi: "
        "read_kpi_data(kind='env', by='index', idx_start=0, idx_end=100) | "
        "read_kpi_data(kind='social', by='date', date_start='2025-01-01', date_end='2025-03-31', facility='Stabilimento_Lino_B')."
    ),
    args_schema=ReadKpiDataArgs,
)

generate_environment_report = StructuredTool.from_function(
    func=_wrap_args(EnvReportArgs, generate_environment_report_tool),
    name="generate_environment_report",
    description=(
        "Crea il Report Ambientale aderente al template. Usa targets per status, calcola trend e score medio. "
        "Output: 'text' (markdown) o 'json' (strutturato). Selezione per indici o date.\n"
        "Esempi: generate_environment_report(by='index', idx_start=0, idx_end=1440, output_mode='text') | "
        "generate_environment_report(by='date', date_start='2025-09-01', date_end='2025-09-10', output_mode='json')."
    ),
    args_schema=EnvReportArgs,
)

generate_social_report = StructuredTool.from_function(
    func=_wrap_args(SocialReportArgs, generate_social_report_tool),
    name="generate_social_report",
    description=(
        "Crea il Report Sociale aderente al template. Normalizza satisfaction alla scala target, "
        "valuta status vs targets, calcola trend e score. Output 'text' o 'json'. Supporta filtro 'facility'.\n"
        "Esempi: generate_social_report(by='index', idx_start=0, idx_end=0, facility='Stabilimento_Lino_B') | "
        "generate_social_report(by='date', date_start='2025-04-01', date_end='2025-06-30', output_mode='json')."
    ),
    args_schema=SocialReportArgs,
)

get_kpi_targets = StructuredTool.from_function(
    func=_wrap_args(GetTargetsArgs, get_kpi_targets_tool),
    name="get_kpi_targets",
    description=(
        "Ritorna i targets (environment/social). Se il file manca viene creato con default. "
        "Esempi: get_kpi_targets(section='all') | get_kpi_targets(section='environment')."
    ),
    args_schema=GetTargetsArgs,
)

read_env_data = StructuredTool.from_function(
    func=_wrap_args(ReadEnvDataArgs, read_env_data_tool),
    name="read_env_data",
    description=(
        "Legge solo dati ambientali (ENV). Indice 0 = più recente; filtri date inclusivi; "
        "intervalli fuori range gestiti. Esempi: read_env_data(by='index', idx_start=0, idx_end=200) | "
        "read_env_data(by='date', date_start='2025-09-01', date_end='2025-09-07')."
    ),
    args_schema=ReadEnvDataArgs,
)

read_social_data = StructuredTool.from_function(
    func=_wrap_args(ReadSocialDataArgs, read_social_data_tool),
    name="read_social_data",
    description=(
        "Legge solo dati sociali (SOCIAL) con filtro facility opzionale. Indice 0 = più recente; "
        "filtri date inclusivi; intervalli fuori range gestiti. Esempi: "
        "read_social_data(by='index', idx_start=0, idx_end=0, facility='Stabilimento_Lino_B') | "
        "read_social_data(by='date', date_start='2025-04-01', date_end='2025-06-30')."
    ),
    args_schema=ReadSocialDataArgs,
)

generate_dss_report = StructuredTool.from_function(
    func=_wrap_args(DSSReportArgs, generate_dss_report_tool),
    name="generate_dss_report",
    description=(
        "Genera un report DSS/AHP combinando categorie **ENV/SOC/FIN**. "
        "Recupera i KPI ENV/SOC dal dataset (finestra per indici o date), normalizza 0–1 da status, "
        "usa AHP per pesi di categorie e indicatori. I KPI finanziari sono simulati (override via "
        "'financial_mock_values'). Output 'text' (markdown) o 'json'."
    ),
    args_schema=DSSReportArgs,
)


# Lista completa (puoi filtrare per modalità nel core)
TOOLS = [generate_dss_report, read_env_data, read_social_data, read_kpi_data, generate_environment_report, generate_social_report, get_kpi_targets]

##############################################
class _PingArgs(BaseModel):
    pass

def _ping_impl(_: _PingArgs) -> str:
    return "PONG"

ping = StructuredTool.from_function(
    func=_wrap_args(_PingArgs, _ping_impl),
    name="ping",
    description="Tool di test: ritorna sempre 'PONG'. Nessun argomento.",
    args_schema=_PingArgs,
)

############################################