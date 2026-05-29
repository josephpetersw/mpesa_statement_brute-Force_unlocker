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
# BOOTSTRAP_CDN: Includes Bootstrap 5 stylesheet and Font Awesome icons for modern, emoji-free visuals
BOOTSTRAP_CDN = """
<link rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
  integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH"
  crossorigin="anonymous">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
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
            pwd, worker_idx = run_brute_force(
                pdf_bytes, candidates,
                enc_info=enc_info,
                max_workers=num_batches,
                progress_callback=callback,
                stop_event=state["stop_event"],
                pause_event=state["pause_event"],
            )
            state["found_password"] = pwd
            state["found_worker"] = worker_idx
            t_str = datetime.now().strftime("%H:%M:%S")
            if pwd:
                state["logs_list"].append(f"[{t_str}] ✅ SUCCESS — Password: {pwd} (found by Worker {worker_idx + 1})")
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
        "Running":   ("#1b5e20", "#43a047", '<i class="fas fa-play" style="font-size:0.7rem; margin-right:4px;"></i>'),
        "Done":      ("#0d47a1", "#1e88e5", '<i class="fas fa-check" style="font-size:0.7rem; margin-right:4px;"></i>'),
        "Pending":   ("#37474f", "#607d8b", '<i class="fas fa-ellipsis-h" style="font-size:0.7rem; margin-right:4px;"></i>'),
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
                <i class="fas fa-key" style="margin-right:4px;"></i> {last_pwd}
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

    # Dashboard sub-title during decryption using Font Awesome icon instead of emoji
    st.markdown(f"### <i class='fas fa-cogs'></i> Running **{state['strategy_name']}** Brute-Force  ·  {state['max_workers']} concurrent workers", unsafe_allow_html=True)

    col_stop, col_pause = st.columns(2)
    with col_stop:
        if st.button("Stop", key="stop_btn", type="primary"):
            state["stop_event"].set()
            state["finished"] = True
            state["active"] = False
            st.rerun()
    with col_pause:
        paused = state["paused"]
        lbl = "Resume" if paused else "Pause"
        if st.button(lbl, key="pause_btn"):
            state["paused"] = not paused
            if not paused:
                state["pause_event"].clear()
                state["pause_start"] = time.time()
                state["logs_list"].append(f"[{datetime.now().strftime('%H:%M:%S')}] PAUSED")
            else:
                state["pause_event"].set()
                if state["pause_start"] > 0:
                    state["elapsed_paused"] += time.time() - state["pause_start"]
                state["logs_list"].append(f"[{datetime.now().strftime('%H:%M:%S')}] RESUMED")
            st.rerun()

    # ── Overall progress bar + status ────────────────────────────────────────
    progress_bar = st.progress(0.0)
    status_text  = st.empty()

    # ── Worker grid header + placeholder ─────────────────────────────────────
    num_workers = state["max_workers"]
    st.markdown(
        f"<div style='font-size:0.8rem;color:#78909c;margin:8px 0 4px;letter-spacing:.6px;font-weight:700;'>"
        f"<i class='fas fa-network-wired'></i> SPAWNED WORKERS ({num_workers})</div>",
        unsafe_allow_html=True,
    )
    worker_grid = st.empty()

    # ── Live console header + placeholder ────────────────────────────────────
    st.markdown(
        "<div style='font-size:0.8rem;color:#78909c;margin:10px 0 4px;letter-spacing:.6px;font-weight:700;'>"
        "<i class='fas fa-terminal'></i> LIVE CONSOLE</div>",
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
                f"**PAUSED** — `{overall_tested:,}` / `{total_candidates:,}` "
                f"tested **({pct*100:.2f}%)**"
            )
        else:
            status_text.markdown(
                f"`{overall_tested:,}` / `{total_candidates:,}` tested "
                f"**({pct*100:.2f}%)** · ETA **{eta_str}** · Speed: {speed_str}"
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
        st.session_state.cracked_by_worker = state.get("found_worker")

        worker_lbl = f" (Found by Worker {st.session_state.cracked_by_worker + 1})" if st.session_state.cracked_by_worker is not None else ""
        st.success(f"Password cracked successfully!{worker_lbl}")
        bt(f"""
        <div style="background:#1b5e20;border-radius:10px;padding:16px 24px;margin:12px 0;display:inline-block;">
            <span style="color:#a5d6a7;font-size:0.85rem;letter-spacing:1px;"><i class="fas fa-unlock-alt"></i> DOCUMENT PASSWORD</span><br>
            <span style="color:#ffffff;font-size:2rem;font-weight:900;letter-spacing:3px;font-family:monospace;">{found_pwd}</span>
        </div>
        """)
        st.download_button(
            label="Download Unlocked PDF",
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
            st.warning("Brute-force stopped by user.")
        else:
            st.error("Password not found in the specified range / wordlist.")
        if "brute_force_state" in st.session_state:
            del st.session_state.brute_force_state


# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="M-PESA Statement Analyzer",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Global CSS — dark-mode friendly + Bootstrap tables + Nunito Font integration
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(BOOTSTRAP_CDN, unsafe_allow_html=True)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@300;400;600;700;800;900&display=swap');

html, body, [class*="css"], .stApp, .kpi-card, .table, button, select, input, p, div, span, h1, h2, h3, h4, h5, h6 {
    font-family: 'Nunito', sans-serif !important;
}

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
.kpi-card.warning  { border-left-color: #ffb300; background: linear-gradient(135deg,#2e251a,#362b1e); }
.kpi-card.risk     { border-left-color: #d81b60; background: linear-gradient(135deg,#2c1a24,#331d2a); }
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

/* ── Landing Page Hero & Cards ──────────────────────── */
.hero-section {
    background: linear-gradient(135deg, #1b3d22 0%, #0d1b10 100%);
    border-radius: 16px;
    padding: 30px 40px;
    margin-bottom: 28px;
    border: 1px solid #2e7d32;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
}
.hero-title {
    font-size: 2.2rem;
    font-weight: 800;
    color: #ffffff;
    margin-bottom: 8px;
    letter-spacing: -0.5px;
}
.hero-tagline {
    font-size: 1.1rem;
    color: #a5d6a7;
    margin-bottom: 0;
}
.feature-card {
    background: linear-gradient(135deg, #18221b 0%, #0e1511 100%);
    border-radius: 14px;
    padding: 24px;
    border: 1px solid #253327;
    height: 100%;
    transition: transform 0.2s, border-color 0.2s;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
}
.feature-card:hover {
    transform: translateY(-4px);
    border-color: #43a047;
}
.feature-icon {
    margin-bottom: 16px;
    font-size: 2rem;
}
.feature-title {
    font-size: 1.15rem;
    font-weight: 700;
    color: #eceff1;
    margin-bottom: 10px;
}
.feature-desc {
    font-size: 0.88rem;
    color: #b0bec5;
    line-height: 1.6;
}
.cta-box {
    background: #09130a;
    border: 1px dashed #2e7d32;
    border-radius: 12px;
    padding: 24px;
    margin-top: 28px;
    text-align: center;
    box-shadow: 0 4px 14px rgba(0,0,0,0.25);
}
.cta-title {
    font-size: 1.15rem;
    font-weight: 700;
    color: #81c784;
    margin-bottom: 8px;
}
.privacy-banner {
    background: rgba(27, 94, 32, 0.08);
    border: 1px solid rgba(46, 125, 50, 0.25);
    border-radius: 12px;
    padding: 16px 24px;
    margin-top: 36px;
    display: flex;
    align-items: center;
    gap: 16px;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Session-state init
# ─────────────────────────────────────────────────────────────────────────────
for key, default in [("unlocked_pdf_bytes", None), ("pdf_name", None), ("password", None), ("cracked_by_worker", None)]:
    if key not in st.session_state:
        st.session_state[key] = default

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — logo & upload
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("images/M-PESA_LOGO-01.svg.png", use_container_width=True)
    st.markdown("---")
    st.markdown("<h3><i class='fas fa-file-upload'></i> Upload Statement</h3>", unsafe_allow_html=True)
    uploaded_file = st.file_uploader("Upload M-PESA PDF Statement", type=["pdf"])
    if uploaded_file:
        if st.session_state.pdf_name != uploaded_file.name:
            st.session_state.unlocked_pdf_bytes = None
            st.session_state.pdf_name = uploaded_file.name
            st.session_state.password = None
            st.session_state.cracked_by_worker = None

    # Show previously cracked password in sidebar
    if st.session_state.password:
        st.markdown("---")
        worker_lbl = f" (Worker {st.session_state.cracked_by_worker + 1})" if st.session_state.get("cracked_by_worker") is not None else ""
        st.markdown(f"**Document Password**{worker_lbl}")
        st.code(st.session_state.password, language=None)
        if st.session_state.unlocked_pdf_bytes:
            st.download_button(
                "Save Unlocked PDF",
                data=st.session_state.unlocked_pdf_bytes,
                file_name="unlocked_statement.pdf",
                mime="application/pdf",
            )

# ─────────────────────────────────────────────────────────────────────────────
# Main content
# ─────────────────────────────────────────────────────────────────────────────
if not uploaded_file:
    # ── Gorgeous Landing Page Hero Banner ──
    bt("""
    <div class="hero-section">
        <div class="hero-title"><i class="fas fa-credit-card"></i> M-PESA Statement Analyzer</div>
        <div class="hero-tagline">Decrypt, parse, and analyze your financial statement securely and offline.</div>
    </div>
    """)
    
    # ── Feature Info Grid ──
    st.markdown("### Feature Capabilities")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        bt("""
        <div class="feature-card">
            <div class="feature-icon text-success"><i class="fas fa-shield-alt"></i></div>
            <div class="feature-title">Local Password Crack</div>
            <div class="feature-desc">Forgotten password? Lock-in birth years, ID numbers, or wordlists to recover passwords safely offline. Runs entirely on CPU with full multi-threaded performance.</div>
        </div>
        """)
    with col2:
        bt("""
        <div class="feature-card">
            <div class="feature-icon text-warning"><i class="fas fa-bolt"></i></div>
            <div class="feature-title">High-Speed Parsing</div>
            <div class="feature-desc">Processes structured transaction lines in under 0.3s. Built with pure-Python data frames to run seamlessly across restricted environments without requiring administrative overhead.</div>
        </div>
        """)
    with col3:
        bt("""
        <div class="feature-card">
            <div class="feature-icon text-info"><i class="fas fa-chart-bar"></i></div>
            <div class="feature-title">Interactive Analysis</div>
            <div class="feature-desc">Visualizes account balances, cash-flow trends, top paybill/buy-goods merchants, and risk behaviors (such as overdraft count and repayment ratios).</div>
        </div>
        """)
        
    # ── CTA Prompt Box ──
    bt("""
    <div class="cta-box">
        <div class="cta-title"><i class="fas fa-arrow-left"></i> Get Started Instantly</div>
        <div style="color: #b0bec5; font-size: 0.92rem;">
            Please select and upload your M-PESA PDF statement in the left sidebar file uploader to load your dashboard.
        </div>
    </div>
    """)
    
    # ── Client-Side Privacy Guarantee Banner ──
    bt("""
    <div class="privacy-banner">
        <div style="font-size: 1.8rem; color: #43a047;"><i class="fas fa-user-shield"></i></div>
        <div>
            <strong style="color: #eceff1; font-size: 0.95rem;">100% Client-Side Privacy Guarantee</strong><br>
            <span style="color: #90a4ae; font-size: 0.85rem; line-height: 1.4;">
                All file reads, decryption attempts, transaction extractions, and statistical calculations are performed locally in your machine's system memory. None of your statements, passwords, or transaction records are ever sent to an external server.
            </span>
        </div>
    </div>
    """)
    
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
        perm_parts = [f"{'Yes' if v else 'No'} {k.capitalize()}" for k, v in perms.items()]
        perm_html = f"<div style='font-size:0.78rem;color:#90a4ae;margin-top:6px;'>Permissions: {' &nbsp;|&nbsp; '.join(perm_parts)}</div>"

    bt(f"""
    <div style="display:flex;align-items:center;gap:14px;margin-bottom:10px;">
        <div style="background:{badge_color};color:#fff;border-radius:8px;padding:7px 18px;
                    font-weight:800;font-size:1rem;letter-spacing:1px;">
            <i class="fas fa-lock"></i> {label}
        </div>
        <span style="color:#cfd8dc;font-size:0.95rem;">
            <strong>{label}</strong> encryption detected — unlock required to proceed.<br>
            <small style="color:#90a4ae;">{algo} algorithm · {bits} key length</small>
        </span>
    </div>
    {perm_html}
    """)

    st.warning(f"This PDF is encrypted with {label} ({algo}, {bits} key). Enter the password or use the brute-forcer below.")

    tab_manual, tab_brute = st.tabs(["Manual Password", "Brute-Force Cracker"])

    with tab_manual:
        with st.form("manual_password_form"):
            pwd_input = st.text_input("Enter Statement Password", type="password")
            submit_pwd = st.form_submit_button("Unlock PDF")
            if submit_pwd:
                if validate_password(pdf_bytes, pwd_input, enc_info=enc_info):
                    st.session_state.unlocked_pdf_bytes = generate_unlocked_pdf(pdf_bytes, pwd_input)
                    st.session_state.password = pwd_input
                    st.success("PDF unlocked successfully!")
                    st.rerun()
                else:
                    st.error("Wrong password. Please try again.")

    with tab_brute:
        if "brute_force_state" in st.session_state and st.session_state.brute_force_state.get("active"):
            run_progress_ui_loop(pdf_bytes)
        else:
            brute_mode = st.selectbox("Brute-Force Strategy", ["ID Number Range", "Birth Year Range", "Common Wordlist"])

            sys_cores = os.cpu_count() or 4
            default_workers = min(50, sys_cores * 2)

            st.markdown(f"**<i class='fas fa-microchip'></i> CPU Hardware Acceleration**", unsafe_allow_html=True)
            st.info(
                f"Logical CPU Cores Detected: {sys_cores}. \n\n"
                f"Note on GPUs: PDF decryption (AES/RC4) relies heavily on standard CPU hashing. "
                f"GPU acceleration is not supported by PDF parsing libraries (like PyMuPDF/pikepdf) without custom CUDA wrappers. "
                f"However, we fully bypass the Python GIL to utilize all your CPU cores simultaneously."
            )
            max_workers = st.slider(
                "Concurrent CPU Workers (Threads)",
                min_value=1, max_value=min(200, sys_cores * 10), value=default_workers, step=1,
                help=f"We auto-detected {sys_cores} cores. Setting this higher than your core count will oversubscribe the CPU (often beneficial for I/O bounds, but usually 2x to 4x cores is optimal for decryption)."
            )
            st.caption(f"Candidates will be split into {max_workers} parallel batches running simultaneously.")

            if brute_mode == "ID Number Range":
                col1, col2 = st.columns(2)
                with col1:
                    start_id = st.number_input("Start ID", min_value=1, max_value=999999999, value=1)
                with col2:
                    end_id = st.number_input("End ID", min_value=1, max_value=999999999, value=999999)
                total_comb = int(end_id) - int(start_id) + 1
                st.caption(f"Will attempt {total_comb:,} combinations across {max_workers} workers (~{total_comb//max_workers:,} each).")
                if st.button("Start Brute-Force (ID Range)", type="primary"):
                    candidates = list(brute_force_generator("", "", int(start_id), int(end_id)))
                    start_brute_force_session(pdf_bytes, candidates, "ID Range", enc_info, max_workers)

            elif brute_mode == "Birth Year Range":
                col1, col2 = st.columns(2)
                with col1:
                    start_yr = st.number_input("Start Year", min_value=1950, max_value=2026, value=1980)
                with col2:
                    end_yr = st.number_input("End Year", min_value=1950, max_value=2026, value=2010)
                if st.button("Start Brute-Force (Birth Years)", type="primary"):
                    candidates = [str(yr) for yr in range(int(start_yr), int(end_yr) + 1)]
                    start_brute_force_session(pdf_bytes, candidates, "Birth Years", enc_info, max_workers)

            elif brute_mode == "Common Wordlist":
                wordlist_file = st.file_uploader("Upload wordlist (.txt, one password per line)")
                if wordlist_file:
                    words = [w.decode("utf-8", errors="ignore").strip() for w in wordlist_file.readlines() if w.strip()]
                    st.caption(f"Loaded {len(words):,} candidates from wordlist.")
                    if st.button("Start Wordlist Attack", type="primary"):
                        start_brute_force_session(pdf_bytes, words, "Wordlist", enc_info, max_workers)

    if st.session_state.unlocked_pdf_bytes is not None:
        working_bytes = st.session_state.unlocked_pdf_bytes
    else:
        st.info("Unlock the statement above to continue with analysis.")
        st.stop()
else:
    working_bytes = pdf_bytes
    st.success("PDF is unencrypted — opening immediately.")

# ── Show password banner if session has a cracked password ──────────────────
if st.session_state.password:
    worker_lbl = ""
    if st.session_state.get("cracked_by_worker") is not None:
        worker_lbl = f" (found by Worker {st.session_state.cracked_by_worker + 1})"
    bt(f"""
    <div class="pwd-box">
        <div class="lbl"><i class="fas fa-key"></i> Document Password (cracked){worker_lbl}</div>
        <div class="val">{st.session_state.password}</div>
    </div>
    """)

# ─────────────────────────────────────────────────────────────────────────────
# Extract data
# ─────────────────────────────────────────────────────────────────────────────
parse_start_time = time.time()
with st.spinner("Extracting text and transactions..."):
    raw_text = extract_text_from_pdf(working_bytes)
    is_scanned = len(raw_text.strip()) < 100

    if is_scanned:
        st.warning("Appears to be a scanned document. OCR required.")
        selected_engine = st.selectbox("OCR Engine", ["auto", "easyocr", "tesseract"])
        if st.button("Run OCR"):
            with st.spinner("Running OCR..."):
                raw_text = ocr_fallback_extract(working_bytes, engine=selected_engine)
                is_scanned = False
        else:
            st.info("Click Run OCR to extract text.")
            st.stop()

    metadata = extract_metadata(raw_text)
    summary_totals = parse_summary_totals(raw_text)
    
    # Fast path: Parse transactions from PyMuPDF raw text layout (takes <0.3s)
    transactions = parse_transactions(None, raw_text)
    
    # Fallback: Run slow pdfplumber table extraction only if text-based parsing returned 0 transactions
    if not transactions:
        with st.spinner("Fast-path text parser returned 0 results. Running table extraction fallback..."):
            tables = extract_tables_from_pdf(working_bytes)
            transactions = parse_transactions(tables, raw_text)

parse_duration = time.time() - parse_start_time

if not transactions:
    st.error("No transactions found in this statement.")
    st.stop()

analysis = analyze_statement(transactions)

# ─────────────────────────────────────────────────────────────────────────────
# Dashboard header using Nunito & Font Awesome
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2><i class='fas fa-chart-line'></i> Statement Analysis Dashboard</h2>", unsafe_allow_html=True)
st.caption(f"Processed **{len(transactions):,}** transactions in **{parse_duration:.3f}** seconds.")

# Customer metadata row
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
# Financial KPI cards with Font Awesome and defensive get() accessor
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h3><i class='fas fa-wallet'></i> Financial Performance Indicators</h3>", unsafe_allow_html=True)

# Fetching values safely using .get() with fallback defaults
total_inflow   = analysis.get("total_inflow", 0.0)
total_outflow  = analysis.get("total_outflow", 0.0)
avg_in         = analysis.get("avg_monthly_inflow", 0.0)
avg_out        = analysis.get("avg_monthly_outflow", 0.0)
low_bal        = analysis.get("lowest_balance", 0.0)
high_bal       = analysis.get("highest_balance", 0.0)
avg_bal        = analysis.get("average_balance", 0.0)

total_fees     = analysis.get("total_fees", 0.0)
net_savings    = analysis.get("net_savings", 0.0)
savings_rate   = analysis.get("savings_rate", 0.0)
total_txs      = analysis.get("total_txs", 0)
inflow_count   = analysis.get("inflow_count", 0)
outflow_count  = analysis.get("outflow_count", 0)

# Row 1: Key Financial Flow KPIs
r1_c1, r1_c2, r1_c3 = st.columns(3)
with r1_c1:
    bt(f"""<div class="kpi-card">
        <div class="kpi-label"><i class="fas fa-arrow-down text-success" style="margin-right:6px;"></i> Total Inflow</div>
        <div class="kpi-value">KES {total_inflow:,.2f}</div>
        <div class="kpi-sub">Monthly Avg: KES {avg_in:,.2f}</div>
    </div>""")
with r1_c2:
    bt(f"""<div class="kpi-card outflow">
        <div class="kpi-label"><i class="fas fa-arrow-up text-danger" style="margin-right:6px;"></i> Total Outflow</div>
        <div class="kpi-value">KES {abs(total_outflow):,.2f}</div>
        <div class="kpi-sub">Monthly Avg: KES {abs(avg_out):,.2f}</div>
    </div>""")
with r1_c3:
    savings_card_cls = "" if net_savings >= 0 else "outflow"
    bt(f"""<div class="kpi-card {savings_card_cls}">
        <div class="kpi-label"><i class="fas fa-piggy-bank text-info" style="margin-right:6px;"></i> Net Surplus (Savings)</div>
        <div class="kpi-value">KES {net_savings:,.2f}</div>
        <div class="kpi-sub">Savings Rate: {savings_rate:.1f}%</div>
    </div>""")

# Row 2: Secondary Performance KPIs
r2_c1, r2_c2, r2_c3 = st.columns(3)
with r2_c1:
    bt(f"""<div class="kpi-card neutral">
        <div class="kpi-label"><i class="fas fa-balance-scale text-primary" style="margin-right:6px;"></i> Average Balance</div>
        <div class="kpi-value">KES {avg_bal:,.2f}</div>
        <div class="kpi-sub">Total Txns: {total_txs:,} ({inflow_count} in / {outflow_count} out)</div>
    </div>""")
with r2_c2:
    bt(f"""<div class="kpi-card outflow">
        <div class="kpi-label"><i class="fas fa-percent text-warning" style="margin-right:6px;"></i> M-PESA Transaction Fees</div>
        <div class="kpi-value">KES {total_fees:,.2f}</div>
        <div class="kpi-sub">Cost of Service & Levies</div>
    </div>""")
with r2_c3:
    bt(f"""<div class="kpi-card neutral">
        <div class="kpi-label"><i class="fas fa-arrows-alt-v" style="margin-right:6px;"></i> Balance Range</div>
        <div class="kpi-value" style="font-size:1.15rem; line-height: 1.5; font-weight:700;">
            Min: KES {low_bal:,.2f}<br>Max: KES {high_bal:,.2f}
        </div>
        <div class="kpi-sub">Lowest to Highest Balance Peak</div>
    </div>""")

# ─────────────────────────────────────────────────────────────────────────────
# Tabs (Clean text labels only)
# ─────────────────────────────────────────────────────────────────────────────
tab_viz, tab_partners, tab_raw, tab_neg = st.tabs([
    "Visualizations", 
    "Top Partners & Merchants", 
    "Transaction Log",
    "Risk & Negative Indicators"
])

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
        st.markdown("##### <i class='fas fa-store'></i> Buy Goods Merchants", unsafe_allow_html=True)
        bt(partner_table(analysis.get("top_merchants", []),  "Merchant",  "Spent"))
    with p2:
        st.markdown("##### <i class='fas fa-file-invoice-dollar'></i> Paybill Accounts", unsafe_allow_html=True)
        bt(partner_table(analysis.get("top_paybills", []),   "Paybill",   "Paid"))
    with p3:
        st.markdown("##### <i class='fas fa-user-friends'></i> Send Money Recipients", unsafe_allow_html=True)
        bt(partner_table(analysis.get("top_recipients", []), "Recipient", "Sent"))

    st.markdown("---")
    e1, e2 = st.columns(2)
    le = analysis.get("largest_incoming", {"receipt_no": "N/A", "date": "N/A", "amount": 0.0, "details": "N/A"})
    lo = analysis.get("largest_outgoing", {"receipt_no": "N/A", "date": "N/A", "amount": 0.0, "details": "N/A"})
    with e1:
        st.markdown("##### <i class='fas fa-arrow-alt-circle-down text-success'></i> Largest Incoming", unsafe_allow_html=True)
        bt(f"""<table class="table table-sm table-borderless">
            <tbody>
                <tr><th class="text-muted small">Receipt</th><td><code>{le['receipt_no']}</code></td></tr>
                <tr><th class="text-muted small">Date</th><td>{le['date']}</td></tr>
                <tr><th class="text-muted small">Amount</th><td class="amount-in">KES {le['amount']:,.2f}</td></tr>
                <tr><th class="text-muted small">Details</th><td>{le['details']}</td></tr>
            </tbody></table>""")
    with e2:
        st.markdown("##### <i class='fas fa-arrow-alt-circle-up text-danger'></i> Largest Outgoing", unsafe_allow_html=True)
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
        search_query = st.text_input("Search receipt no. or details", "").strip().lower()
    with r2:
        categories = ["All"] + sorted(set(t["type"] for t in transactions))
        selected_cat = st.selectbox("Category", categories)

    # Reset page to 1 if search query or category selection changes
    prev_search = st.session_state.get("txn_log_prev_search", "")
    prev_cat = st.session_state.get("txn_log_prev_cat", "All")
    if search_query != prev_search or selected_cat != prev_cat:
        st.session_state.txn_log_page = 1
        st.session_state.txn_log_prev_search = search_query
        st.session_state.txn_log_prev_cat = selected_cat

    # Highly optimized list comprehension for instant search
    filtered = [
        t for t in transactions
        if (not search_query or search_query in t["details"].lower() or search_query in t["receipt_no"].lower())
        and (selected_cat == "All" or t["type"] == selected_cat)
    ]

    if filtered:
        # Pagination setup
        page_size = 50
        total_items = len(filtered)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        
        # Initialize page state
        if "txn_log_page" not in st.session_state:
            st.session_state.txn_log_page = 1
            
        current_page = min(st.session_state.txn_log_page, total_pages)
        st.session_state.txn_log_page = current_page
        
        start_idx = (current_page - 1) * page_size
        end_idx = min(start_idx + page_size, total_items)
        
        # Paginate results
        page_txs = filtered[start_idx:end_idx]

        # Top pagination controls
        col_prev, col_page, col_next = st.columns([1, 2, 1])
        with col_prev:
            if st.button("Previous", key="prev_page_btn", disabled=(current_page == 1)):
                st.session_state.txn_log_page -= 1
                st.rerun()
        with col_page:
            st.markdown(
                f"<div style='text-align:center;line-height:2.2rem;font-weight:600;color:#90a4ae;font-size:0.88rem;'>"
                f"Page {current_page} of {total_pages} &nbsp;·&nbsp; showing {start_idx+1} to {end_idx} of {total_items:,} results"
                f"</div>",
                unsafe_allow_html=True
            )
        with col_next:
            if st.button("Next", key="next_page_btn", disabled=(current_page == total_pages)):
                st.session_state.txn_log_page += 1
                st.rerun()

        # Render rows for current page only
        rows_html = ""
        for t in page_txs:
            amt = t["amount"]
            cls  = "amount-in" if amt > 0 else "amount-out"
            sign = "+" if amt > 0 else ""
            
            # Nice Bootstrap pill badge styling
            if amt > 0:
                badge_cls = "bg-success"
            elif t["type"] in ("Paybill", "Buy Goods", "Send Money", "Agent Withdrawal", "Airtime Purchase"):
                badge_cls = "bg-danger"
            else:
                badge_cls = "bg-secondary"

            rows_html += f"""<tr>
                <td><code style="font-size:0.78rem;">{t['receipt_no']}</code></td>
                <td style="white-space:nowrap;">{t['completion_time']}</td>
                <td style="max-width:280px;word-wrap:break-word;">{t['details']}</td>
                <td class="{cls}" style="text-align:right;white-space:nowrap;">{sign}KES {abs(amt):,.2f}</td>
                <td style="text-align:right;white-space:nowrap;">KES {t['balance']:,.2f}</td>
                <td><span class="badge {badge_cls}" style="font-size:0.72rem;font-weight:600;">{t['type']}</span></td>
            </tr>"""

        # Table rendering
        bt(f"""
        <div style="overflow-x:auto; margin: 12px 0 18px 0;">
        <table class="table table-sm table-striped table-hover align-middle" style="font-size:0.86rem; border: 1px solid #263238;">
            <thead>
                <tr>
                    <th style="padding: 10px 8px;">Receipt No</th>
                    <th style="padding: 10px 8px;">Time</th>
                    <th style="padding: 10px 8px;">Details</th>
                    <th class="text-end" style="padding: 10px 8px;">Amount</th>
                    <th class="text-end" style="padding: 10px 8px;">Balance</th>
                    <th style="padding: 10px 8px;">Category</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        </div>
        """)

        # CSV export (keeps full filtered list)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Receipt No", "Completion Time", "Details", "Amount", "Balance", "Type"])
        for t in filtered:
            writer.writerow([t["receipt_no"], t["completion_time"], t["details"], t["amount"], t["balance"], t["type"]])
        st.download_button(
            label="Export All Filtered as CSV",
            data=output.getvalue().encode("utf-8"),
            file_name="mpesa_transactions.csv",
            mime="text/csv",
            key="csv_export_btn"
        )
    else:
        st.info("No transactions match the current filter.")

# ── Risk & Negative Indicators ────────────────────────────────────────────────
with tab_neg:
    st.markdown("##### <i class='fas fa-exclamation-triangle text-warning'></i> Financial Risk & Negative Indicators", unsafe_allow_html=True)
    st.caption("Key vulnerability indicators, including account inactivity, high spending days, deficit periods, and overdraft dependencies.")
    
    # Row of KPI Cards
    n1, n2, n3 = st.columns(3)
    with n1:
        # Longest Dormant Period
        gap_days = analysis.get("longest_gap_days", 0.0)
        gap_str = f"{gap_days:.1f} Days" if gap_days > 0 else "0 Days"
        bt(f"""<div class="kpi-card warning">
            <div class="kpi-label"><i class="fas fa-hourglass-half"></i> Longest Dormant Period</div>
            <div class="kpi-value">{gap_str}</div>
            <div class="kpi-sub">Span: {analysis.get('longest_gap_start', 'N/A')} to {analysis.get('longest_gap_end', 'N/A')}</div>
        </div>""")
        
    with n2:
        # Peak Outflow Day
        bt(f"""<div class="kpi-card outflow">
            <div class="kpi-label"><i class="fas fa-fire"></i> Peak Outflow Day</div>
            <div class="kpi-value">KES {analysis.get('peak_outflow_amount', 0.0):,.2f}</div>
            <div class="kpi-sub">Date: {analysis.get('peak_outflow_day', 'N/A')}</div>
        </div>""")
        
    with n3:
        # Overdraft Reliance
        ov_vol = analysis.get("overdraft_total_volume", 0.0)
        bt(f"""<div class="kpi-card risk">
            <div class="kpi-label"><i class="fas fa-chart-line"></i> Overdraft (Fuliza) Triggers</div>
            <div class="kpi-value">{analysis.get('overdraft_events', 0)} Times</div>
            <div class="kpi-sub">Total Borrowed: KES {ov_vol:,.2f}</div>
        </div>""")

    # Deficit Months & Debt Reliance Breakdown
    col_def, col_debt = st.columns(2)
    
    with col_def:
        st.markdown("##### <i class='fas fa-chart-area'></i> Monthly Deficit Analysis", unsafe_allow_html=True)
        st.caption("Months where total cash outflow exceeded total cash inflow.")
        neg_months = analysis.get("negative_months", [])
        if neg_months:
            rows_html = ""
            for m in neg_months:
                rows_html += f"""<tr>
                    <td><strong>{m['month']}</strong></td>
                    <td class="amount-in" style="text-align:right;">KES {m['inflow']:,.2f}</td>
                    <td class="amount-out" style="text-align:right;">KES {m['outflow']:,.2f}</td>
                    <td class="amount-out" style="text-align:right; font-weight:bold;">-KES {m['deficit']:,.2f}</td>
                </tr>"""
            bt(f"""
            <table class="table table-sm table-striped table-hover align-middle" style="font-size:0.86rem; border: 1px solid #263238;">
                <thead>
                    <tr>
                        <th style="padding: 8px;">Month</th>
                        <th class="text-end" style="padding: 8px;">Inflow</th>
                        <th class="text-end" style="padding: 8px;">Outflow</th>
                        <th class="text-end" style="padding: 8px;">Net Deficit</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
            """)
        else:
            st.success("No net deficit months detected. Inflow exceeded outflow in all active months!")

    with col_debt:
        st.markdown("##### <i class='fas fa-credit-card'></i> Overdraft & Debt Repayment Ratio", unsafe_allow_html=True)
        st.caption("Comparison of total amount borrowed via Fuliza vs total amount repaid.")
        
        # Details comparison
        borrowed = analysis.get("overdraft_total_volume", 0.0)
        repaid = analysis.get("repayments_total_volume", 0.0)
        b_count = analysis.get("overdraft_events", 0)
        r_count = analysis.get("repayments_count", 0)
        
        repay_ratio = (repaid / borrowed * 100) if borrowed > 0 else 0.0
        ratio_color = "#69f0ae" if repay_ratio >= 100 else "#ffb300" if repay_ratio > 80 else "#ff5252"
        
        bt(f"""
        <div style="background:#192027; border: 1px solid #2c3e50; border-radius:10px; padding:18px; margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <span style="font-size:0.85rem; color:#90a4ae; font-weight:600;">Repayment Rate</span>
                <span style="font-size:1.35rem; color:{ratio_color}; font-weight:800;">{repay_ratio:.1f}%</span>
            </div>
            <!-- Progress bar -->
            <div style="background:#1c2b1c; border-radius:4px; height:8px; margin-bottom:16px;">
                <div style="background:{ratio_color}; width:{min(repay_ratio, 100.0):.1f}%; height:8px; border-radius:4px; transition:width .3s;"></div>
            </div>
            
            <table class="table table-sm table-borderless" style="font-size:0.82rem; margin-bottom:0; color:#cfd8dc !important;">
                <tbody>
                    <tr>
                        <th style="color:#90a4ae; padding:4px 0;">Total Borrowed Volume</th>
                        <td class="amount-out" style="text-align:right; padding:4px 0;">KES {borrowed:,.2f} ({b_count} txs)</td>
                    </tr>
                    <tr>
                        <th style="color:#90a4ae; padding:4px 0;">Total Repaid Volume</th>
                        <td class="amount-in" style="text-align:right; padding:4px 0;">KES {repaid:,.2f} ({r_count} txs)</td>
                    </tr>
                    <tr style="border-top:1px solid #2c3e50;">
                        <th style="color:#eceff1; padding:6px 0;">Outstanding Debt Est.</th>
                        <td style="text-align:right; padding:6px 0; font-weight:bold; color:{'#fff' if borrowed-repaid <= 0 else '#ff5252'}">
                            KES {max(0.0, borrowed - repaid):,.2f}
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
        """)
