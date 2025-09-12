import re
from typing import Tuple

def is_select_only(sql: str) -> bool:
    s = sql.strip().strip(";").lower()
    # WITH ... SELECT veya SELECT ile başlamalı
    return s.startswith("select") or s.startswith("with")

def has_multiple_statements(sql: str) -> bool:
    # naif kontrol: ; sayısı >1 ise çoklu statement olabilir
    return sql.strip().count(";") > 1

def contains_banned(sql: str, banned_keywords) -> str | None:
    s = re.sub(r"\s+", " ", sql.lower())
    for kw in banned_keywords:
        if re.search(rf"\b{re.escape(kw)}\b", s):
            return kw
    return None

def ensure_limit(sql: str, max_limit: int) -> str:
    s = sql.strip().rstrip(";")
    # LIMIT var mı?
    if re.search(r"\blimit\s+\d+\b", s, flags=re.I):
        return s + ";"
    return f"{s} LIMIT {max_limit};"

def sanitize_sql(sql: str) -> str:
    # yorumları kaldır (naif)
    sql = re.sub(r"--.*?$", "", sql, flags=re.M)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return sql.strip()

def static_checks(sql: str, banned_keywords, select_only=True, allow_multi=False) -> Tuple[bool, str]:
    s = sanitize_sql(sql)
    if select_only and not is_select_only(s):
        return False, "SELECT-only kuralı ihlali."
    if not allow_multi and has_multiple_statements(s):
        return False, "Çoklu statement yasak."
    bad = contains_banned(s, banned_keywords)
    if bad:
        return False, f"Yasaklı anahtar kelime tespit edildi: {bad}"
    return True, "OK"

SQL_BLOCK_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.S|re.I)

def extract_single_select(sql_text: str) -> str | None:
    """
    Metin içinden TEK bir SELECT/WITH statement çıkarır.
    - Kod bloklarını önceler, yoksa tüm metinde arar.
    - İlk bulduğu SELECT/WITH ile başlayan cümleyi alır.
    """
    txt = sql_text.strip()
    m = SQL_BLOCK_RE.search(txt)
    cand = m.group(1).strip() if m else txt

    # İlk SELECT/WITH'i bul
    m2 = re.search(r"(?is)\b(with\b.*?;|\bselect\b.*?;)", cand)
    if not m2:
        # noktalı virgül yoksa satır sonuna kadar
        m3 = re.search(r"(?is)\b(with\b.*|\bselect\b.*)", cand)
        if not m3:
            return None
        cand = m3.group(0).strip()
    else:
        cand = m2.group(1).strip()

    # Fazla cümle ayrımı (ilk ;'e kadar)
    if ";" in cand:
        cand = cand.split(";", 1)[0] + ";"
    # Güvenlik için yorumları sil
    cand = sanitize_sql(cand)
    return cand