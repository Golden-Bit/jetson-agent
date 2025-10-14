# ─────────────────────────────────────────────────────────────────────────────
# AHP helpers (pesi, consistenza) + costanti DSS
# ─────────────────────────────────────────────────────────────────────────────
from typing import List, Tuple, Dict, Any

# Random Index (Saaty) per n = 1..10
_AHP_RI = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49}

# ─────────────────────────────────────────────────────────────────────────────
# KPI inclusi nel DSS (solo quelli con soglie per l'ENV)
# ─────────────────────────────────────────────────────────────────────────────
ENV_KPI_FOR_DSS = ["temperature", "humidity", "light", "vibration_g", "co2_ppm", "distance_mm"]

FIN_KPI_ORDER = [
    ("sustainable_cost_index", "Indice costo produzione sostenibile"),
    ("energy_efficiency_index", "Indice efficienza energetica"),
    ("revenue_impact_index", "Impatto ricavi prodotti sostenibili"),
]


def _ahp_weights_and_cr(matrix: List[List[float]]) -> Tuple[List[float], float]:
    """Calcola i pesi AHP (metodo della media geometrica) e il Consistency Ratio."""
    n = len(matrix)
    if n == 0 or any(len(row) != n for row in matrix):
        raise ValueError("Matrice AHP non quadrata o vuota.")
    # media geometrica per riga
    g = []
    for i in range(n):
        prod = 1.0
        for j in range(n):
            prod *= float(matrix[i][j])
        g.append(prod ** (1.0 / n))
    s = sum(g) or 1.0
    w = [gi / s for gi in g]  # pesi normalizzati

    # lambda_max
    Aw = []
    for i in range(n):
        val = 0.0
        for j in range(n):
            val += float(matrix[i][j]) * w[j]
        Aw.append(val)
    lambda_max = sum((Aw[i] / (w[i] or 1e-12)) for i in range(n)) / n

    # Consistency Ratio
    ci = (lambda_max - n) / (n - 1) if n > 2 else 0.0
    ri = _AHP_RI.get(n, 1.49)  # fallback prudente
    cr = (ci / ri) if ri > 0 else 0.0
    return w, cr

def _pairwise_equal_matrix(n: int) -> List[List[float]]:
    """Matrice di confronto 'tutti uguali' (peso uniforme)."""
    return [[1.0 if i == j else 1.0 for j in range(n)] for i in range(n)]

def _status_to_norm01(status: str) -> float:
    """Mappa lo status a normalizzazione 0–1 coerente con i punteggi (green=1.0, yellow=0.8, red=0.5, na=0)."""
    return {"green": 1.0, "yellow": 0.8, "red": 0.5}.get(status, 0.0)

def _has_thresholds(tdef: Dict[str, Any]) -> bool:
    """True se il target ha soglie utili (green/yellow o target±tol)."""
    return ("target" in tdef and "tol" in tdef) or ("green" in tdef)
