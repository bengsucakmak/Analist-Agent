from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field
import uuid, time

def new_trace_id() -> str:
    # Her sorgu için 12 hanelik rastgele trace ID üretir
    return uuid.uuid4().hex[:12]

class AgentState(BaseModel):
    # Telemetri için sorgu kimliği
    trace_id: str = Field(default_factory=new_trace_id)
    # Kullanıcının sorusu (ham metin)
    question: str
    # Planner tarafından belirlenen intent
    intent: Literal["sql_query","non_sql"] = "sql_query"
    # Çalışma planı (node sıraları vs.)
    plan: List[str] = []
    # Planner kararı: RAG kullanılacak mı?
    use_rag: bool = False
    # Şema dökümanı (schema_retriever tarafından doldurulur)
    schema_doc: Optional[str] = None
    # RAG ipuçları/snippet listesi
    rag_snippets: List[str] = []
    # Query Generator tarafından üretilen SQL adayları
    candidate_sql: List[str] = []
    # Doğrulanmış SQL (validator sonrası)
    validated_sql: Optional[str] = None
    # Doğrulama raporu (ok/reason)
    validation_report: Optional[Dict[str, Any]] = None
    # Executor raporu (ok/ms/rowcount veya hata)
    execution_stats: Optional[Dict[str, Any]] = None
    # Executor’dan dönen önizleme satırları
    rows_preview: Optional[List[Dict[str, Any]]] = None
    # Summarizer’ın nihai cevabı
    answer_text: Optional[str] = None
    # Token maliyet istatistikleri
    cost: Dict[str, float] = Field(default_factory=lambda: {"input_tokens":0,"output_tokens":0,"usd":0})
    # Sorgunun başlama zamanı (telemetri için)
    t0: float = Field(default_factory=time.time)
    # Validator ↔ QGen döngüsünde onarım sayacı
    repair_attempts: int = 0
    # Dil tercihi (summarizer için: "en"/"tr")
    language: Optional[str] = None
    output_pref: Optional[Literal['analyst', 'table_only', 'bullets_only', 'one_liner']] = None