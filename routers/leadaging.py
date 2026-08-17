from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter()

# Bucket boundaries in days since last_activity_at, for leads still active
# (not delivered/lost). Tune these once you see the real distribution.
AGE_BUCKETS = [
    ("0-3d", 0, 3),
    ("4-7d", 4, 7),
    ("8-14d", 8, 14),
    ("15d+", 15, None),
]


def resolve_time_range(range_name: Optional[str]):
    if range_name in (None, "all"):
        return None, None
    if range_name == "q3-2025":
        return date(2025, 7, 1), date(2025, 9, 30)
    if range_name == "q4-2025":
        return date(2025, 10, 1), date(2025, 12, 31)
    if range_name.startswith("month-"):
        month_value = range_name.split("month-")[1]
        try:
            year = int(month_value[:4])
            month = int(month_value[5:7])
            start = date(year, month, 1)
            end = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
            return start, end
        except (ValueError, IndexError):
            return None, None
    return None, None


@router.get("/api/lead-aging")
def get_lead_aging(
    db: Session = Depends(get_db),
    range: Optional[str] = Query(None, alias="range"),
):
    start_date, end_date = resolve_time_range(range)

    reference_time = db.execute(text("SELECT MAX(last_activity_at) FROM leads")).scalar()
    reference_time = reference_time or datetime.utcnow()

    where_clause = "WHERE l.status NOT IN ('delivered', 'lost')"
    params = {"ref_time": reference_time}
    if start_date and end_date:
        where_clause += " AND l.created_at >= :start_date AND l.created_at < :end_date"
        params["start_date"] = start_date
        params["end_date"] = end_date + timedelta(days=1)

    rows = db.execute(text(f"""
        SELECT
            l.id AS lead_id,
            l.customer_name,
            l.status,
            l.branch_id,
            b.name AS branch_name,
            l.assigned_to,
            sr.name AS rep_name,
            sr.id AS rep_id,
            l.last_activity_at,
            l.model_interested,
            l.deal_value,
            EXTRACT(DAY FROM (:ref_time - l.last_activity_at)) AS days_stale
        FROM leads l
        JOIN branches b ON b.id = l.branch_id
        LEFT JOIN sales_reps sr ON sr.id = l.assigned_to
        {where_clause}
        ORDER BY l.last_activity_at ASC
    """), params).mappings().all()

    leads = [{
        "lead_id": r["lead_id"],
        "customer_name": r["customer_name"],
        "status": r["status"],
        "branch_id": r["branch_id"],
        "branch_name": r["branch_name"],
        "rep_name": r["rep_name"] or "Unassigned",
        "rep_id": str(r["rep_id"]), 
        "model_interested": r["model_interested"],
        "deal_value": float(r["deal_value"]) if r["deal_value"] else None,
        "last_activity_at": (
            r["last_activity_at"].isoformat()
            if r["last_activity_at"]
            else None
        ),
        "days_stale": int(r["days_stale"]),
    } for r in rows]

    # Bucket counts
    buckets = []
    for label, low, high in AGE_BUCKETS:
        if high is None:
            count = sum(1 for l in leads if l["days_stale"] >= low)
        else:
            count = sum(1 for l in leads if low <= l["days_stale"] <= high)
        buckets.append({"label": label, "count": count})

    # Per-branch stale breakdown (7+ days = "going cold")
    stale_leads = [l for l in leads if l["days_stale"] >= 7]
    branch_stale_counts = {}
    for l in stale_leads:
        branch_stale_counts.setdefault(l["branch_name"], 0)
        branch_stale_counts[l["branch_name"]] += 1
    branch_breakdown = [{"branch_name": k, "stale_count": v} for k, v in
                         sorted(branch_stale_counts.items(), key=lambda x: -x[1])]

    return {
        "total_active_leads": len(leads),
        "stale_count": len(stale_leads),
        "buckets": buckets,
        "branch_breakdown": branch_breakdown,
        "leads": leads,
    }