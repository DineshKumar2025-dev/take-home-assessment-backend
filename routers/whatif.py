from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter()

# Same funnel assumption used elsewhere (branches.py, sales_reps.py, lead_aging.py).
# Verify against `SELECT DISTINCT status FROM leads;` — if your data uses different
# stage names, update this list and the transitions below to match.
STAGE_ORDER = ["new", "contacted", "test_drive", "negotiation", "order", "delivered"]


@router.get("/api/what-if")
def get_what_if_baseline(db: Session = Depends(get_db)):

    # How many leads ever REACHED each stage (based on status_history),
    # not just how many are CURRENTLY sitting in that stage. This is what
    # a funnel conversion calculation actually needs — current `status`
    # only tells you where a lead is now, not what it passed through.
    stage_reach_counts = {}
    for stage in STAGE_ORDER:
        count = db.execute(text("""
            SELECT COUNT(DISTINCT lead_id)
            FROM lead_status_history
            WHERE status = :stage
        """), {"stage": stage}).scalar()
        stage_reach_counts[stage] = count or 0

    # Conversion rate from each stage to the next
    transitions = []
    for i in range(len(STAGE_ORDER) - 1):
        from_stage = STAGE_ORDER[i]
        to_stage = STAGE_ORDER[i + 1]
        from_count = stage_reach_counts[from_stage]
        to_count = stage_reach_counts[to_stage]
        rate = round(to_count / from_count * 100, 1) if from_count else None
        transitions.append({
            "from_stage": from_stage,
            "to_stage": to_stage,
            "from_count": from_count,
            "to_count": to_count,
            "conversion_rate_pct": rate,
        })

    # Revenue baseline
    revenue_row = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
            SUM(deal_value) FILTER (WHERE status = 'delivered') AS total_revenue,
            AVG(deal_value) FILTER (WHERE status = 'delivered') AS avg_deal_value
        FROM leads
    """)).mappings().first()

    delivered = revenue_row["delivered"] or 0
    total_revenue = float(revenue_row["total_revenue"]) if revenue_row["total_revenue"] else 0
    avg_deal_value = float(revenue_row["avg_deal_value"]) if revenue_row["avg_deal_value"] else 0

    return {
        "stage_reach_counts": stage_reach_counts,
        "transitions": transitions,
        "delivered": delivered,
        "total_revenue": total_revenue,
        "avg_deal_value": avg_deal_value,
    }