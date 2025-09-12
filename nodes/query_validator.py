# nodes/query_validator.py
import logging
import re
import sqlite3
from utils.types import AgentState
from utils.llm import call_llm_text

log = logging.getLogger("validator")

# -----------------------------
# Yardımcılar
# -----------------------------
def _extract_cte_names(sql: str) -> set[str]:
    """
    WITH cte_name AS (...) kısımlarındaki CTE adlarını yakalar.
    Çoklu CTE: WITH a AS (...), b AS (...).
    """
    s = sql.strip()
    low = s.lower()
    if not low.startswith("with"):
        return set()
    # 'with  a  as (...) ,  "B"  as (...) select ...'
    # cte adı: virgüllerle ayrılır, 'as' öncesi ilk token
    # Basit ve yeterli bir yaklaşımla toplayalım:
    head = re.split(r"\bselect\b", low, maxsplit=1)[0]  # WITH bloğunun baş tarafı
    # Aynı uzunlukta orijinal parçadan adları çıkaracağız (case/quote için)
    orig_head = s[:len(head)]
    names = []
    for m in re.finditer(r'\bwith\b\s*(.+?)\bselect\b', low, flags=re.S):
        # Tüm WITH bloğunu aldık; içinde "name AS" örüntülerini ara (orijinalde)
        segment = s[m.start():m.end()]
        for m2 in re.finditer(r'([a-zA-Z0-9_"]+)\s+as\s*\(', segment, flags=re.I):
            name = m2.group(1).strip().strip('"')
            names.append(name)
    return set(names)

def _extract_table_names(sql: str) -> set[str]:
    """
    FROM/JOIN sonrasındaki ham tablo isimlerini çıkarır.
    "schema.table" -> "table", "quoted" -> quoted içi
    """
    s = sql
    low = s.lower()
    names = set(re.findall(r"\bfrom\s+([a-zA-Z0-9_\"\.]+)", low))
    names |= set(re.findall(r"\bjoin\s+([a-zA-Z0-9_\"\.]+)", low))
    clean = set(n.strip('"').split(".")[-1] for n in names if n)
    return clean

def _has_multiple_statements(sql: str) -> bool:
    """
    Birden fazla statement var mı? (örn. SELECT ... ; DROP TABLE ...)
    Sona opsiyonel tek ';' izin, onun dışında ';' görülürse çoklu kabul.
    """
    body = sql.strip()
    if body.endswith(";"):
        body = body[:-1]
    return ";" in body

# -----------------------------
# Statik doğrulama
# -----------------------------
def static_check(
    sql: str,
    banned_keywords: list[str],
    enforce_select_only: bool,
    allowed_tables: set[str],
) -> tuple[bool, str]:
    """
    - (Ops.) Sadece SELECT/WITH ile başlasın
    - Yasaklı anahtar kelimeler
    - Çoklu statement yasağı
    - İzinli olmayan tablo adı kullanımı (CTE'ler hariç)
    - COUNT/AVG/SUM gibi agregatlarda LIMIT uyarısı (FAIL değil, WARN)
    """
    original = sql.strip()
    low = original.lower()

    # 0) Çoklu statement
    if _has_multiple_statements(original):
        return False, "Multiple SQL statements are not allowed"

    # 1) SELECT/WITH-only
    if enforce_select_only and not (low.startswith("select") or low.startswith("with")):
        return False, "Only SELECT (or WITH..SELECT) statements are allowed"

    # 2) Banned keywords (tam kelime)
    for kw in banned_keywords:
        if re.search(rf"\b{re.escape(kw.lower())}\b", low):
            return False, f"Banned keyword detected: {kw}"

    # 3) İzinli tablo beyaz listesi (CTE'leri hariç)
    used_tables = _extract_table_names(original)
    cte_names = _extract_cte_names(original)
    bad = [t for t in used_tables if t and t not in allowed_tables and t not in cte_names]
    if bad:
        return False, f"Disallowed table(s): {', '.join(sorted(set(bad)))}"

    # 4) Agregat + LIMIT uyarısı (FAIL değil)
    warn = ""
    if re.search(r"\b(count|avg|sum|min|max)\s*\(", low) and re.search(r"\blimit\b", low):
        warn = "WARN: LIMIT clause is unnecessary for aggregate queries"

    return True, warn

