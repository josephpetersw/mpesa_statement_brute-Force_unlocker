import fitz
import os

def create_sample_pdf(filename, password=None):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842) # A4
    
    # Metadata
    page.insert_text(fitz.Point(50, 50), "DETAILED M-PESA STATEMENT", fontsize=16)
    page.insert_text(fitz.Point(50, 80), "Customer Name: JOHN DOE", fontsize=10)
    page.insert_text(fitz.Point(50, 95), "Mobile Number: 254712345678", fontsize=10)
    page.insert_text(fitz.Point(50, 110), "Email Address: john.doe@gmail.com", fontsize=10)
    page.insert_text(fitz.Point(50, 125), "Statement Period: 01 May 2026 to 28 May 2026", fontsize=10)
    page.insert_text(fitz.Point(50, 140), "Request Date: 2026-05-29 12:00:00", fontsize=10)
    
    # Summary Totals Section
    page.insert_text(fitz.Point(50, 180), "SUMMARY TOTALS", fontsize=12)
    page.insert_text(fitz.Point(50, 200), "Send Money: 5,000.00", fontsize=10)
    page.insert_text(fitz.Point(50, 215), "Received Money: 15,000.00", fontsize=10)
    page.insert_text(fitz.Point(50, 230), "Agent Deposit: 10,000.00", fontsize=10)
    page.insert_text(fitz.Point(50, 245), "Agent Withdrawal: 2,000.00", fontsize=10)
    page.insert_text(fitz.Point(50, 260), "Paybill: 8,000.00", fontsize=10)
    page.insert_text(fitz.Point(50, 275), "Buy Goods: 4,000.00", fontsize=10)
    page.insert_text(fitz.Point(50, 290), "Others: 500.00", fontsize=10)
    page.insert_text(fitz.Point(50, 305), "Total: 34,500.00", fontsize=10)
    
    # Table headers
    headers = ["Receipt No", "Completion Time", "Details", "Amount", "Balance"]
    y = 350
    for idx, h in enumerate(headers):
        x = 50 + (idx * 100)
        page.insert_text(fitz.Point(x, y), h, fontsize=9)
        
    # Transaction rows
    txs = [
        ("RCE4P8D9S1", "2026-05-02 09:15:00", "Agent Deposit of 10000 KES", "10000.00", "10000.00"),
        ("RCE4P8D9S2", "2026-05-05 14:22:00", "Customer Transfer to 254787654321 - MARY ANNE", "-5000.00", "5000.00"),
        ("RCE4P8D9S3", "2026-05-10 18:30:00", "Customer Transfer Receive from 254711111111 - DANIEL", "15000.00", "20000.00"),
        ("RCE4P8D9S4", "2026-05-15 11:05:00", "Pay Bill to 247247 - EQUITY BANK", "-8000.00", "12000.00"),
        ("RCE4P8D9S5", "2026-05-20 16:45:00", "Merchant Payment to 765432 - SUPERMARKET", "-4000.00", "8000.00"),
        ("RCE4P8D9S6", "2026-05-22 10:10:00", "Agent Withdrawal to Agent 12345", "-2000.00", "6000.00"),
        ("RCE4P8D9S7", "2026-05-25 12:00:00", "Airtime Purchase", "-500.00", "5500.00"),
    ]
    
    for tx in txs:
        y += 25
        for idx, val in enumerate(tx):
            x = 50 + (idx * 100)
            page.insert_text(fitz.Point(x, y), val, fontsize=8)
            
    # Save document
    if password:
        # Save as encrypted PDF using PyMuPDF (user password, owner password)
        doc.save(filename, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw="admin")
    else:
        doc.save(filename)
        
    doc.close()
    print(f"Created sample PDF: {filename} (Encrypted: {password is not None})")

if __name__ == "__main__":
    create_sample_pdf("sample_normal.pdf")
    create_sample_pdf("sample_encrypted.pdf", password="30002154")
