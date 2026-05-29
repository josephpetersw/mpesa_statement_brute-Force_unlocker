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

    # Calculate transaction fees
    total_fees = 0.0
    fee_pattern = re.compile(r"charge|fee|interest|tax|levy|commission|cost", re.IGNORECASE)
    for t in transactions:
        if t["amount"] < 0:
            if fee_pattern.search(t["details"]) or t["type"] == "Others":
                total_fees += abs(t["amount"])

    # Calculate net savings and savings rate
    net_savings = total_inflow + total_outflow
    savings_rate = (net_savings / total_inflow * 100) if total_inflow > 0 else 0.0

    # ── Negative Indicators ──
    # 1. Transaction Gaps (Dormant Periods)
    sorted_txs = sorted([t for t in transactions if t["date"]], key=lambda x: x["date"])
    longest_gap_days = 0.0
    longest_gap_start = None
    longest_gap_end = None
    
    for i in range(len(sorted_txs) - 1):
        t1 = sorted_txs[i]["date"]
        t2 = sorted_txs[i+1]["date"]
        gap = (t2 - t1).total_seconds() / 86400.0  # gap in days
        if gap > longest_gap_days:
            longest_gap_days = gap
            longest_gap_start = t1
            longest_gap_end = t2

    # 2. Overdraft (Fuliza) & Debt Reliance
    overdraft_events = 0
    overdraft_total_volume = 0.0
    repayments_count = 0
    repayments_total_volume = 0.0
    
    for t in transactions:
        det_lower = t["details"].lower()
        if any(w in det_lower for w in ("fuliza", "overdraft", "overdraw", "od loan")):
            if t["amount"] > 0:
                overdraft_events += 1
                overdraft_total_volume += t["amount"]
            else:
                repayments_count += 1
                repayments_total_volume += abs(t["amount"])

    # 3. Net Deficit Months
    negative_months = []
    for month_key in sorted(dated_months):
        in_sum = monthly_inflows_sums.get(month_key, 0.0)
        out_sum = abs(monthly_outflows_sums.get(month_key, 0.0))
        if out_sum > in_sum:
            deficit = out_sum - in_sum
            negative_months.append({
                "month": month_key,
                "inflow": in_sum,
                "outflow": out_sum,
                "deficit": deficit
            })

    # 4. Peak Outflow Day
    daily_outflows = defaultdict(float)
    for t in transactions:
        if t["amount"] < 0 and t["date"]:
            day_key = t["date"].strftime("%Y-%m-%d")
            daily_outflows[day_key] += abs(t["amount"])
            
    peak_outflow_day = "N/A"
    peak_outflow_amount = 0.0
    if daily_outflows:
        peak_outflow_day = max(daily_outflows, key=daily_outflows.get)
        peak_outflow_amount = daily_outflows[peak_outflow_day]

    return {
        "total_inflow": total_inflow,
        "total_outflow": total_outflow,
        "avg_monthly_inflow": avg_monthly_inflow,
        "avg_monthly_outflow": avg_monthly_outflow,
        "total_fees": total_fees,
        "net_savings": net_savings,
        "savings_rate": savings_rate,
        "total_txs": len(transactions),
        "inflow_count": len(inflows),
        "outflow_count": len(outflows),
        
        # Negative Indicators
        "longest_gap_days": longest_gap_days,
        "longest_gap_start": str(longest_gap_start.strftime("%Y-%m-%d %H:%M")) if longest_gap_start else "N/A",
        "longest_gap_end": str(longest_gap_end.strftime("%Y-%m-%d %H:%M")) if longest_gap_end else "N/A",
        "overdraft_events": overdraft_events,
        "overdraft_total_volume": overdraft_total_volume,
        "repayments_count": repayments_count,
        "repayments_total_volume": repayments_total_volume,
        "negative_months": negative_months,
        "peak_outflow_day": peak_outflow_day,
        "peak_outflow_amount": peak_outflow_amount,
        
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
