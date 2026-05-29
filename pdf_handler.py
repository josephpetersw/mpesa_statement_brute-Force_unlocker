import io
import fitz  # PyMuPDF
import pikepdf
import pypdf
import concurrent.futures
import threading

# ─────────────────────────────────────────────────────────────────────────────
# Encryption detection & info
# ─────────────────────────────────────────────────────────────────────────────

# Map fitz encryption constants → human-readable labels
_FITZ_ENC_MAP = {
    fitz.PDF_ENCRYPT_NONE:       ("None", ""),
    fitz.PDF_ENCRYPT_RC4_40:     ("RC4", "40-bit"),
    fitz.PDF_ENCRYPT_RC4_128:    ("RC4", "128-bit"),
    fitz.PDF_ENCRYPT_AES_128:    ("AES", "128-bit"),
    fitz.PDF_ENCRYPT_AES_256:    ("AES", "256-bit"),
    fitz.PDF_ENCRYPT_UNKNOWN:    ("Unknown", ""),
}

def get_encryption_info(pdf_bytes: bytes) -> dict:
    """
    Returns a detailed dict describing the PDF encryption:
      {
        "is_encrypted": bool,
        "algorithm": str,    e.g. "AES", "RC4", "None", "Unknown"
        "key_length": str,   e.g. "256-bit", "128-bit", ""
        "label": str,        e.g. "AES-256", "RC4-128", "None"
        "permissions": dict  (owner/user password required flags)
      }
    """
    info = {
        "is_encrypted": False,
        "algorithm": "None",
        "key_length": "",
        "label": "None",
        "permissions": {}
    }

    # ── Primary: PyMuPDF (most complete metadata) ──────────────────────────
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        info["is_encrypted"] = doc.is_encrypted

        if doc.is_encrypted:
            enc_method = doc.encryption_method          # int constant
            algo, bits = _FITZ_ENC_MAP.get(enc_method, ("Unknown", ""))
            info["algorithm"] = algo
            info["key_length"] = bits
            info["label"] = f"{algo}-{bits}" if bits else algo

            # Permission / protection flags (only readable after auth for some docs)
            perms = doc.permissions
            info["permissions"] = {
                "print":    bool(perms & fitz.PDF_PERM_PRINT),
                "copy":     bool(perms & fitz.PDF_PERM_COPY),
                "annotate": bool(perms & fitz.PDF_PERM_ANNOTATE),
                "modify":   bool(perms & fitz.PDF_PERM_MODIFY),
            }
        doc.close()
        return info
    except Exception:
        pass

    # ── Secondary: pikepdf (catches edge cases fitz might miss) ───────────
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            enc = pdf.encryption
            if enc:
                info["is_encrypted"] = True
                r_val = enc.get("R", 0)           # Revision number in PDF spec
                # R<=3 → RC4, R==4 may be RC4-128 or AES-128, R>=5 → AES-256
                if r_val >= 6:
                    info["algorithm"], info["key_length"] = "AES", "256-bit"
                elif r_val == 5:
                    info["algorithm"], info["key_length"] = "AES", "256-bit"
                elif r_val == 4:
                    info["algorithm"], info["key_length"] = "AES", "128-bit"
                elif r_val == 3:
                    info["algorithm"], info["key_length"] = "RC4", "128-bit"
                else:
                    info["algorithm"], info["key_length"] = "RC4", "40-bit"
                info["label"] = f"{info['algorithm']}-{info['key_length']}" if info["key_length"] else info["algorithm"]
            else:
                info["is_encrypted"] = False
        return info
    except pikepdf.PasswordError:
        # Encrypted but couldn't open — still report it
        info["is_encrypted"] = True
        if info["label"] == "None":
            info["algorithm"] = "Unknown"
            info["label"] = "Unknown (password required)"
        return info
    except Exception:
        pass

    # ── Fallback: pypdf ────────────────────────────────────────────────────
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            info["is_encrypted"] = True
            if info["label"] == "None":
                info["algorithm"] = "Unknown"
                info["label"] = "Encrypted (type undetermined)"
    except Exception:
        pass

    return info


def detect_encryption(pdf_bytes: bytes) -> bool:
    """Quick boolean encryption check."""
    return get_encryption_info(pdf_bytes)["is_encrypted"]


# ─────────────────────────────────────────────────────────────────────────────
# Password validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_password_fitz(pdf_bytes: bytes, password: str) -> bool:
    """Validate via PyMuPDF — works for RC4-40, RC4-128, AES-128, AES-256."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        res = doc.authenticate(password)
        doc.close()
        return res > 0
    except Exception:
        return False


def validate_password_pikepdf(pdf_bytes: bytes, password: str) -> bool:
    """
    Validate via pikepdf — uses QPDF under the hood.
    Excellent for AES-256 (PDF 2.0 / R=6) which fitz may handle slightly differently.
    """
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes), password=password):
            return True
    except Exception:
        return False


def validate_password_pypdf(pdf_bytes: bytes, password: str) -> bool:
    """Validate via pypdf — tertiary fallback."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        return reader.decrypt(password) > 0
    except Exception:
        return False


def validate_password(pdf_bytes: bytes, password: str, enc_info: dict = None) -> bool:
    """
    Validates a password using the most appropriate library for the encryption type.
    - AES-256: pikepdf first (QPDF handles R=6 most reliably), then fitz, then pypdf
    - All others: fitz first, then pikepdf, then pypdf
    """
    if enc_info is None:
        enc_info = {}

    algo = enc_info.get("algorithm", "")
    bits = enc_info.get("key_length", "")
    is_aes256 = (algo == "AES" and bits == "256-bit")

    if is_aes256:
        # pikepdf (QPDF) is the gold-standard for AES-256 / PDF 2.0
        if validate_password_pikepdf(pdf_bytes, password):
            return True
        if validate_password_fitz(pdf_bytes, password):
            return True
        return validate_password_pypdf(pdf_bytes, password)
    else:
        if validate_password_fitz(pdf_bytes, password):
            return True
        if validate_password_pikepdf(pdf_bytes, password):
            return True
        return validate_password_pypdf(pdf_bytes, password)


