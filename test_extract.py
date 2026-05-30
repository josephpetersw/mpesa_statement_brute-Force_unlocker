import sys
sys.path.append('.')
from extractor import extract_text_from_pdf
from parser import extract_metadata

with open("sample_normal.pdf", "rb") as f:
    unlocked = f.read()

try:
    text_dec = extract_text_from_pdf(unlocked)
    meta_dec = extract_metadata(text_dec)
    c_name = meta_dec.get("customer_name")
    print(f"DEBUG: Customer name extracted for filename: {c_name}")
    if c_name and c_name not in ("Unknown", "N/A"):
        safe_name = "".join(c for c in c_name if c.isalnum() or c in (' ', '_')).strip().replace(' ', '_')
        out_filename = f"{safe_name}_unlocked.pdf"
        print(f"DEBUG: Final filename: {out_filename}")
    else:
        out_filename = "unlocked_statement.pdf"
except Exception as e:
    print(f"DEBUG: Exception extracting metadata for filename: {e}")
    out_filename = "unlocked_statement.pdf"
    
print(f"Outcome: {out_filename}")
