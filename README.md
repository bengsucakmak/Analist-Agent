# 🤖 Analist AI Ajanı

SQLite / PostgreSQL üzerinde **doğal dilden analist sorularını** alıp:
- Güvenli SQL üretir (yalnızca `SELECT`).
- SQL'i çalıştırır (READ-ONLY).
- Tablo önizlemesi + analist tarzında özet çıkarır.
- İstenirse kullanılan SQL’i de gösterir.
- Çok-ajanlı mimari: **Planner → Schema → RAG → QueryGen → Validator → Executor → Postprocessor → Summarizer → Guardian → Telemetry**

---

## 🚀 Özellikler
- **Doğal dil (TR/EN)** → SQL → Tablo + Özet
- **Guardrails**: sadece SELECT, yasak komut filtresi, LIMIT, timeout
- **RAG destekli**: tablo/kolon bilgisini daha doğru SQL için kullanır
- **Çok ajanlı yapı** (LangGraph)
- **Maliyet ölçümü** (token bazlı tahmin)
- **Arayüzler**:
  - Terminal REPL (`main.py`)
  - Streamlit Web UI (`ui_streamlit.py`)

---

## 📦 Kurulum
```bash
git clone <repo-url> analist-agent
cd analist-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

▶️ Kullanım
Terminal REPL
PYTHONPATH=. python main.py


Komutlar:

:q, :quit, :exit → çıkış

:sql → SQL göster/gizle

:rag → RAG aç/kapat

Streamlit Web UI
pip install streamlit pandas
streamlit run ui_streamlit.py


→ Tarayıcıda http://localhost:8501
🧪 Değerlendirme (Eval)

Hazır eval seti ve koşucu var:

python eval_runner.py --lang tr
python eval_runner.py --lang en


Sonuçlar eval/results.jsonl içine kaydedilir.

📂 Proje Yapısı
analist-agent/
├─ main.py               # Terminal REPL
├─ ui_streamlit.py       # Web arayüzü
├─ graph.py              # LangGraph pipeline
├─ nodes/                # Ajan nodeları
├─ utils/                # Yardımcılar (LLM, logging, cost, state)
├─ tools/                # RAG, DB helper
├─ eval/                 # Eval seti
└─ config.yaml           # Konfigürasyon

🧩 Ajanlar ve Mimari

Sistem çok-ajanlı çalışır. Her ajan AgentState nesnesini alır, işler, çıktıyı bir sonrakine aktarır.

Planner
Kullanıcı sorusunun niyetini belirler (sql_query / non_sql). Çıkış/gürültü sorularını hemen sonlandırır. Gerekirse RAG kullanımını tetikler.

Schema Retriever
Veritabanı tablolarını/kolonlarını çıkarır, şema dökümanı hazırlar.

RAG (opsiyonel)
Şema dökümanından ilgili parçaları seçer, SQL üretimine bağlam sağlar.

Query Generator
LLM kullanarak tek bir SELECT SQL üretir.

Query Validator
SQL’in güvenliğini ve geçerliliğini kontrol eder.

Yasaklı komut var mı?

Tek statement mi?

LIMIT var mı? Yoksa ekle.

EXPLAIN/PLAN başarılı mı?
Başarısızsa onarım döngüsü ile SQL yeniden üretilir (max n deneme).

SQL Executor
Sorguyu READ-ONLY çalıştırır, küçük bir önizleme (rows_preview) döner.

Postprocessor
Tipleri normalize eder, tablo halinde gösterime hazır hale getirir.

Summarizer
Tablo/istatistiklerden kısa bir analist anlatımı çıkarır. (TR/EN desteklenebilir).
İsteğe bağlı SQL de eklenir.

Guardian
Sonuçtaki PII veya kara liste kolonları kontrol eder, gerekirse maskeler.

Telemetry
Maliyet ve süreyi hesaplar, loglama yapar.

🔒 Guardrails (Özet)

Yalnızca SELECT

Yasak komutlar engelli (DROP, ALTER, DELETE, INSERT, ...)

LIMIT enjekte edilir (max_limit)

Timeout / instruction limiti

Onarım döngüsü: hatalı SQL → yeniden üretim (max 3)

Opsiyonel: PII kolon maskeleme

📊 Mimari Akış
START
 → Planner
   ├─ non_sql → END
   └─ sql_query → Schema → [RAG?] → QueryGen → Validator
         ├─ OK → Executor → Post → Summarizer → Guardian → Telemetry → END
         └─ FAIL → [onarım döngüsü] → QueryGen

⚠️ Notlar

Şu an DB: SQLite. PostgreSQL için tools/db.connect_readonly değiştirilebilir.

LLM API: OpenAI-compatible endpoint gerekir (base_url, api_key, model_name).

Streamlit açılışta email sorarsa boş bırakıp enter’a basabilirsin.

