# main.py — Uygulama giriş noktası: CLI/REPL, konfig yükleme, LLM/DB başlatma, graf çalıştırma
import argparse, yaml, logging, sys, time
from utils.logging import setup_logging          # JSON log/format kurulumunu yapan yardımcı
from utils.types import AgentState               # Grafın durum/State tipini taşıyan sınıf (pydantic/dataclass)
from utils.cost import CostTracker               # LLM token maliyetlerini ölçen sayaç
from tools.db import connect_readonly            # SQLite'a read-only ve timeout/progress ile bağlan
from graph import build_graph                    # LangGraph düğümlerini bağlayan derleyici
from utils.llm import LLMService                 # OpenAI-compatible LLM istemcisi

# Kullanıcıya REPL modunda görünen kısa yardım/komutlar
BANNER = """
Analist AI Ajanı — SQLite + Planner + RAG + LLM
Komutlar:
  :q, :quit, :exit   -> çıkış
  :sql               -> özetlerde SQL göster/gizle toggle
  :rag               -> RAG açık/kapalı toggle (sadece bu oturum için)
"""

def run_once(question: str, cfg, conn, llm, show_sql_override=None, rag_override=None):
    """
    Tek bir kullanıcı sorusunu uçtan uca işler:
      - CostTracker başlatır (token/maliyet ölçümü)
      - Opsiyonel oturumluk override'ları uygular (SQL gösterimi, RAG)
      - Başlangıç AgentState oluşturur
      - Grafı build_graph ile derler ve invoke eder
      - Toplam süre ve maliyeti loglar; cevabı stdout'a yazar
      - Geçici override'ları eski haline çevirir
    """
    # Her soru çağrısında yeni bir maliyet sayacı (input/output token ve $) başlat
    cost = CostTracker(cfg["llm"]["price_per_1k_input"], cfg["llm"]["price_per_1k_output"])

    # --- Oturuma özel geçici override'lar: config'i RAM'de anlık değiştir ---
    if show_sql_override is not None:
        cfg_rt_show = cfg["runtime"]["show_sql_in_answer"]  # eski değeri sakla (geri almak için)
        cfg["runtime"]["show_sql_in_answer"] = show_sql_override
    if rag_override is not None:
        cfg_rag = cfg["rag"]["enabled"]                     # eski değeri sakla (geri almak için)
        cfg["rag"]["enabled"] = rag_override

    # Başlangıç durumunu yalnızca kullanıcı sorusuyla oluştur (diğer alanlar düğümlerce doldurulur)
    state = AgentState(question=question)
    # Grafı DB bağlantısı, config, cost tracker ve LLM servisi ile derle (bağımlılık enjeksiyonu)
    graph = build_graph(conn, cfg, cost, llm)

    # --- Çalıştır ve süreyi ölç ---
    t0 = time.time()
    out = graph.invoke(
        state,
        config={"recursion_limit": cfg["runtime"].get("recursion_limit", 50)}  # LangGraph derinlik koruması (sonsuz döngüleri engeller)
    )
    # Çıktıyı Type/State'e dök (tip/doğrulama için; eksik/yanlış alanlar erken yakalanır)
    final_state = AgentState(**out)
    dt = time.time() - t0

    # Maliyet özetini al ve bilgi logu bas (logger adı: analist_agent)
    final_cost = cost.to_dict()
    logging.getLogger("analist_agent").info(
        "Süre=%.1f ms | Tokens in=%d out=%d | Cost=%s %s",
        dt*1000, final_cost["input_tokens"], final_cost["output_tokens"],
        final_cost["usd"], cfg["llm"]["currency"]
    )

    # Kullanıcıya nihai yanıtı yazdır (özet/metin; tablo çıktısı varsa üst katman yazdırır)
    print("\n================= CEVAP =================")
    print(final_state.answer_text or "(cevap yok)")
    print("=========================================\n")

    # --- Override'ları geri al: config'i eski haline döndür ---
    if show_sql_override is not None:
        cfg["runtime"]["show_sql_in_answer"] = cfg_rt_show
    if rag_override is not None:
        cfg["rag"]["enabled"] = cfg_rag

def main():
    """CLI akışı: argümanları al, log+config yükle, LLM ve DB başlat, tek seferlik veya REPL çalıştır."""
    # Basit CLI: config yolu ve tek seferlik soru opsiyonu
    parser = argparse.ArgumentParser(description="Analist AI Ajanı (Interactive)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--question", "-q", help="Tek seferlik soru (REPL yerine)")
    args = parser.parse_args()

    # Log altyapısını kur ve başlangıç logu at (JSON format, seviyeler, handler'lar)
    logger = setup_logging()
    logger.info("Uygulama başlıyor…")

    # YAML config'i yükle (safe_load: güvenli YAML parse)
    with open(args.config, "r") as f:
        cfg = yaml.safe_load(f)

    # LLM servisini OpenAI-compatible parametrelerle hazırla (vLLM/Ollama/LM Studio gibi)
    llm = LLMService(
        model_name=cfg["llm"]["model_name"],
        max_tokens=cfg["llm"]["max_tokens"],
        temperature=cfg["llm"]["temperature"],
        base_url=cfg["llm"]["base_url"],
        api_key=cfg["llm"]["api_key"],
    )

    # SQLite'a read-only, timeout ve progress handler limitiyle bağlan
    try:
        conn = connect_readonly(
            cfg["db"]["path"],
            timeout_ms=cfg["db"]["timeout_ms"],
            max_instructions=cfg["db"]["max_instructions"]
        )
    except Exception as e:
        # Bağlantı hatası olursa exception logla ve süreçten çık
        logger.exception("DB bağlantı hatası: %s", e)
        sys.exit(1)

    # --- Tek seferlik mod: -q verildiyse REPL açmadan çalıştır ve çık ---
    if args.question:
        run_once(args.question, cfg, conn, llm)
        return

    # --- REPL modu: kullanıcıdan sürekli soru al ---
    print(BANNER)
    show_sql_override = None   # None → config'teki default kullan (False/True)
    rag_override = None

    while True:
        try:
            q = input("Soru > ").strip()  # Kullanıcı girdisi (boşsa devam)
        except (EOFError, KeyboardInterrupt):
            print("\nÇıkılıyor…")
            break

        if not q:
            continue
        if q in (":q", ":quit", ":exit"):
            print("Çıkılıyor…")
            break
        if q == ":sql":
            # SQL gösterimini oturumluk toggle et (config'i değiştirmeden RAM'de)
            show_sql_override = (not (show_sql_override if show_sql_override is not None else cfg["runtime"]["show_sql_in_answer"]))
            print(f"[i] SQL gösterimi: {'AÇIK' if show_sql_override else 'KAPALI'}")
            continue
        if q == ":rag":
            # RAG kullanımını oturumluk toggle et (config'e dokunmadan)
            rag_override = (not (rag_override if rag_override is not None else cfg["rag"]["enabled"]))
            print(f"[i] RAG: {'AÇIK' if rag_override else 'KAPALI'}")
            continue

        # Soru çalıştır ve hataları hem logla hem kullanıcıya kısa mesajla göster
        try:
            run_once(q, cfg, conn, llm, show_sql_override=show_sql_override, rag_override=rag_override)
        except Exception as e:
            logger.exception("Çalışma sırasında hata: %s", e)
            print(f"[HATA] {e}\n(Lütfen logs/run.log dosyasına bakın.)")

if __name__ == "__main__":
    main()  # Modül doğrudan çalıştırıldığında CLI girişini başlat