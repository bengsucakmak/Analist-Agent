import logging
import sqlite3
from utils.types import AgentState

log = logging.getLogger("schema")

# Manuel kolon açıklamaları sözlüğü (LLM'e ipucu için)
COLUMN_DESCRIPTIONS = {
    "chat_session.num_of_mess": "number of messages in this chat session",
    "chat_session.message_date": "timestamp (datetime) of the chat session",
    "user.age": "age of the user in years",
    "user.unit_id": "foreign key → unit table",
    "unit.unit_name": "name of the organizational unit",
}

def run(conn: sqlite3.Connection, state: AgentState) -> AgentState:
    # DB bağlantısından cursor aç
    cur = conn.cursor()

    # 1) Tabloları listele (sqlite_master: tablo metadata)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]  # dahili tabloları atla

    schema_lines = []
    for t in tables:
        schema_lines.append(f"TABLE {t} (")
        # 2) Her tablo için kolon bilgisi al (PRAGMA table_info)
        cur.execute(f"PRAGMA table_info({t})")
        cols = cur.fetchall()
        for cid, name, ctype, notnull, dflt, pk in cols:
            # Kolon adı + tipi
            schema_lines.append(f"    {name} {ctype}")
        schema_lines.append(")")

    # 3) Sözlük ekle: manuel açıklamalar
    schema_lines.append("\nCOLUMN DICTIONARY:")
    for col, desc in COLUMN_DESCRIPTIONS.items():
        schema_lines.append(f"- {col} → {desc}")

    # 4) Şemayı tek string olarak birleştir
    schema_doc = "\n".join(schema_lines)
    state.schema_doc = schema_doc

    log.info("Şema dökümanı hazır (%d karakter).", len(schema_doc))
    return state