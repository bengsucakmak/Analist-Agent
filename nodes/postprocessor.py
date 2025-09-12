import logging
from typing import Dict, Any, List, Tuple
import re
import math
import sqlite3
from utils.types import AgentState

log = logging.getLogger("post")

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)

def _load_unit_map(conn: sqlite3.Connection) -> Dict[str, str]:
    try:
        cur = conn.execute("SELECT unit_id, unit_name FROM unit")
        return {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        return {}

def _humanize_rows(rows: List[Dict[str, Any]], unit_map: Dict[str, str]) -> List[Dict[str, Any]]:
    if not rows:
        return rows
    out = []
    for r in rows:
        r2 = dict(r)
        # unit_id → unit_name
        for k, v in list(r2.items()):
            if k.lower() in ("unit_id", "unit"):
                if isinstance(v, str) and UUID_RE.match(v) and v in unit_map:
                    r2.pop(k, None)
                    r2["unit_name"] = unit_map[v]
            # Genel UUID kolonları da isimle değiştirilebilirse burada benzer mantık eklenebilir
        out.append(r2)
    return out

def _weighted_avg(rows: List[Dict[str, Any]], avg_key: str, weight_key: str) -> float | None:
    """rows içinde avg_key (örn avg_age) ve weight_key (örn n) varsa ağırlıklı ortalama."""
    try:
        num = 0.0
        den = 0.0
        for r in rows:
            a = r.get(avg_key)
            w = r.get(weight_key)
            if a is None or w is None:
                continue
            num += float(a) * float(w)
            den += float(w)
        if den <= 0:
            return None
        return round(num / den, 2)
    except Exception:
        return None

def run(state: AgentState, conn: sqlite3.Connection) -> AgentState:
    # ID→name humanization
    unit_map = _load_unit_map(conn)
    state.rows_preview = _humanize_rows(state.rows_preview or [], unit_map)

    # Ağırlıklı genel ortalama (varsa)
    # Sık gördüğümüz kolon isimlerini dene:
    avg_keys = ["avg_age", "average_age", "avg", "mean_age"]
    weight_keys = ["n", "n_users", "count", "users"]
    rows = state.rows_preview or []
    overall = None
    for ak in avg_keys:
        for wk in weight_keys:
            overall = _weighted_avg(rows, ak, wk)
            if overall is not None:
                state.execution_stats = state.execution_stats or {}
                state.execution_stats["overall_avg_age_estimate"] = overall
                break
        if overall is not None:
            break

    log.info("Postprocess tamam. Humanized=%d rows, overall=%s", len(rows), overall)
    return state