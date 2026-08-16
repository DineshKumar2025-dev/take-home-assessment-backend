from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter()


def resolve_time_range(range_name: Optional[str]):

    if range_name in (None, "all"):
        return None, None
    if range_name == "q4-2025":
        return date(2025, 10, 1), date(2025, 12, 31)
    return None, None



@router.get("/api/branches/list")
def get_branches_list(db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT id, name FROM branches ORDER BY name")).mappings().all()
    return {"branches": [dict(r) for r in rows]}

@router.get("/api/sales-reps")
def get_sales_reps(
    branch_id: Optional[str] = Query(None, description="Filter to one branch, omit for all"),
    db: Session = Depends(get_db),
):
    query = text("""
        SELECT
            sr.id AS rep_id,
            sr.name,
            sr.role,
            sr.branch_id,
            b.name AS branch_name,
            COUNT(l.id) AS total_leads,
            COUNT(l.id) FILTER (WHERE l.status = 'delivered') AS delivered,
            SUM(l.deal_value) FILTER (WHERE l.status = 'delivered') AS revenue
        FROM sales_reps sr
        JOIN branches b ON b.id = sr.branch_id
        LEFT JOIN leads l ON l.assigned_to = sr.id
        WHERE (:branch_id IS NULL OR sr.branch_id = :branch_id)
        GROUP BY sr.id, sr.name, sr.role, sr.branch_id, b.name
        ORDER BY revenue DESC NULLS LAST
    """)

    rows = db.execute(query, {"branch_id": branch_id}).mappings().all()

    reps = [{
        "rep_id": r["rep_id"],
        "name": r["name"],
        "role": r["role"],
        "branch_id": r["branch_id"],
        "branch_name": r["branch_name"],
        "total_leads": r["total_leads"],
        "delivered": r["delivered"],
        "revenue": float(r["revenue"]) if r["revenue"] else 0,
        "conversion_rate_pct": round(r["delivered"] / r["total_leads"] * 100, 1) if r["total_leads"] else None,
    } for r in rows]

    return {"sales_reps": reps}
