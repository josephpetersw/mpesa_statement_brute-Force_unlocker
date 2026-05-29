import re
from datetime import datetime

def extract_metadata(text: str) -> dict:
    """
    Extracts customer info and statement metadata from text.
    """
    metadata = {
        "customer_name": "N/A",
        "mobile_number": "N/A",
        "email_address": "N/A",
        "statement_period": "N/A",
        "request_date": "N/A"
    }
    
    name_patterns = [
        r"Customer Name\s*:\s*([^\n\r]+)",
        r"Name\s*:\s*([^\n\r]+)",
        r"Customer\s*:\s*([^\n\r]+)"
    ]
    for pattern in name_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            metadata["customer_name"] = match.group(1).strip()
            break
            
    phone_patterns = [
        r"Mobile Number\s*:\s*([^\n\r]+)",
        r"Phone Number\s*:\s*([^\n\r]+)",
        r"MSISDN\s*:\s*([^\n\r]+)",
        r"254\d{9}"
    ]
    for pattern in phone_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = match.group(0) if ":" not in pattern else match.group(1)
            metadata["mobile_number"] = val.strip()
            break

    email_patterns = [
        r"Email\s*(?:Address)?\s*:\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
        r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b"
    ]
    for pattern in email_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = match.group(0) if ":" not in pattern else match.group(1)
            metadata["email_address"] = val.strip()
            break

    period_patterns = [
        r"Statement Period\s*:\s*([^\n\r]+)",
        r"Period\s*:\s*([^\n\r]+)",
        r"Duration\s*:\s*([^\n\r]+)"
    ]
    for pattern in period_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            metadata["statement_period"] = match.group(1).strip()
            break

    req_date_patterns = [
        r"Request Date\s*:\s*([^\n\r]+)",
        r"Date of Request\s*:\s*([^\n\r]+)",
        r"Generated on\s*:\s*([^\n\r]+)",
        r"Date\s*:\s*([^\n\r]+)"
    ]
    for pattern in req_date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            metadata["request_date"] = match.group(1).strip()
            break
            
    return metadata

def parse_summary_totals(text: str) -> dict:
    """
    Parses summary totals. Supports optional colons and spaces.
    """
    summary = {
        "Send Money": 0.0,
        "Received Money": 0.0,
        "Agent Deposit": 0.0,
        "Agent Withdrawal": 0.0,
        "Paybill": 0.0,
        "Buy Goods": 0.0,
        "Others": 0.0,
        "Total": 0.0
    }
    
    patterns = {
        "Send Money": [r"(?:Send Money|Customer Transfer Out|Sent Money)\s*:?\s*[\-\+]?\s*([\d,]+\.?\d*)"],
        "Received Money": [r"(?:Received Money|Customer Transfer In|Received)\s*:?\s*[\-\+]?\s*([\d,]+\.?\d*)"],
        "Agent Deposit": [r"(?:Agent Deposit|Deposit via Agent)\s*:?\s*[\-\+]?\s*([\d,]+\.?\d*)"],
        "Agent Withdrawal": [r"(?:Agent Withdrawal|Withdrawal via Agent)\s*:?\s*[\-\+]?\s*([\d,]+\.?\d*)"],
        "Paybill": [r"(?:Paybill|Pay Bill|Pay Utility|Bill Payment)\s*:?\s*[\-\+]?\s*([\d,]+\.?\d*)"],
        "Buy Goods": [r"(?:Buy Goods|Merchant Payment|Till Payment)\s*:?\s*[\-\+]?\s*([\d,]+\.?\d*)"],
        "Others": [r"(?:Others|Other Transactions|Fees/Charges)\s*:?\s*[\-\+]?\s*([\d,]+\.?\d*)"],
        "Total": [r"(?:Total Transactions|Total)\s*:?\s*[\-\+]?\s*([\d,]+\.?\d*)"]
    }
    
    for key, regexes in patterns.items():
        for r in regexes:
            match = re.search(r, text, re.IGNORECASE)
            if match:
                try:
                    summary[key] = float(match.group(1).replace(",", ""))
                    break
                except ValueError:
                    pass
    
    return summary

def clean_amount(val) -> float:
    if not val:
        return 0.0
    val_str = str(val).strip().replace(",", "")
    if not val_str:
        return 0.0
    if val_str.startswith("(") and val_str.endswith(")"):
        val_str = "-" + val_str[1:-1]
    
    # Extract only the last valid float or integer from the string to prevent concatenating unrelated numbers
    matches = re.findall(r"(-?\d+\.?\d*)", val_str)
    if matches:
        try:
            return float(matches[-1])
        except ValueError:
            pass

    try:
        return float(val_str)
    except ValueError:
        return 0.0

def classify_transaction(details: str) -> str:
    """
    Classifies a transaction based on the Details text.
    """
    details_lower = details.lower()
    
    if "pay bill" in details_lower or "paybill" in details_lower or "utility" in details_lower:
        return "Paybill"
    elif "merchant payment" in details_lower or "buy goods" in details_lower or "lipa na m-pesa" in details_lower:
        return "Buy Goods"
    elif "customer transfer to" in details_lower or "send money" in details_lower or "sent to" in details_lower:
        return "Send Money"
    elif "customer transfer receive" in details_lower or "received from" in details_lower or "transfer from" in details_lower:
        return "Received Money"
    elif "agent deposit" in details_lower or "deposit of" in details_lower:
        return "Agent Deposit"
    elif "agent withdrawal" in details_lower or "withdraw to agent" in details_lower or "withdrawal at" in details_lower:
        return "Agent Withdrawal"
    elif "airtime" in details_lower:
        return "Airtime Purchase"
    elif "m-shwari" in details_lower:
        return "M-Shwari"
    else:
        return "Others"

