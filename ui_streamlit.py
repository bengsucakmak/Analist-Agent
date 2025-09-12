import time
import yaml
import pandas as pd
import streamlit as st

from graph import build_graph
from utils.types import AgentState
from utils.cost import CostTracker
from utils.llm import LLMService
from tools.db import connect_readonly

# ─────────────────────────────────────
# SAYFA AYARLARI
# ─────────────────────────────────────
st.set_page_config(
    page_title="Analist AI Ajanı",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────
# TEMA & STİL (Modern / Glass / Dark uyumlu)
# ─────────────────────────────────────
CUSTOM_CSS = """
<style>
:root {
  --bg: #0b1220;
  --card: rgba(255,255,255,0.06);
  --border: rgba(255,255,255,0.12);
  --text: #e9eef7;
  --muted: #a3adc2;
  --accent: #7c9cff;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f8fb;
    --card: rgba(255,255,255,0.8);
    --border: rgba(0,0,0,0.08);
    --text: #0b1220;
    --muted: #495266;
    --accent: #3f5efb;
  }
}

.main .block-container { max-width: 1200px; }
html, body, [data-testid="stMarkdown"] p { color: var(--text); }

/* Başlık banner */
.hero {
  margin-bottom: 16px;
  padding: 18px 22px;
  border-radius: 18px;
  background: linear-gradient(135deg, var(--accent) 0%, var(--bg) 100%);
  color: white;
}
.hero h1 { margin: 0; font-size: 32px; font-weight: 700; }
.hero p { margin: 4px 0 0 0; font-size: 15px; opacity: 0.85; }

/* Sohbet baloncukları */
.chat-wrap { display:flex; flex-direction:column; gap:14px; }
.msg {
  max-width: 80%; padding: 14px 14px; border-radius: 16px; line-height: 1.45;
  border: 1px solid var(--border);
}
.msg.user { align-self:flex-end; background: linear-gradient(180deg, rgba(124,156,255,.1), rgba(124,156,255,.05)); }
.msg.assistant { align-self:flex-start; background: var(--card); }
.msg .meta { margin-top:8px; font-size: 12px; color: var(--muted); }

/* SQL kutusu */
.sqlbox { background: rgba(2,6,23,.5); border:1px dashed var(--border); border-radius: 12px; padding: 10px; }
.sqlbox code { font-size: 12.5px; }

/* ZAMAN ÇİZELGESİ */
.tl { margin: 8px 0 10px 0; padding-left: 6px; border-left: 2px solid var(--border); }
.tl-item { position: relative; padding: 6px 8px 6px 14px; color: var(--muted); font-size: 13px; }
.tl-item::before { content: ""; position: absolute; left: -7px; top: 10px; width: 10px; height: 10px; border-radius: 50%; background: var(--border); }
.tl-item.running { color: var(--accent); }
.tl-item.running::before { background: var(--accent); box-shadow: 0 0 0 6px rgba(124,156,255,.15); }
.tl-item.done { color: #10b981; }
.tl-item.done::before { background: #10b981; }

/* Rozet */
.badge { display:inline-flex; align-items:center; gap:6px; font-size: 12px; padding: 4px 8px; border-radius: 999px;
  border:1px solid var(--border); color: var(--muted); }
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ─────────────────────────────────────
# YARDIMCILAR
# ─────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_config(path="config.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)

class StepStatus:
    def __init__(self, min_run=0.45, done_hold=0.45):
        self.min_run = float(min_run)
        self.done_hold = float(done_hold)
    def wait_min(self, start_ts):
        elapsed = time.time() - start_ts
        if elapsed < self.min_run:
            time.sleep(self.min_run - elapsed)

STEP_LABELS = {
    "planner":   ("🤔", "Analiz"),
    "schema":    ("📚", "Şema"),
    "rag":       ("🔎", "RAG"),
    "qgen":      ("🧮", "SQL"),
    "qval":      ("🛡️", "Doğrulama"),
    "exec":      ("▶️", "Çalıştırma"),
    "post":      ("🧩", "Biçim"),
    "sum":       ("📝", "Özet"),
    "guard":     ("🚦", "Güvenlik"),
    "telemetry": ("📈", "Kayıt"),
}

def timeline_html(running: str | None, done: set[str]):
    items = []
    for key, (icon, label) in STEP_LABELS.items():
        cls = "tl-item"
        if key in done:
            cls += " done"
        elif key == running:
            cls += " running"
        items.append(f"<div class='{cls}'> {icon} {label}</div>")
    return "<div class='tl'>" + "".join(items) + "</div>"

# ─────────────────────────────────────
# BAŞLIK
# ─────────────────────────────────────
st.markdown("""
<div class="hero">
  <h1>🤖 Analist AI Ajanı</h1>
  <p>Doğal dilden güvenli SQL sorgularına </p>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────
# OTURUM
# ─────────────────────────────────────
cfg = load_config()
if "conn" not in st.session_state:
    st.session_state.conn = connect_readonly(
        cfg["db"]["path"],
        timeout_ms=cfg["db"]["timeout_ms"],
        max_instructions=cfg["db"]["max_instructions"],
    )
if "llm" not in st.session_state:
    st.session_state.llm = LLMService(
        model_name=cfg["llm"]["model_name"],
        max_tokens=cfg["llm"]["max_tokens"],
        temperature=cfg["llm"]["temperature"],
        base_url=cfg["llm"]["base_url"],
        api_key=cfg["llm"]["api_key"],
        timeout=cfg["llm"].get("timeout", 30),
    )
if "messages" not in st.session_state:
    st.session_state.messages = []

# ─────────────────────────────────────
# GEÇMİŞ
# ─────────────────────────────────────
st.markdown("<div class='chat-wrap'>", unsafe_allow_html=True)
for m in st.session_state.messages:
    role = m.get("role", "assistant")
    cls = "assistant" if role != "user" else "user"
    st.markdown(f"<div class='msg {cls}'>" + m.get("content", "") + "</div>", unsafe_allow_html=True)
st.markdown("</div>", unsafe_allow_html=True)

# ─────────────────────────────────────
# INPUT
# ─────────────────────────────────────
user_prompt = st.chat_input("Bir soru yazın…")

if user_prompt:
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    with st.chat_message("user"):
        st.markdown(f"<div class='msg user'>{user_prompt}</div>", unsafe_allow_html=True)

    with st.chat_message("assistant"):
        tl_ph = st.empty()
        ans_ph = st.empty()
        meta_ph = st.empty()

        cost = CostTracker(cfg["llm"]["price_per_1k_input"], cfg["llm"]["price_per_1k_output"])
        state = AgentState(question=user_prompt)
        graph = build_graph(st.session_state.conn, cfg, cost, st.session_state.llm)

        done_steps: set[str] = set()
        running_step: str | None = None
        stepper = StepStatus()
        t0 = time.time()

        tl_ph.markdown(timeline_html(running_step, done_steps), unsafe_allow_html=True)

        final_state_dict = None
        try:
            for event in graph.stream(state, config={"recursion_limit": cfg["runtime"].get("recursion_limit", 100)}):
                step = list(event.keys())[0]
                running_step = step
                start_ts = time.time()
                tl_ph.markdown(timeline_html(running_step, done_steps), unsafe_allow_html=True)

                final_state_dict = event[step]
                stepper.wait_min(start_ts)

                done_steps.add(step)
                running_step = None
                tl_ph.markdown(timeline_html(running_step, done_steps), unsafe_allow_html=True)
                time.sleep(stepper.done_hold)

            fs = AgentState(**final_state_dict) if isinstance(final_state_dict, dict) else final_state_dict
            answer_text = getattr(fs, "answer_text", None) or "(cevap oluşturulamadı)"
            elapsed = time.time() - t0
            meta = f"⏱ {elapsed:.2f} s  ·  💲 ≈ {getattr(cost, 'usd', lambda: 0.0)():.4f} USD" if callable(getattr(cost, 'usd', None)) else f"⏱ {elapsed:.2f} s"

            tl_ph.markdown("", unsafe_allow_html=True)
            meta_ph.markdown(f"<div class='badge'>✅ Tamamlandı · {meta}</div>", unsafe_allow_html=True)

            buf = ""
            for w in answer_text.split():
                buf += w + " "
                ans_ph.markdown(f"<div class='msg assistant'>{buf}</div>", unsafe_allow_html=True)
                time.sleep(0.02)

            st.session_state.messages.append({"role": "assistant", "content": answer_text})

            validated_sql = getattr(fs, "validated_sql", None)
            rows_preview = getattr(fs, "rows_preview", None)
            if validated_sql or rows_preview:
                with st.expander("🔍 Detaylar / SQL / Tablo", expanded=False):
                    if validated_sql:
                        st.markdown("**Kullanılan SQL**")
                        st.markdown("<div class='sqlbox'>", unsafe_allow_html=True)
                        st.code(validated_sql, language="sql")
                        st.markdown("</div>", unsafe_allow_html=True)
                    if rows_preview:
                        st.markdown("**Veri Önizleme**")
                        st.dataframe(pd.DataFrame(rows_preview), use_container_width=True, hide_index=True)

        except Exception as e:
            st.error(f"Çalışma sırasında hata: {e}")