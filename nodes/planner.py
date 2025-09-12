# nodes/planner.py
import logging
import re
from difflib import get_close_matches  # Küçük yazım hataları için fuzzy eşleşme
from utils.types import AgentState

log = logging.getLogger("planner")

# --- Anahtar kelime sözlüğü -------------------------------------------------
# Not: Türkçe/İngilizce karışık; eşanlamlılar ve sık yazım hataları eklendi.
DB_KEYWORDS = [
    # Sorgu/analitik niyet göstergeleri
    "kaç", "say", "listele", "ortalama", "avg", "sum", "count", "max", "min",
    "group by", "sırala", "sort", "en fazla", "en çok", "hangi", "dağılım", "distribution",
    # Varlık/şema isimleri ve sinonimler
    "unit", "birim",
    "user", "kullanıcı", "kulanıcı",  # 'kullanıcı' yaygın yazım hatası: 'kulanıcı'
    "chat", "session", "oturum", "sohbet",
    "mesaj", "message",
    "age", "yaş",
]

# Selamlama/sohbet/deneme gibi veritabanı dışı içerik sinyalleri
NON_SQL_NOISE = [
    "merhaba", "selam", "hello", "hi", "poem", "şiir", "story", "hikaye",
    "hava", "weather", "şaka", "joke", "lol", "deneme", "test"
]

# Eğitimsel/ansiklopedik sorular (işlevsel SQL üretim niyeti taşımayanlar)
NON_SQL_EDU = [
    "sql injection nedir", "veritabanı nedir", "database nedir",
    "how to install", "what is sql injection", "how does it work"
]

# Çıkış kısayolları (REPL için)
EXIT_TOKENS = {"q", ":q", ":quit", "quit", "exit", ":exit"}


# --- Yardımcı fonksiyonlar ---------------------------------------------------
def _alpha_ratio(s: str) -> float:
    """Metindeki harf oranı: çok düşükse gürültü/noise olabilir."""
    letters = sum(ch.isalpha() for ch in s)
    return letters / max(1, len(s))

def _tokenize(q: str) -> list[str]:
    """Basit tokenizasyon (TR karakterleri dahil)."""
    return re.findall(r"[a-z0-9ğüşöçıİĞÜŞÖÇ]+", q.lower())

def _has_fuzzy_keyword(q: str) -> bool:
    """
    Soru metnindeki token'ları DB_KEYWORDS ile karşılaştır.
    - Birebir içerme kontrolü
    - Küçük yazım hataları için fuzzy eşleşme (difflib)
    """
    toks = _tokenize(q)
    for t in toks:
        # Doğrudan kapsama (ör. 'unit', 'user', 'hangi', 'dağılım' vs.)
        if any(t in kw or kw in t for kw in DB_KEYWORDS):
            return True
        # Fuzzy: benzerlik %80+ ise eşleşmiş say
        if get_close_matches(t, DB_KEYWORDS, n=1, cutoff=0.8):
            return True
    return False

def _looks_like_noise(q: str) -> bool:
    """Çıkış/çok kısa/çok az harf/sohbet/edu türü içerikleri 'noise' say."""
    ql = q.lower().strip()
    if ql in EXIT_TOKENS:
        return True
    if len(ql) < 2:
        return True
    if _alpha_ratio(ql) < 0.3:
        return True
    if any(tok in ql for tok in NON_SQL_NOISE):
        return True
    if any(tok in ql for tok in NON_SQL_EDU):
        return True
    return False

def _mentions_db_semantics(q: str, schema_hint: str | None = None) -> bool:
    """
    Metinde veritabanı/analitik niyetini gösteren sinyaller var mı?
    - Anahtar kelimeler (TR/EN)
    - Fuzzy eşleşme (yazım hataları)
    - Şema ipucu: 'TABLE {ad}' satırlarına referans
    """
    ql = q.lower()
    # 1) Doğrudan anahtar kelime yakalama
    if any(tok in ql for tok in DB_KEYWORDS):
        return True
    # 2) Fuzzy anahtar kelime (kulanıcı → kullanıcı gibi)
    if _has_fuzzy_keyword(ql):
        return True
    # 3) Şema ipucundan tablo adı yakalama (varsa)
    if schema_hint:
        for line in schema_hint.lower().splitlines():
            m = re.search(r"table\s+([a-z0-9_]+)", line)
            if m and m.group(1) in ql:
                return True
    return False


# --- Ana giriş (planner.run) -------------------------------------------------
def run(state: AgentState, rag_enabled_default: bool = True) -> AgentState:
    """
    Soru → intent belirleme + basit plan oluşturma.
    - 'sql_query' veya 'non_sql'
    - RAG kullanım kararı (tetikleyicilerle)
    """
    q = state.question or ""
    ql = q.strip().lower()

    # 0) Önce DB semantiğine bakalım (varsa noise filtresini esneteceğiz)
    has_db_sem = _mentions_db_semantics(ql, state.schema_doc)

    # 1) Çıkış/noise kontrolü (yalnızca DB semantiği YOKSA uygula)
    if not has_db_sem and _looks_like_noise(ql):
        state.intent = "non_sql"
        if ql in EXIT_TOKENS:
            state.answer_text = "Çıkış komutu algılandı."
        else:
            state.answer_text = "Bu soru veritabanıyla ilgili değil."
        state.plan = ["end"]
        state.use_rag = False
        log.info("Intent=non_sql (noise/exit).")
        return state

    # 2) DB semantiği yoksa non_sql
    if not has_db_sem:
        state.intent = "non_sql"
        state.answer_text = "Bu soru veritabanıyla ilgili değil."
        state.plan = ["end"]
        state.use_rag = False
        log.info("Intent=non_sql (no db semantics).")
        return state
    state.use_rag = rag_enabled_default
    state.output_pref = "analyst"  # "table_only" / "bullets_only" vb. de olabilir

    # Kullanıcı “sadece tablo” isterse override et
    if re.search(r"\bsadece tablo\b|\btablo olarak\b|\btable\b", q):
        state.output_pref = "table_only"
    elif re.search(r"\byorumla\b|\banaliz et\b|\byorum\b", q):
        state.output_pref = "analyst"

    # intent tespitin neyse onu koru (sql_query / non_sql)
    state.intent = state.intent or "sql_query"


    # 3) Normal durumda: sql_query
    state.intent = "sql_query"

    # RAG tetikleyicileri: ilişki/arama/doğru kolon seçimi gerektiren ifadeler
    rag_triggers = [
        "hangi", "nerede", "unit", "birim", "kolon", "column", "join",
        "ilişki", "relationship", "dağılım", "distribution"
    ]
    state.use_rag = rag_enabled_default and any(t in ql for t in rag_triggers)
    state.use_rag = rag_enabled_default
    state.output_pref = "analyst"  # "table_only" / "bullets_only" vb. de olabilir


    # 4) Planı yaz
    state.plan = [
        "fetch_schema",
        "use_rag" if state.use_rag else "no_rag",
        "generate_sql",
        "validate_sql",
        "execute_sql",
        "postprocess",
        "summarize",
        "guardian",
        "telemetry",
    ]
    log.info("Intent=sql_query, use_rag=%s, plan=%s", state.use_rag, state.plan)
    return state