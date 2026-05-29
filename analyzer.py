from collections import Counter, defaultdict
import re

def analyze_statement(transactions: list) -> dict:
    """
    Computes all analysis metrics from the list of transaction dictionaries.
    """
    if not transactions:
        return {}

    inflows = [t for t in transactions if t["amount"] > 0]
    outflows = [t for t in transactions if t["amount"] < 0]

    total_inflow = sum(t["amount"] for t in inflows)
    total_outflow = sum(t["amount"] for t in outflows)

    # ── Monthly grouping ──────────────────────────────────────────────────────
    monthly_inflows_sums  = defaultdict(float)
    monthly_outflows_sums = defaultdict(float)
    dated_months = set()

    for t in transactions:
        if t["date"]:
            month_key = t["date"].strftime("%Y-%m")
            dated_months.add(month_key)
            if t["amount"] > 0:
                monthly_inflows_sums[month_key]  += t["amount"]
            else:
                monthly_outflows_sums[month_key] += t["amount"]

    # Use the full span of months in the statement (first month → last month inclusive)
    # so that months with zero activity are still counted in the denominator.
    if dated_months:
        sorted_months = sorted(dated_months)
        first_yr, first_mo = int(sorted_months[0][:4]),  int(sorted_months[0][5:])
        last_yr,  last_mo  = int(sorted_months[-1][:4]), int(sorted_months[-1][5:])
        span_months = (last_yr - first_yr) * 12 + (last_mo - first_mo) + 1
    else:
        span_months = 1

    avg_monthly_inflow  = total_inflow  / span_months
    avg_monthly_outflow = total_outflow / span_months


    # Largest Incoming & Outgoing
    largest_incoming = max(inflows, key=lambda x: x["amount"]) if inflows else None
    largest_outgoing = min(outflows, key=lambda x: x["amount"]) if outflows else None

    # Balance stats
    balances = [t["balance"] for t in transactions]
    lowest_balance = min(balances) if balances else 0.0
    highest_balance = max(balances) if balances else 0.0
    average_balance = (sum(balances) / len(balances)) if balances else 0.0

    def extract_name(details, tx_type):
        details = str(details).strip()
        if tx_type == "Send Money":
            match = re.search(r"to\s+254\d{9}\s*-\s*(.*)", details, re.IGNORECASE)
            if match:
                return match.group(1).strip()
            return details.replace("Customer Transfer to", "").strip()
        elif tx_type == "Paybill":
            match = re.search(r"to\s+\d+\s*-\s*(.*)", details, re.IGNORECASE)
            if match:
                return match.group(1).strip()
            return details.replace("Pay Bill to", "").replace("Pay Bill Online to", "").strip()
        elif tx_type == "Buy Goods":
            match = re.search(r"to\s+\d+\s*-\s*(.*)", details, re.IGNORECASE)
            if match:
                return match.group(1).strip()
            return details.replace("Merchant Payment to", "").strip()
        return details

    # Frequencies using Counters
    paybills_counter = Counter()
    paybills_totals = defaultdict(float)
    
    merchants_counter = Counter()
    merchants_totals = defaultdict(float)
    
    recipients_counter = Counter()
    recipients_totals = defaultdict(float)

    for t in transactions:
        tx_type = t["type"]
        clean_name = extract_name(t["details"], tx_type)
        amt_abs = abs(t["amount"])
        
        if tx_type == "Paybill":
            paybills_counter[clean_name] += 1
            paybills_totals[clean_name] += amt_abs
        elif tx_type == "Buy Goods":
            merchants_counter[clean_name] += 1
            merchants_totals[clean_name] += amt_abs
        elif tx_type == "Send Money":
            recipients_counter[clean_name] += 1
            recipients_totals[clean_name] += amt_abs

    def format_frequency(counter, totals):
        res = []
        for name, count in counter.most_common(5):
            res.append({
                "clean_name": name,
                "count": count,
                "total": totals[name]
            })
        return res

    return {
        "total_inflow": total_inflow,
        "total_outflow": total_outflow,
        "avg_monthly_inflow": avg_monthly_inflow,
        "avg_monthly_outflow": avg_monthly_outflow,
        "largest_incoming": {
            "receipt_no": largest_incoming["receipt_no"] if largest_incoming else "N/A",
            "amount": largest_incoming["amount"] if largest_incoming else 0.0,
            "details": largest_incoming["details"] if largest_incoming else "N/A",
            "date": str(largest_incoming["date"]) if largest_incoming else "N/A"
        },
        "largest_outgoing": {
            "receipt_no": largest_outgoing["receipt_no"] if largest_outgoing else "N/A",
            "amount": largest_outgoing["amount"] if largest_outgoing else 0.0,
            "details": largest_outgoing["details"] if largest_outgoing else "N/A",
            "date": str(largest_outgoing["date"]) if largest_outgoing else "N/A"
        },
        "lowest_balance": lowest_balance,
        "highest_balance": highest_balance,
        "average_balance": average_balance,
        "top_paybills": format_frequency(paybills_counter, paybills_totals),
        "top_merchants": format_frequency(merchants_counter, merchants_totals),
        "top_recipients": format_frequency(recipients_counter, recipients_totals)
    }
