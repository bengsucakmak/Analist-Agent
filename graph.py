from langgraph.graph import StateGraph, START, END
# from utils import llm  # (Kullanılmıyor; istersen tekrar aç)
from utils.types import AgentState
from utils.cost import CostTracker

from nodes import (
    planner,
    schema_retriever,
    query_generator,
    query_validator,
    sql_executor,
    postprocessor,
    summarizer,
    guardian,
)

def build_graph(conn, cfg, cost: CostTracker, llm_service):
    # LangGraph grafını AgentState durum tipi ile başlat
    g = StateGraph(AgentState)

    # --- Nodes (düğümler) ---
    # planner: intent sınıflandırma + varsayılan RAG kullanım bayrağı
    g.add_node("planner", lambda s: planner.run(s, rag_enabled_default=cfg["rag"]["enabled"]))

    # schema: DB şemasını/metadata'yı çekip state'e yazar (örn. s.schema_doc)
    g.add_node("schema", lambda s: schema_retriever.run(conn, s))

    # RAG node (basit, schema_doc'tan in-memory indeks kurar)
    from tools.rag import SimpleRAG
    rag_engine = None  # lazy init: ilk ihtiyaçta kurulur

    def rag_node(s: AgentState):
        nonlocal rag_engine
        # Kullanıcı/Planner RAG'i kapattıysa direkt geç
        if not s.use_rag:
            return s
        # İlk kez ihtiyaç varsa ve schema_doc hazırsa belge listesi kur
        if rag_engine is None and getattr(s, "schema_doc", None):
            docs = s.schema_doc.splitlines()
            rag_engine = SimpleRAG(docs)
        # RAG motoru varsa query çalıştır
        if rag_engine:
            res = rag_engine.query(
                s.question,
                top_k=cfg["rag"]["top_k"],
                min_score=cfg["rag"]["min_score"],
            )
            # Sadece metin snippet'lerini state'e yaz
            s.rag_snippets = [d for _, d in res]
        return s

    g.add_node("rag", rag_node)

    # qgen: LLM ile yalnızca SELECT odaklı SQL üretimi
    g.add_node(
        "qgen",
        lambda s: query_generator.run(
            s,
            cost,
            llm_service,
            max_limit=cfg["security"]["max_limit"],
        ),
    )

    # İzinli tablo listesi (typo kontrol et: "message_into" doğru mu?)
    ALLOWED_TABLES = [
        "user",
        "unit",
        "chat_session",
        "message_into",      # <-- muhtemelen "message_info" olmalı; şemanı kontrol et
        "llm_providers",
        "use_llm_service",
    ]

    g.add_node(
        "qval",
        lambda s: query_validator.run(
            conn,
            s,
            banned_keywords=cfg["security"]["banned_keywords"],
            enforce_select_only=True,
            allow_multiple=False,
            max_limit=cfg["security"]["max_limit"],
            llm_service=llm_service,
            cost=cost,
            allowed_tables=ALLOWED_TABLES,
        ),
    )

    # exec: güvenli yürütme (parametreli, timeout/progress)
    g.add_node("exec", lambda s: sql_executor.run(conn, s))
    # post: tip/format/locale düzeltmeleri
    g.add_node("post", lambda s: postprocessor.run(s, conn))
    # sum: nihai kısa analist özeti + opsiyonel SQL
    g.add_node("sum", lambda s: summarizer.run(s, cost, cfg["runtime"]["show_sql_in_answer"], llm_service))
    # guard: nihai güvenlik/PII/satır sayısı vb. kontrol
    g.add_node("guard", lambda s: guardian.run(s))
    # telemetry: burada sadece state'i ileri taşır (telemetry sink dışarıda)
    g.add_node("telemetry", lambda s: s)

    # --- Edges (kenarlar/akış) ---
    g.add_edge(START, "planner")

    # planner sonrası intent kontrolü: non_sql ise akışı sonlandır
    def after_planner(s: AgentState) -> str:
        if s.intent == "non_sql":
            if not getattr(s, "answer_text", None):
                s.answer_text = "Bu soru veritabanıyla ilgili değil."
            return "end"  # END'e koşullu dal
        return "schema"   # sql_query → schema

    # Koşullu kenarlar: "end" sembolik dalını gerçek END'e map et
    g.add_conditional_edges("planner", after_planner, {"schema": "schema", "end": END})

    # Şema → RAG → QGen → QVal hattı
    g.add_edge("schema", "rag")
    g.add_edge("rag", "qgen")
    g.add_edge("qgen", "qval")

    # qval sonrası: geçer/onar/özet
    def is_valid(s: AgentState) -> str:
        vr = s.validation_report or {}
        if vr.get("ok"):
            return "exec"  # doğrulama geçti → çalıştır
        # geçmediyse onarım denemesi sayacını artır
        s.repair_attempts = getattr(s, "repair_attempts", 0) + 1
        if s.repair_attempts > cfg["runtime"]["max_repairs"]:
            # onarım sınırı aşıldıysa kısa açıklama ile özet düğümüne git
            s.answer_text = (
                f"SQL doğrulaması başarısız oldu: {vr.get('reason', 'bilinmeyen')} "
                f"(onarım denemeleri aşıldı)."
            )
            return "sum"
        return "qgen"  # tekrar üret (onarım döngüsü)

    g.add_conditional_edges("qval", is_valid, {"exec": "exec", "qgen": "qgen", "sum": "sum"})

    # Yürütme sonrası ardışık akış
    g.add_edge("exec", "post")
    g.add_edge("post", "sum")
    g.add_edge("sum", "guard")
    g.add_edge("guard", "telemetry")
    g.add_edge("telemetry", END)

    # Derlenmiş (invoke edilebilir) grafı döndür
    return g.compile()
