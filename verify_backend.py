import os
from pdf_handler import detect_encryption, validate_password, run_brute_force, brute_force_generator, generate_unlocked_pdf
from extractor import extract_text_from_pdf, extract_tables_from_pdf
from parser import extract_metadata, parse_summary_totals, parse_transactions
from analyzer import analyze_statement

def main():
    print("--- 1. Testing Unencrypted Statement ---")
    with open("sample_normal.pdf", "rb") as f:
        pdf_bytes = f.read()
        
    is_enc = detect_encryption(pdf_bytes)
    print(f"Is encrypted: {is_enc} (Expected: False)")
    
    text = extract_text_from_pdf(pdf_bytes)
    tables = extract_tables_from_pdf(pdf_bytes)
    print(f"Extracted text length: {len(text)}")
    print(f"Extracted tables count: {len(tables)}")
    
    meta = extract_metadata(text)
    print(f"Metadata: {meta}")
    
    totals = parse_summary_totals(text)
    print(f"Totals: {totals}")
    
    print("--- Extracted Raw Text ---")
    print(text)
    print("--------------------------")
    
    df = parse_transactions(tables, text)
    print(f"Parsed Transactions count: {len(df)}")
    if df:
        print(df[0])
        
    analysis = analyze_statement(df)
    print(f"Analysis results keys: {list(analysis.keys())}")
    print(f"Inflow: {analysis.get('total_inflow')}, Outflow: {analysis.get('total_outflow')}")
    
    print("\n--- 2. Testing Encrypted Statement & Brute Force ---")
    with open("sample_encrypted.pdf", "rb") as f:
        enc_pdf_bytes = f.read()
        
    is_enc_2 = detect_encryption(enc_pdf_bytes)
    print(f"Is encrypted: {is_enc_2} (Expected: True)")
    
    # Try invalid password
    is_valid_bad = validate_password(enc_pdf_bytes, "wrong_pass")
    print(f"Password 'wrong_pass' valid: {is_valid_bad} (Expected: False)")
    
    # Try brute force
    print("Running brute force range 30002100 to 30002200...")
    candidates = list(brute_force_generator("", "", 30002100, 30002200))
    found_pwd, worker_idx = run_brute_force(enc_pdf_bytes, candidates)
    print(f"Brute forced password: {found_pwd} (Expected: 30002154, found by Worker {worker_idx + 1 if worker_idx is not None else 'None'})")
    
    # Verify decrypt
    if found_pwd:
        unlocked_bytes = generate_unlocked_pdf(enc_pdf_bytes, found_pwd)
        text_dec = extract_text_from_pdf(unlocked_bytes)
        print(f"Decrypted text length: {len(text_dec)} (Expected: > 0)")
        
        meta_dec = extract_metadata(text_dec)
        print(f"Decrypted customer: {meta_dec.get('customer_name')}")

if __name__ == "__main__":
    main()
