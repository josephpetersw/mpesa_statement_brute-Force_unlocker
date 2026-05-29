import streamlit as st
import plotly.graph_objects as go
import io
import csv
import time
import threading
import os
from datetime import datetime
from pdf_handler import detect_encryption, get_encryption_info, validate_password, generate_unlocked_pdf, run_brute_force, brute_force_generator
from extractor import extract_text_from_pdf, extract_tables_from_pdf, ocr_fallback_extract, get_ocr_status
from parser import extract_metadata, parse_summary_totals, parse_transactions
from analyzer import analyze_statement


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap CDN helper — injected once
# ─────────────────────────────────────────────────────────────────────────────
BOOTSTRAP_CDN = """
<link rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
  integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH"
  crossorigin="anonymous">
"""


def clean_html(html: str) -> str:
    """Strip leading/trailing whitespace from each line of HTML to prevent Streamlit from rendering indented HTML as a markdown code block."""
    return "\n".join(line.strip() for line in html.strip().splitlines())


def bt(html: str) -> None:
    """Render raw HTML (unsafe) – thin wrapper for readability."""
    st.markdown(clean_html(html), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Brute-force session management
# ─────────────────────────────────────────────────────────────────────────────

def start_brute_force_session(pdf_bytes, candidates, strategy_name, enc_info, max_workers: int = 20):
    num_batches = max(1, min(max_workers, 50))  # clamp 1–50
    state = {
        "active": True,
        "strategy_name": strategy_name,
        "total_candidates": len(candidates),
        "max_workers": num_batches,
        "batch_progress": {i: {"tested": 0, "total": 0, "last_pwd": "", "status": "Pending"} for i in range(num_batches)},
        "logs_list": [f"[{datetime.now().strftime('%H:%M:%S')}] [System] Starting {num_batches} concurrent workers…"],
        "stop_event": threading.Event(),
        "pause_event": threading.Event(),
        "paused": False,
        "found_password": None,
        "finished": False,
        "start_time": time.time(),
        "elapsed_paused": 0.0,
        "pause_start": 0.0,
    }
    state["pause_event"].set()
    st.session_state.brute_force_state = state

    lock = threading.Lock()

    def callback(batch_idx, tested, total=None, last_pwd=None):
        if total is None:
            actual_tested, actual_total, actual_batch_idx, actual_last_pwd = batch_idx, tested, 0, ""
        else:
            actual_tested, actual_total, actual_batch_idx, actual_last_pwd = tested, total, batch_idx, last_pwd
        with lock:
            bp = state["batch_progress"]
            if actual_batch_idx in bp:
                bp[actual_batch_idx]["tested"] = actual_tested
                bp[actual_batch_idx]["total"] = actual_total
                bp[actual_batch_idx]["last_pwd"] = actual_last_pwd
                bp[actual_batch_idx]["status"] = "Running" if actual_tested < actual_total else "Done"
            pct = (actual_tested / actual_total * 100) if actual_total > 0 else 0.0
            t_str = datetime.now().strftime("%H:%M:%S")
            state["logs_list"].append(
                f"[{t_str}] Worker {actual_batch_idx+1}: {actual_tested:,}/{actual_total:,} ({pct:.1f}%) | tried: {actual_last_pwd}"
            )
            if len(state["logs_list"]) > 200:
                state["logs_list"].pop(0)

    def run_wrapper():
        try:
            pwd = run_brute_force(
                pdf_bytes, candidates,
                enc_info=enc_info,
                max_workers=num_batches,
                progress_callback=callback,
                stop_event=state["stop_event"],
                pause_event=state["pause_event"],
            )
            state["found_password"] = pwd
            t_str = datetime.now().strftime("%H:%M:%S")
            if pwd:
                state["logs_list"].append(f"[{t_str}] ✅ SUCCESS — Password: {pwd}")
            else:
                msg = "ABORTED by user." if state["stop_event"].is_set() else "FINISHED — not found."
                state["logs_list"].append(f"[{t_str}] {msg}")
        except Exception as e:
            state["logs_list"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [Error] {e}")
        finally:
            state["finished"] = True

    t = threading.Thread(target=run_wrapper, name="BruteForceWrapper", daemon=True)
    t.start()
    st.rerun()


def _worker_cards_html(batch_progress: dict, num_workers: int, overall_speed: float) -> str:
    """
    Build a responsive CSS-grid of per-worker stat cards.
    Each card shows: worker index, status badge, mini progress bar,
    candidates tested / total, % complete, and last-tried password.
    """
    status_colors = {
        "Running":   ("#1b5e20", "#43a047", "▶"),
        "Done":      ("#0d47a1", "#1e88e5", "✔"),
        "Pending":   ("#37474f", "#607d8b", "…"),
    }

    cards = ""
    for i in range(num_workers):
        bp        = batch_progress.get(i, {"tested": 0, "total": 0, "last_pwd": "", "status": "Pending"})
        tested    = bp["tested"]
        total     = bp["total"]
        last_pwd  = str(bp.get("last_pwd", ""))[:30] or "—"
        status    = bp.get("status", "Pending")
        pct_w     = (tested / total * 100) if total > 0 else 0.0
        bg, bar_col, icon = status_colors.get(status, status_colors["Pending"])

        # Per-worker speed estimate (rough: assume uniform share of overall speed)
        w_speed = f"{overall_speed / max(num_workers, 1):,.0f}/s" if overall_speed > 0 else "—"

        cards += f"""
        <div style="
            background:{bg};
            border:1px solid {bar_col};
            border-radius:10px;
            padding:10px 13px;
            min-width:0;
            overflow:hidden;
        ">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">
                <span style="color:#eceff1;font-weight:700;font-size:0.82rem;letter-spacing:.5px;">
                    {icon} Worker&nbsp;{i+1}
                </span>
                <span style="
                    background:{bar_col};color:#fff;
                    border-radius:4px;padding:1px 7px;
                    font-size:0.68rem;font-weight:700;letter-spacing:.5px;
                ">{status.upper()}</span>
            </div>

            <!-- mini progress bar -->
            <div style="background:#1c2b1c;border-radius:3px;height:5px;margin-bottom:7px;">
                <div style="background:{bar_col};width:{pct_w:.1f}%;height:5px;border-radius:3px;
                            transition:width .2s ease;"></div>
            </div>

            <div style="font-size:0.75rem;color:#b0bec5;line-height:1.6;">
                <span style="color:#fff;font-weight:600;">{pct_w:.1f}%</span>
                &nbsp;·&nbsp;{tested:,}&nbsp;/&nbsp;{total:,}
                &nbsp;·&nbsp;~{w_speed}
            </div>
            <div style="
                font-size:0.68rem;color:#78909c;
                white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
                margin-top:3px;" title="{last_pwd}">
                🔑 {last_pwd}
            </div>
        </div>"""

    # Responsive auto-fill grid
    cols = min(num_workers, 5)   # max 5 columns
    return clean_html(f"""
    <div style="
        display:grid;
        grid-template-columns:repeat(auto-fill,minmax(min(100%/{cols},160px),1fr));
        gap:8px;
        margin:10px 0 16px;
    ">{cards}</div>""")


def run_progress_ui_loop(pdf_bytes):
    state = st.session_state.brute_force_state

    st.markdown(f"### 🛠️ Running **{state['strategy_name']}** Brute-Force  ·  {state['max_workers']} concurrent workers")

    col_stop, col_pause = st.columns(2)
    with col_stop:
        if st.button("🛑 Stop", key="stop_btn", type="primary"):
            state["stop_event"].set()
            state["finished"] = True
            state["active"] = False
            st.rerun()
    with col_pause:
        paused = state["paused"]
        lbl = "▶️ Resume" if paused else "⏸️ Pause"
        if st.button(lbl, key="pause_btn"):
            state["paused"] = not paused
            if not paused:
                state["pause_event"].clear()
                state["pause_start"] = time.time()
                state["logs_list"].append(f"[{datetime.now().strftime('%H:%M:%S')}] ⏸ PAUSED")
            else:
                state["pause_event"].set()
                if state["pause_start"] > 0:
                    state["elapsed_paused"] += time.time() - state["pause_start"]
                state["logs_list"].append(f"[{datetime.now().strftime('%H:%M:%S')}] ▶ RESUMED")
            st.rerun()

    # ── Overall progress bar + status ────────────────────────────────────────
    progress_bar = st.progress(0.0)
    status_text  = st.empty()

    # ── Worker grid header + placeholder ─────────────────────────────────────
    num_workers = state["max_workers"]
    st.markdown(
        f"<div style='font-size:0.8rem;color:#78909c;margin:8px 0 4px;letter-spacing:.6px;font-weight:700;'>"
        f"🔀 SPAWNED WORKERS ({num_workers})</div>",
        unsafe_allow_html=True,
    )
    worker_grid = st.empty()

    # ── Live console header + placeholder ────────────────────────────────────
    st.markdown(
        "<div style='font-size:0.8rem;color:#78909c;margin:10px 0 4px;letter-spacing:.6px;font-weight:700;'>"
        "💬 LIVE CONSOLE</div>",
        unsafe_allow_html=True,
    )
    log_area = st.empty()

    while True:
        is_done          = state["finished"]
        found_pwd        = state["found_password"]
        current_logs     = list(state["logs_list"])
        batch_prog       = dict(state["batch_progress"])
        overall_tested   = sum(b["tested"] for b in batch_prog.values())
        total_candidates = state["total_candidates"]

        pct = min(overall_tested / max(1, total_candidates), 1.0)
        progress_bar.progress(pct)

        # ── Timing / ETA ─────────────────────────────────────────────────────
        now         = time.time()
        active_time = now - state["start_time"] - state["elapsed_paused"]
        if state["paused"] and state["pause_start"] > 0:
            active_time -= (now - state["pause_start"])

        speed = 0.0
        if overall_tested > 0 and active_time > 0.5:
            speed     = overall_tested / active_time
            remaining = total_candidates - overall_tested
            eta_secs  = remaining / speed
            if eta_secs < 60:
                eta_str = f"{int(eta_secs)}s"
            elif eta_secs < 3600:
                eta_str = f"{int(eta_secs // 60)}m {int(eta_secs % 60)}s"
            else:
                eta_str = f"{int(eta_secs // 3600)}h {int((eta_secs % 3600) // 60)}m"
            speed_str = f"{speed:,.0f}/sec"
        else:
            eta_str = speed_str = "Calculating…"

        if state["paused"]:
            status_text.markdown(
                f"⏸️ **PAUSED** — `{overall_tested:,}` / `{total_candidates:,}` "
                f"tested **({pct*100:.2f}%)**"
            )
        else:
            status_text.markdown(
                f"⚡ `{overall_tested:,}` / `{total_candidates:,}` tested "
                f"**({pct*100:.2f}%)** · ⏱ ETA **{eta_str}** · 🚀 {speed_str}"
            )

        # ── Render worker grid ────────────────────────────────────────────────
        worker_grid.markdown(
            _worker_cards_html(batch_prog, num_workers, speed),
            unsafe_allow_html=True,
        )

        # ── Render log console ────────────────────────────────────────────────
        log_area.code("\n".join(current_logs[-14:]), language=None)

        if is_done:
            break
        time.sleep(0.2)

    # ── Result handling ──────────────────────────────────────────────────────
    if found_pwd:
        unlocked = generate_unlocked_pdf(pdf_bytes, found_pwd)
        st.session_state.unlocked_pdf_bytes = unlocked
        st.session_state.password = found_pwd

        st.success(f"🎉 **Password cracked successfully!**")
        bt(f"""
        <div style="background:#1b5e20;border-radius:10px;padding:16px 24px;margin:12px 0;display:inline-block;">
            <span style="color:#a5d6a7;font-size:0.85rem;letter-spacing:1px;">DOCUMENT PASSWORD</span><br>
            <span style="color:#ffffff;font-size:2rem;font-weight:900;letter-spacing:3px;font-family:monospace;">{found_pwd}</span>
        </div>
        """)
        st.download_button(
            label="💾 Download Unlocked PDF",
            data=unlocked,
            file_name="unlocked_statement.pdf",
            mime="application/pdf",
            type="primary",
        )
        if "brute_force_state" in st.session_state:
            del st.session_state.brute_force_state
        st.rerun()
    else:
        if state.get("stop_event") and state["stop_event"].is_set():
            st.warning("⚠️ Brute-force stopped by user.")
        else:
            st.error("❌ Password not found in the specified range / wordlist.")
        if "brute_force_state" in st.session_state:
            del st.session_state.brute_force_state


# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="M-PESA Statement Analyzer",
    page_icon="💸",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Global CSS — dark-mode friendly + Bootstrap tables
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(BOOTSTRAP_CDN, unsafe_allow_html=True)

st.markdown("""
<style>
/* ── KPI cards ─────────────────────────────────────── */
.kpi-card {
    background: linear-gradient(135deg, #1e2a1e 0%, #243024 100%);
    border-radius: 14px;
    padding: 18px 20px 14px;
    border-left: 5px solid #43a047;
    margin-bottom: 14px;
    box-shadow: 0 4px 14px rgba(0,0,0,0.35);
}
.kpi-card.outflow  { border-left-color: #e53935; background: linear-gradient(135deg,#2a1e1e,#301e1e); }
.kpi-card.neutral  { border-left-color: #1e88e5; background: linear-gradient(135deg,#1a2035,#1e2540); }
.kpi-label {
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 1.4px;
    text-transform: uppercase;
    color: #90a4ae;
    margin-bottom: 4px;
}
.kpi-value {
    font-size: 1.45rem;
    font-weight: 800;
    line-height: 1.1;
    color: #e8f5e9;
}
.kpi-card.outflow .kpi-value  { color: #ffcdd2; }
.kpi-card.neutral .kpi-value  { color: #bbdefb; }
.kpi-sub {
    font-size: 0.76rem;
    color: #78909c;
    margin-top: 4px;
}

/* ── Meta info cards ───────────────────────────────── */
.meta-card {
    background: #192027;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 12px;
    border: 1px solid #2c3e50;
}
.meta-label { font-size:0.65rem; color:#607d8b; letter-spacing:1.2px; text-transform:uppercase; }
.meta-value { font-size:1rem; color:#eceff1; font-weight:600; margin-top:2px; }

/* ── Bootstrap table overrides for dark bg ─────────── */
.table { color: #cfd8dc !important; }
.table thead th {
    background: #1b5e20 !important;
    color: #fff !important;
    border-color: #2e7d32 !important;
    font-size: 0.8rem;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
.table-striped > tbody > tr:nth-of-type(odd) > * {
    background-color: rgba(255,255,255,0.04) !important;
    color: #cfd8dc !important;
}
.table-hover > tbody > tr:hover > * {
    background-color: rgba(67,160,71,0.12) !important;
    color: #fff !important;
}
.table td, .table th { border-color: #263238 !important; vertical-align: middle; }
.badge-in  { background:#1b5e20 !important; }
.badge-out { background:#b71c1c !important; }
.amount-in  { color:#69f0ae; font-weight:700; }
.amount-out { color:#ff5252; font-weight:700; }

/* ── Password reveal box ───────────────────────────── */
.pwd-box {
    background:#0d1b0e;
    border:2px solid #43a047;
    border-radius:12px;
    padding:14px 24px;
    display:inline-block;
    margin:10px 0;
}
.pwd-box .lbl { font-size:0.7rem;letter-spacing:1.5px;color:#81c784;text-transform:uppercase; }
.pwd-box .val { font-size:2.2rem;font-weight:900;font-family:monospace;color:#fff;letter-spacing:5px; }
</style>
""", unsafe_allow_html=True)

st.title("💸 M-PESA Statement Analyzer")
st.caption("Securely analyze, decrypt, and visualize your M-PESA statements — all locally.")

# ─────────────────────────────────────────────────────────────────────────────
# Session-state init
# ─────────────────────────────────────────────────────────────────────────────
for key, default in [("unlocked_pdf_bytes", None), ("pdf_name", None), ("password", None)]:
    if key not in st.session_state:
        st.session_state[key] = default

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — upload
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📥 Upload Statement")
    uploaded_file = st.file_uploader("Upload M-PESA PDF Statement", type=["pdf"])
    if uploaded_file:
        if st.session_state.pdf_name != uploaded_file.name:
            st.session_state.unlocked_pdf_bytes = None
            st.session_state.pdf_name = uploaded_file.name
            st.session_state.password = None

    # Show previously cracked password in sidebar
    if st.session_state.password:
        st.markdown("---")
        st.markdown("🔓 **Document Password**")
        st.code(st.session_state.password, language=None)
        if st.session_state.unlocked_pdf_bytes:
            st.download_button(
                "💾 Save Unlocked PDF",
                data=st.session_state.unlocked_pdf_bytes,
                file_name="unlocked_statement.pdf",
                mime="application/pdf",
            )

# ─────────────────────────────────────────────────────────────────────────────
# Main content
# ─────────────────────────────────────────────────────────────────────────────
if not uploaded_file:
    st.info("Please upload an M-PESA statement in the sidebar to get started.")
    st.stop()

pdf_bytes = uploaded_file.read()
enc_info = get_encryption_info(pdf_bytes)
is_encrypted = enc_info["is_encrypted"]

# ── Encrypted path ──────────────────────────────────────────────────────────
if is_encrypted:
    algo  = enc_info.get("algorithm", "Unknown")
    bits  = enc_info.get("key_length", "")
    label = enc_info.get("label", "Encrypted")
    perms = enc_info.get("permissions", {})

    badge_color = "#c62828" if "256" in bits else "#e65100" if "128" in bits else "#6a1b9a"
    perm_html = ""
    if perms:
        perm_parts = [f"{'✅' if v else '🚫'} {k.capitalize()}" for k, v in perms.items()]
        perm_html = f"<div style='font-size:0.78rem;color:#90a4ae;margin-top:6px;'>Permissions: {' &nbsp;|&nbsp; '.join(perm_parts)}</div>"

    bt(f"""
    <div style="display:flex;align-items:center;gap:14px;margin-bottom:10px;">
        <div style="background:{badge_color};color:#fff;border-radius:8px;padding:7px 18px;
                    font-weight:800;font-size:1rem;letter-spacing:1px;">
            🔐 {label}
        </div>
        <span style="color:#cfd8dc;font-size:0.95rem;">
            <strong>{label}</strong> encryption detected — unlock required to proceed.<br>
            <small style="color:#90a4ae;">{algo} algorithm · {bits} key length</small>
        </span>
    </div>
    {perm_html}
    """)

    st.warning(f"🔒 This PDF is encrypted with **{label}** ({algo}, {bits} key). Enter the password or use the brute-forcer below.")

    tab_manual, tab_brute = st.tabs(["🔑 Manual Password", "🛠️ Brute-Force Cracker"])

    with tab_manual:
        with st.form("manual_password_form"):
            pwd_input = st.text_input("Enter Statement Password", type="password")
            submit_pwd = st.form_submit_button("🔓 Unlock PDF")
            if submit_pwd:
                if validate_password(pdf_bytes, pwd_input, enc_info=enc_info):
                    st.session_state.unlocked_pdf_bytes = generate_unlocked_pdf(pdf_bytes, pwd_input)
                    st.session_state.password = pwd_input
                    st.success("✅ PDF unlocked successfully!")
                    st.rerun()
                else:
                    st.error("❌ Wrong password. Please try again.")

    with tab_brute:
        if "brute_force_state" in st.session_state and st.session_state.brute_force_state.get("active"):
            run_progress_ui_loop(pdf_bytes)
        else:
            brute_mode = st.selectbox("Brute-Force Strategy", ["ID Number Range", "Birth Year Range", "Common Wordlist"])

            sys_cores = os.cpu_count() or 4
            default_workers = min(50, sys_cores * 2)

            st.markdown(f"**⚙️ CPU Hardware Acceleration**")
            st.info(
                f"💻 **{sys_cores} Logical CPU Cores Detected.** \n\n"
                f"**Note on GPUs**: PDF decryption (AES/RC4) relies heavily on standard CPU hashing. "
                f"GPU acceleration is not supported by PDF parsing libraries (like PyMuPDF/pikepdf) without custom CUDA wrappers. "
                f"However, we fully bypass the Python GIL to utilize all your CPU cores simultaneously."
            )
            max_workers = st.slider(
                "Concurrent CPU Workers (Threads)",
                min_value=1, max_value=min(200, sys_cores * 10), value=default_workers, step=1,
                help=f"We auto-detected {sys_cores} cores. Setting this higher than your core count will oversubscribe the CPU (often beneficial for I/O bounds, but usually 2x to 4x cores is optimal for decryption)."
            )
            st.caption(f"🔀 Candidates will be split into **{max_workers} parallel batches** running simultaneously.")

            if brute_mode == "ID Number Range":
                col1, col2 = st.columns(2)
                with col1:
                    start_id = st.number_input("Start ID", min_value=1, max_value=999999999, value=1)
                with col2:
                    end_id = st.number_input("End ID", min_value=1, max_value=999999999, value=999999)
                total_comb = int(end_id) - int(start_id) + 1
                st.caption(f"Will attempt **{total_comb:,}** combinations across {max_workers} workers (~{total_comb//max_workers:,} each).")
                if st.button("🚀 Start Brute-Force (ID Range)", type="primary"):
                    candidates = list(brute_force_generator("", "", int(start_id), int(end_id)))
                    start_brute_force_session(pdf_bytes, candidates, "ID Range", enc_info, max_workers)

            elif brute_mode == "Birth Year Range":
                col1, col2 = st.columns(2)
                with col1:
                    start_yr = st.number_input("Start Year", min_value=1950, max_value=2026, value=1980)
                with col2:
                    end_yr = st.number_input("End Year", min_value=1950, max_value=2026, value=2010)
                if st.button("🚀 Start Brute-Force (Birth Years)", type="primary"):
                    candidates = [str(yr) for yr in range(int(start_yr), int(end_yr) + 1)]
                    start_brute_force_session(pdf_bytes, candidates, "Birth Years", enc_info, max_workers)

            elif brute_mode == "Common Wordlist":
                wordlist_file = st.file_uploader("Upload wordlist (.txt, one password per line)")
                if wordlist_file:
                    words = [w.decode("utf-8", errors="ignore").strip() for w in wordlist_file.readlines() if w.strip()]
                    st.caption(f"Loaded **{len(words):,}** candidates from wordlist.")
                    if st.button("🚀 Start Wordlist Attack", type="primary"):
                        start_brute_force_session(pdf_bytes, words, "Wordlist", enc_info, max_workers)

    if st.session_state.unlocked_pdf_bytes is not None:
        working_bytes = st.session_state.unlocked_pdf_bytes
    else:
        st.info("Unlock the statement above to continue with analysis.")
        st.stop()
else:
    working_bytes = pdf_bytes
    st.success("🔓 PDF is unencrypted — opening immediately.")

# ── Show password banner if session has a cracked password ──────────────────
if st.session_state.password:
    bt(f"""
    <div class="pwd-box">
        <div class="lbl">Document Password (cracked)</div>
        <div class="val">{st.session_state.password}</div>
    </div>
    """)

# ─────────────────────────────────────────────────────────────────────────────
# Extract data
# ─────────────────────────────────────────────────────────────────────────────
with st.spinner("Extracting text and transactions…"):
    raw_text = extract_text_from_pdf(working_bytes)
    is_scanned = len(raw_text.strip()) < 100

    if is_scanned:
        st.warning("⚠️ Appears to be a scanned document. OCR required.")
        selected_engine = st.selectbox("OCR Engine", ["auto", "easyocr", "tesseract"])
        if st.button("Run OCR"):
            with st.spinner("Running OCR…"):
                raw_text = ocr_fallback_extract(working_bytes, engine=selected_engine)
                is_scanned = False
        else:
            st.info("Click **Run OCR** to extract text.")
            st.stop()

    tables = extract_tables_from_pdf(working_bytes)
    metadata = extract_metadata(raw_text)
    summary_totals = parse_summary_totals(raw_text)
    transactions = parse_transactions(tables, raw_text)

if not transactions:
    st.error("No transactions found in this statement.")
    st.stop()

analysis = analyze_statement(transactions)

# ─────────────────────────────────────────────────────────────────────────────
# Dashboard header
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## 📊 Statement Analysis Dashboard")

# Customer meta row
c1, c2, c3, c4 = st.columns(4)
for col, lbl, val in [
    (c1, "Customer Name",    metadata["customer_name"]),
    (c2, "Mobile Number",    metadata["mobile_number"]),
    (c3, "Email Address",    metadata["email_address"]),
    (c4, "Statement Period", metadata["statement_period"]),
]:
    with col:
        bt(f'<div class="meta-card"><div class="meta-label">{lbl}</div><div class="meta-value">{val}</div></div>')

# ─────────────────────────────────────────────────────────────────────────────
# Financial KPI cards
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("### Financial Performance Indicators")
k1, k2, k3, k4, k5 = st.columns(5)

# Proper monthly averages
total_inflow   = analysis["total_inflow"]
total_outflow  = analysis["total_outflow"]
avg_in         = analysis["avg_monthly_inflow"]
avg_out        = analysis["avg_monthly_outflow"]
low_bal        = analysis["lowest_balance"]
high_bal       = analysis["highest_balance"]
avg_bal        = analysis["average_balance"]

with k1:
    bt(f"""<div class="kpi-card">
        <div class="kpi-label">Total Inflow</div>
        <div class="kpi-value">KES {total_inflow:,.2f}</div>
        <div class="kpi-sub">Monthly Avg: KES {avg_in:,.2f}</div>
    </div>""")
with k2:
    bt(f"""<div class="kpi-card outflow">
        <div class="kpi-label">Total Outflow</div>
        <div class="kpi-value">KES {abs(total_outflow):,.2f}</div>
        <div class="kpi-sub">Monthly Avg: KES {abs(avg_out):,.2f}</div>
    </div>""")
with k3:
    bt(f"""<div class="kpi-card outflow">
        <div class="kpi-label">Lowest Balance</div>
        <div class="kpi-value">KES {low_bal:,.2f}</div>
    </div>""")
with k4:
    bt(f"""<div class="kpi-card">
        <div class="kpi-label">Highest Balance</div>
        <div class="kpi-value">KES {high_bal:,.2f}</div>
    </div>""")
with k5:
    bt(f"""<div class="kpi-card neutral">
        <div class="kpi-label">Average Balance</div>
        <div class="kpi-value">KES {avg_bal:,.2f}</div>
    </div>""")

# ─────────────────────────────────────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────────────────────────────────────
tab_viz, tab_partners, tab_raw = st.tabs(["📈 Visualizations", "🤝 Top Partners & Merchants", "📋 Transaction Log"])

# ── Visualizations ──────────────────────────────────────────────────────────
with tab_viz:
    v1, v2 = st.columns(2)
    with v1:
        st.markdown("##### Balance Trend Over Time")
        dates    = [t["date"]    for t in transactions if t["date"]]
        balances = [t["balance"] for t in transactions if t["date"]]
        fig_bal  = go.Figure()
        fig_bal.add_trace(go.Scatter(
            x=dates, y=balances, mode="lines+markers", name="Balance",
            line=dict(color="#43a047", width=2.5),
            marker=dict(size=5, color="#81c784"),
        ))
        fig_bal.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", margin=dict(l=10, r=10, t=30, b=10),
        )
        st.plotly_chart(fig_bal, use_container_width=True)

    with v2:
        st.markdown("##### Spend by Category")
        type_totals = {}
        for t in transactions:
            type_totals[t["type"]] = type_totals.get(t["type"], 0.0) + abs(t["amount"])
        fig_pie = go.Figure(data=[go.Pie(
            labels=list(type_totals.keys()),
            values=list(type_totals.values()),
            hole=0.38,
        )])
        fig_pie.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=30, b=10),
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    monthly_in, monthly_out = {}, {}
    for t in transactions:
        if t["date"]:
            m = t["date"].strftime("%Y-%m")
            if t["amount"] > 0:
                monthly_in[m]  = monthly_in.get(m, 0.0)  + t["amount"]
            else:
                monthly_out[m] = monthly_out.get(m, 0.0) + abs(t["amount"])

    all_months = sorted(set(list(monthly_in) + list(monthly_out)))
    if all_months:
        st.markdown("##### Monthly Inflow vs Outflow")
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(x=all_months, y=[monthly_in.get(m, 0) for m in all_months],  name="Inflow",  marker_color="#2e7d32"))
        fig_bar.add_trace(go.Bar(x=all_months, y=[monthly_out.get(m, 0) for m in all_months], name="Outflow", marker_color="#c62828"))
        fig_bar.update_layout(
            barmode="group", template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_bar, use_container_width=True)