def parse_date(dt_str: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(dt_str.strip(), fmt)
        except Exception:
            pass
    return None

def parse_transactions(tables: list, raw_text: str = "") -> list:
    """
    Parses tables extracted from pdfplumber into a list of transaction dictionaries,
    with an advanced line-by-line fallback scanner for PyMuPDF text layouts.
    """
    rows = []
    seen_receipts = set()
    tx_id_pattern = re.compile(r"^[A-Z0-9]{10}$")
    
    # Helper to add transaction
    def add_transaction(receipt_no, completion_time, details, amount, balance):
        if receipt_no in seen_receipts:
            return
        seen_receipts.add(receipt_no)
        rows.append({
            "receipt_no": receipt_no,
            "completion_time": completion_time,
            "details": details,
            "amount": clean_amount(amount),
            "balance": clean_amount(balance)
        })

    # 1. Process pdfplumber tables
    for table in tables:
        if not table or len(table) < 2:
            continue
            
        header = None
        for r_idx, row in enumerate(table):
            row_str = " ".join([str(cell) for cell in row if cell])
            if "receipt" in row_str.lower() and ("completion" in row_str.lower() or "date" in row_str.lower()) and "details" in row_str.lower():
                header = [str(c).strip().lower() for c in row]
                table_rows = table[r_idx+1:]
                break
        else:
            table_rows = table
            
        for row in table_rows:
            if not row or len(row) < 3:
                continue
                
            receipt_no = str(row[0]).strip() if row[0] else ""
            if not tx_id_pattern.match(receipt_no):
                found = False
                for cell in row:
                    cell_str = str(cell).strip()
                    if tx_id_pattern.match(cell_str):
                        receipt_no = cell_str
                        found = True
                        break
                if not found:
                    continue
                    
            completion_time = "N/A"
            details = "N/A"
            amount = 0.0
            balance = 0.0
            
            if len(row) >= 5:
                completion_time = str(row[1]).strip() if row[1] else "N/A"
                details = str(row[2]).strip() if row[2] else "N/A"
                
                if len(row) >= 6 and header and any("paid" in str(c).lower() or "received" in str(c).lower() for c in header):
                    paid_in_idx = -1
                    paid_out_idx = -1
                    balance_idx = -1
                    for idx, col in enumerate(header):
                        if "paid in" in col or "received" in col:
                            paid_in_idx = idx
                        elif "paid out" in col or "sent" in col or "paid to" in col:
                            paid_out_idx = idx
                        elif "balance" in col:
                            balance_idx = idx
                            
                    if paid_in_idx != -1 and paid_out_idx != -1:
                        in_val = clean_amount(row[paid_in_idx])
                        out_val = clean_amount(row[paid_out_idx])
                        amount = in_val if in_val > 0 else -out_val
                        balance = clean_amount(row[balance_idx]) if balance_idx != -1 else 0.0
                    else:
                        val1 = clean_amount(row[-2])
                        val2 = clean_amount(row[-1])
                        balance = val2
                        amount = val1
                else:
                    nums = []
                    for idx in range(3, len(row)):
                        val = clean_amount(row[idx])
                        if val != 0.0:
                            nums.append((idx, val))
                    if len(nums) >= 2:
                        amount = nums[-2][1]
                        balance = nums[-1][1]
                    elif len(nums) == 1:
                        balance = nums[0][1]
                        amount = 0.0
            else:
                details = str(row[2]).strip() if len(row) > 2 else "N/A"
            
            add_transaction(receipt_no, completion_time, details, amount, balance)
            
    # 2. Advanced line-by-line fallback scanner for PyMuPDF text layouts
    if raw_text:
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            if tx_id_pattern.match(line):
                receipt_no = line
                if idx + 1 < len(lines) and parse_date(lines[idx+1]):
                    completion_time = lines[idx+1]
                    
                    details = ""
                    amount = "0.0"
                    balance = "0.0"
                    
                    look_idx = idx + 2
                    sub_lines = []
                    while look_idx < len(lines) and not tx_id_pattern.match(lines[look_idx]):
                        sub_lines.append(lines[look_idx])
                        look_idx += 1
                        
                    if len(sub_lines) >= 2:
                        balance = sub_lines[-1]
                        amount_cand = sub_lines[-2]
                        
                        # Use clean_amount with exact matching
                        # If there is a number at the end of the second-last line, extract it.
                        match_num = re.search(r"(-?\d+\.?\d*)\s*$", amount_cand)
                        if match_num:
                            amount = match_num.group(1)
                            # Remove the matched amount from details candidate
                            det_cand = amount_cand[:match_num.start()].strip()
                            details = (" ".join(sub_lines[:-2]) + " " + det_cand).strip()
                        else:
                            details = " ".join(sub_lines[:-1])
                            amount = "0.0"
                    elif len(sub_lines) == 1:
                        details = sub_lines[0]
                        
                    add_transaction(receipt_no, completion_time, details, amount, balance)
                    idx = look_idx - 1
            idx += 1

    # Post processing
    for r in rows:
        r["date"] = parse_date(r["completion_time"])
        r["type"] = classify_transaction(r["details"])

    # Apply sign corrections for outflows
    has_negatives = any(r["amount"] < 0 for r in rows)
    if not has_negatives:
        outflow_types = ["Paybill", "Buy Goods", "Send Money", "Agent Withdrawal", "Airtime Purchase"]
        for r in rows:
            if r["type"] in outflow_types:
                r["amount"] = -abs(r["amount"])
            else:
                r["amount"] = abs(r["amount"])

    # Sort rows by date
    rows.sort(key=lambda x: x["date"] if x["date"] else datetime.min)
    return rows