# -----------------------------
# EXPLAIN kontrolü
# -----------------------------
def explain_check(conn: sqlite3.Connection, sql: str) -> tuple[bool, str]:
    try:
        # Tek trailing ';' varsa kaldır
        q = sql.strip()
        if q.endswith(";"):
            q = q[:-1]
        cur = conn.cursor()
        cur.execute("EXPLAIN QUERY PLAN " + q)
        plan = cur.fetchall()
        if not plan:
            return False, "Empty EXPLAIN plan"
        if any("SCAN" in str(p).upper() for p in plan):
            return True, "Plan warning: full scan possible"
        return True, ""
    except Exception as e:
        return False, str(e)

# -----------------------------
# Anlamsal kontrol (LLM-critic)
# -----------------------------
def semantic_check(state: AgentState, llm_service, cost) -> tuple[bool, str]:
    user_q = state.question
    sql = state.candidate_sql[-1] if state.candidate_sql else ""
    if not sql:
        return False, "No SQL candidate"

    system_prompt = """You are a SQL validator for an Analyst Agent.
Target DB is SQLite.
Decide if the SQL correctly answers the user question.
Be strict about correct tables/columns and time bucketing (strftime).
Answer strictly 'OK' or 'FAIL: <short reason>'.
"""
    user_prompt = f"QUESTION:\n{user_q}\n\nSQL:\n{sql}"
    result = call_llm_text(llm_service, system_prompt, user_prompt, cost=cost).strip()
    if result.upper().startswith("OK"):
        return True, ""
    return False, result

# -----------------------------
# Üst seviye akış
# -----------------------------
def run(
    conn: sqlite3.Connection,
    state: AgentState,
    banned_keywords: list[str],
    enforce_select_only: bool,
    allow_multiple: bool,      # (kullanılmıyor; geriye dönük imza için tutuldu)
    max_limit: int,            # (kullanılmıyor; validator limit enjekte etmez)
    llm_service=None,
    cost=None,
    allowed_tables: list[str] = None,
) -> AgentState:
    """
    1) static_check (SELECT/WITH-only, banned, whitelist, çoklu statement)
    2) EXPLAIN
    3) semantic_check (varsa)
    """
    sql = state.candidate_sql[-1] if state.candidate_sql else ""
    if not sql:
        state.validation_report = {"ok": False, "reason": "No SQL candidate"}
        return state

    allowed_set = set(allowed_tables or [])

    # 1) Statik
    ok, reason = static_check(sql, banned_keywords, enforce_select_only, allowed_set)
    if not ok:
        log.warning("static_check FAIL: %s | sql='%s'", reason, sql)
        state.validation_report = {"ok": False, "reason": reason}
        return state
    if reason:
        log.info("static_check warning: %s | sql='%s'", reason, sql)

    # 2) EXPLAIN
    ok, reason = explain_check(conn, sql)
    if not ok:
        log.warning("EXPLAIN FAIL: %s | sql='%s'", reason, sql)
        state.validation_report = {"ok": False, "reason": reason}
        return state
    if reason:
        log.info("EXPLAIN warning: %s", reason)

    # 3) LLM-critic (opsiyonel)
    if llm_service:
        ok, reason = semantic_check(state, llm_service, cost)
        if not ok:
            log.warning("semantic_check FAIL: %s | sql='%s'", reason, sql)
            state.validation_report = {"ok": False, "reason": reason}
            return state

    # Başarılı
    state.validated_sql = sql
    state.validation_report = {"ok": True}
    log.info("Validation OK.")
    return state