# ─────────────────────────────────────────────────────────────────────────────
# Unlocked PDF generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_unlocked_pdf(pdf_bytes: bytes, password: str) -> bytes:
    """
    Generates an unlocked (decrypted) copy of the PDF in memory.
    Uses pikepdf first (cleanest for AES-256), falls back to fitz.
    """
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes), password=password) as pdf:
            out = io.BytesIO()
            pdf.save(out)
            return out.getvalue()
    except Exception:
        pass

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        doc.authenticate(password)
        out = io.BytesIO()
        doc.save(out)
        doc.close()
        return out.getvalue()
    except Exception as e:
        raise ValueError(f"Failed to generate unlocked PDF: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Brute-force engine
# ─────────────────────────────────────────────────────────────────────────────

def brute_force_generator(prefix: str, suffix: str, min_val: int, max_val: int, pad_length: int = 0):
    """Yields candidate passwords for numeric range attacks."""
    for i in range(min_val, max_val + 1):
        num_str = str(i).zfill(pad_length) if pad_length > 0 else str(i)
        yield f"{prefix}{num_str}{suffix}"


def _make_checker(pdf_bytes: bytes, enc_info: dict):
    """
    Returns the fastest single-candidate checker function for the detected
    encryption type, avoiding redundant library round-trips in hot loops.
    Note: We do NOT use PyMuPDF (fitz) for multi-threaded validation because
    fitz.open is not thread-safe and deadlocks under concurrent execution.
    """
    def check(pwd):
        pwd_str = str(pwd)
        try:
            return validate_password_pikepdf(pdf_bytes, pwd_str)
        except Exception:
            pass
        try:
            return validate_password_pypdf(pdf_bytes, pwd_str)
        except Exception:
            return False

    return check


def run_brute_force(
    pdf_bytes: bytes,
    candidates: list,
    enc_info: dict = None,
    max_workers: int = 20,
    progress_callback=None,
    stop_event=None,
    pause_event=None,
) -> str:
    """
    Multi-threaded brute-force engine.

    The keyspace is split into exactly `num_batches` contiguous slices
    (= max_workers, clamped to 1–200). Each slice runs sequentially inside
    its own worker thread. This eliminates nested thread pools, avoids GIL queue
    blocking, and prevents deadlocks.

    Args:
        pdf_bytes:         Raw PDF bytes.
        candidates:        Ordered list of password strings to try.
        enc_info:          Output of get_encryption_info() (optional).
        max_workers:       Number of concurrent worker threads (1–200).
        progress_callback: callable(batch_idx, tested, total, last_pwd)
        stop_event:        threading.Event — set to abort.
        pause_event:       threading.Event — clear to pause, set to resume.
    Returns:
        Matching password string, or None if not found.
    """
    if enc_info is None:
        enc_info = {}

    num_batches = max(1, min(int(max_workers), 200))
    checker     = _make_checker(pdf_bytes, enc_info)
    total       = len(candidates)

    if stop_event is None:
        stop_event = threading.Event()
    if pause_event is None:
        pause_event = threading.Event()
        pause_event.set()           # default: running (not paused)

    # ── Divide candidates into exactly num_batches contiguous slices ────────
    batch_size = (total + num_batches - 1) // num_batches
    batches: list[list] = []
    for i in range(num_batches):
        s = i * batch_size
        e = min(s + batch_size, total)
        batches.append(candidates[s:e] if s < e else [])

    # ── Shared result container ─────────────────────────────────────────────
    found_password: list = [None]

    def batch_worker(batch_idx: int, batch_candidates: list):
        """Runs one batch sequentially, reporting progress periodically."""
        try:
            n = len(batch_candidates)
            # Immediately report that the worker has started running
            _safe_callback(progress_callback, batch_idx, 0, n, "Starting...")
            
            if n == 0:
                return

            # Determine progress report interval dynamically based on slice size
            chunk_size = max(10, min(100, n // 50))

            for i, pwd in enumerate(batch_candidates):
                # ── Pause gate ──────────────────────────────────────────
                pause_event.wait()                  # blocks while paused
                if stop_event.is_set():
                    break

                # Test candidate
                try:
                    if checker(pwd):
                        found_password[0] = pwd
                        stop_event.set()            # abort all other threads
                        break
                except Exception:
                    pass

                # Report progress periodically
                if (i + 1) % chunk_size == 0:
                    _safe_callback(progress_callback, batch_idx, i + 1, n, str(pwd))

            # Report final progress if not aborted
            if not stop_event.is_set():
                _safe_callback(progress_callback, batch_idx, n, n, str(batch_candidates[-1] if batch_candidates else ""))

        except Exception:
            pass   # batch threads must never raise

    # ── Launch all batch threads simultaneously ──────────────────────────────
    threads = [
        threading.Thread(
            target=batch_worker,
            args=(i, batches[i]),
            name=f"Batch-{i+1}of{num_batches}",
            daemon=True,
        )
        for i in range(num_batches)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()   # wait until every batch is done (or stopped)

    return found_password[0]


def _safe_callback(cb, *args):
    """Call progress callback safely — never lets it crash the engine."""
    if cb is None:
        return
    try:
        cb(*args)
    except Exception:
        pass