# ── Partners ────────────────────────────────────────────────────────────────
with tab_partners:
    p1, p2, p3 = st.columns(3)

    def partner_table(rows, col_label, amount_label):
        if not rows:
            return "<p class='text-muted small'>No transactions.</p>"
        html = f"""
        <table class="table table-sm table-striped table-hover align-middle">
            <thead><tr><th>{col_label}</th><th class="text-center">Txns</th><th class="text-end">{amount_label}</th></tr></thead>
            <tbody>
        """
        for row in rows:
            html += f"""<tr>
                <td>{row['clean_name']}</td>
                <td class="text-center"><span class="badge bg-secondary">{row['count']}</span></td>
                <td class="text-end amount-out">KES {row['total']:,.2f}</td>
            </tr>"""
        html += "</tbody></table>"
        return html

    with p1:
        st.markdown("##### 🏢 Buy Goods Merchants")
        bt(partner_table(analysis["top_merchants"],  "Merchant",  "Spent"))
    with p2:
        st.markdown("##### 🧾 Paybill Accounts")
        bt(partner_table(analysis["top_paybills"],   "Paybill",   "Paid"))
    with p3:
        st.markdown("##### 🧑 Send Money Recipients")
        bt(partner_table(analysis["top_recipients"], "Recipient", "Sent"))

    st.markdown("---")
    e1, e2 = st.columns(2)
    le = analysis["largest_incoming"]
    lo = analysis["largest_outgoing"]
    with e1:
        st.markdown("##### 🟩 Largest Incoming")
        bt(f"""<table class="table table-sm table-borderless">
            <tbody>
                <tr><th class="text-muted small">Receipt</th><td><code>{le['receipt_no']}</code></td></tr>
                <tr><th class="text-muted small">Date</th><td>{le['date']}</td></tr>
                <tr><th class="text-muted small">Amount</th><td class="amount-in">KES {le['amount']:,.2f}</td></tr>
                <tr><th class="text-muted small">Details</th><td>{le['details']}</td></tr>
            </tbody></table>""")
    with e2:
        st.markdown("##### 🟥 Largest Outgoing")
        bt(f"""<table class="table table-sm table-borderless">
            <tbody>
                <tr><th class="text-muted small">Receipt</th><td><code>{lo['receipt_no']}</code></td></tr>
                <tr><th class="text-muted small">Date</th><td>{lo['date']}</td></tr>
                <tr><th class="text-muted small">Amount</th><td class="amount-out">KES {abs(lo['amount']):,.2f}</td></tr>
                <tr><th class="text-muted small">Details</th><td>{lo['details']}</td></tr>
            </tbody></table>""")

