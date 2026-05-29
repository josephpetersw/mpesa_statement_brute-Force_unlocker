# M-PESA Statement Brute-Force Unlocker & Analyzer

A high-performance, secure, and fully local web application designed to decrypt, parse, analyze, and visualize M-PESA PDF statements. 

This tool is built to handle forgotten PDF statement passwords locally using a highly optimized, thread-safe brute-force engine, and then present your transaction history in an elegant, interactive dashboard.

---

## Tech Stack & Architecture

### 1. Frontend & UI
* **Streamlit**: Powers the dark-themed, responsive web dashboard and real-time status UI.
* **Plotly**: Renders interactive, high-fidelity visualization charts (balance trends, monthly flows, category distributions).
* **Bootstrap (v5.3) & Font Awesome (v6.4)**: Integrated via CDN for customized, premium CSS grid tables, UI cards, and vector icons.
* **Nunito Font**: Styled with Google Fonts Nunito family for a clean and professional appearance.

### 2. Multi-Threaded Decryption Engine (`pdf_handler.py`)
* **`pikepdf` (C++ QPDF bindings)**: Utilized as the primary, thread-safe engine for checking PDF passwords concurrently.
* **`pypdf`**: Integrated as a pure-Python fallback validation engine.
* **GIL Bypassing Concurrency**: Implemented using native Python `threading.Thread` workers. Candidates are partitioned into equal slices running in parallel across all logical CPU cores.
* **Safety Note on PyMuPDF (`fitz`)**: Redefined to run only sequentially or for single-document verification, as PyMuPDF's C-bindings are not thread-safe for concurrent file-opening and will deadlock under multi-threaded execution.

### 3. Parsing & Analytics (`extractor.py`, `parser.py`, `analyzer.py`)
* **PyMuPDF (`fitz`)**: Primary parser for structured document metadata, permissions inspection, and raw text/table extraction.
* **OCR fallback**: Integrates EasyOCR / Tesseract to extract text from scanned statement PDFs.
* **Pure-Python Data Structures**: Built entirely using standard library data structures (`lists`, `dict`, `collections.Counter`) for complete portability and memory efficiency.

---

## Key Features

* **Real-Time Cracking Interface**:
  * Dynamic overall progress bar and percentage metric.
  * Real-time calculation of Estimated Time Remaining (ETA) and throughput speed (passwords/second).
  * **Spawned Worker Grid**: Visually monitors individual worker cards showing their status (Pending, Running, Done), completion percentage, and last-tried candidate.
  * **Interactive Controls**: Instantly Stop, Pause, or Resume the background cracking threads.
  * **Live Console**: Scrollable, real-time logging output of worker threads.
* **Data Privacy**: 100% offline and local execution; your financial data never leaves your machine.
* **Interactive Financial Analytics**:
  * **Visualizations**: Account balance trend, spend by categories, and grouped monthly inflow vs outflow bars.
  * **Top Partners**: Automatically aggregates your top Buy Goods merchants, Paybill accounts, and Send Money recipients.
  * **Transaction Log**: Fully searchable, categorizable table with high-performance pagination (50 items per page) and CSV export capabilities.
  * **Risk & Negative Indicators**: Displays dormant (inactive) periods, peak spending days, overdraft (Fuliza) triggers/repayments, net deficit months, and outstanding debt estimates with a visual repayment rate progress bar.

---

## Setup & Installation

### Prerequisites
* Python 3.9 or higher
* Pip (Python package installer)

### 1. Install Dependencies
Install all required libraries using the provided `requirements.txt`:
```bash
pip install -r requirements.txt
```

### 2. Run the Application
Start the local Streamlit development server:
```bash
streamlit run app.py
```
Then open the local URL (usually `http://localhost:8501`) in your web browser.

---

## Security & Privacy Warning
This application is designed strictly for local recovery of personal statement passwords. Never upload or run unknown PDF files from unverified third parties, and always safeguard your personal financial data.