# ── Transaction Log ─────────────────────────────────────────────────────────
with tab_raw:
    st.markdown("##### Transaction Log")
    r1, r2 = st.columns([3, 1])
    with r1:
        search_query = st.text_input("🔍 Search receipt no. or details", "").strip().lower()
    with r2:
        categories = ["All"] + sorted(set(t["type"] for t in transactions))
        selected_cat = st.selectbox("Category", categories)

    filtered = [
        t for t in transactions
        if (not search_query or search_query in t["details"].lower() or search_query in t["receipt_no"].lower())
        and (selected_cat == "All" or t["type"] == selected_cat)
    ]

    if filtered:
        rows_html = ""
        for t in filtered:
            amt = t["amount"]
            cls  = "amount-in" if amt > 0 else "amount-out"
            sign = "+" if amt > 0 else ""
            badge_cls = "badge-in" if amt > 0 else "badge-out"
            rows_html += f"""<tr>
                <td><code style="font-size:0.78rem;">{t['receipt_no']}</code></td>
                <td style="white-space:nowrap;">{t['completion_time']}</td>
                <td style="max-width:280px;">{t['details']}</td>
                <td class="{cls}" style="text-align:right;white-space:nowrap;">{sign}KES {abs(amt):,.2f}</td>
                <td style="text-align:right;white-space:nowrap;">KES {t['balance']:,.2f}</td>
                <td><span class="badge {badge_cls}" style="font-size:0.72rem;">{t['type']}</span></td>
            </tr>"""

        bt(f"""
        <div style="overflow-x:auto;">
        <table class="table table-sm table-striped table-hover align-middle" style="font-size:0.86rem;">
            <thead>
                <tr>
                    <th>Receipt No</th>
                    <th>Time</th>
                    <th>Details</th>
                    <th class="text-end">Amount</th>
                    <th class="text-end">Balance</th>
                    <th>Category</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        </div>
        <p class="text-muted small">Showing {len(filtered):,} of {len(transactions):,} transactions.</p>
        """)

        # CSV export
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Receipt No", "Completion Time", "Details", "Amount", "Balance", "Type"])
        for t in filtered:
            writer.writerow([t["receipt_no"], t["completion_time"], t["details"], t["amount"], t["balance"], t["type"]])
        st.download_button(
            label="⬇️ Export as CSV",
            data=output.getvalue().encode("utf-8"),
            file_name="mpesa_transactions.csv",
            mime="text/csv",
        )
    else:
        st.info("No transactions match the current filter.")
