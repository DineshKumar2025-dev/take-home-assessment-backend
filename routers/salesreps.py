from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db


from fastapi import HTTPException

router = APIRouter()


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
            if month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)
            return start, end
        except (ValueError, IndexError):
            return None, None
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



@router.get("/api/sales-reps/{rep_id}")
def get_sales_rep_detail(
    rep_id: str,
    range: Optional[str] = Query(None, alias="range", description="all | 30d | 90d | q4-2025"),
    db: Session = Depends(get_db),
):
    start_date = None
    end_date = None
    if range not in (None, "all"):
        start_date, end_date = resolve_time_range(range)

    rep_row = db.execute(text("""
        SELECT
            sr.id AS rep_id,
            sr.name,
            sr.role,
            sr.branch_id,
            sr.joined,
            b.name AS branch_name,
            b.city
        FROM sales_reps sr
        JOIN branches b ON b.id = sr.branch_id
        WHERE sr.id = :rep_id
    """), {"rep_id": rep_id}).mappings().first()

    if not rep_row:
        raise HTTPException(status_code=404, detail="Sales rep not found")

    # Summary stats
    stats_row = db.execute(text("""
        SELECT
            COUNT(*) AS total_leads,
            COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
            SUM(deal_value) FILTER (WHERE status = 'delivered') AS revenue
        FROM leads
        WHERE assigned_to = :rep_id
          AND (:start_date IS NULL OR created_at >= :start_date)
          AND (:end_date IS NULL OR created_at <= :end_date)
    """), {"rep_id": rep_id, "start_date": start_date, "end_date": end_date}).mappings().first()

    total_leads = stats_row["total_leads"]
    delivered = stats_row["delivered"]
    revenue = float(stats_row["revenue"]) if stats_row["revenue"] else 0

    # Monthly trend for this rep
    monthly = db.execute(text("""
        SELECT
            DATE_TRUNC('month', created_at)::date AS month,
            COUNT(*) AS total_leads,
            COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
            SUM(deal_value) FILTER (WHERE status = 'delivered') AS revenue
        FROM leads
        WHERE assigned_to = :rep_id
          AND (:start_date IS NULL OR created_at >= :start_date)
          AND (:end_date IS NULL OR created_at <= :end_date)
        GROUP BY 1
        ORDER BY 1
    """), {"rep_id": rep_id, "start_date": start_date, "end_date": end_date}).mappings().all()

    monthly_trend = [{
        "month": m["month"].strftime("%Y-%m"),
        "total_leads": m["total_leads"],
        "delivered": m["delivered"],
        "revenue": float(m["revenue"]) if m["revenue"] else 0,
    } for m in monthly]

    # This rep's individual leads (for a detail table)
    leads = db.execute(text("""
        SELECT
            id AS lead_id,
            customer_name,
            source,
            model_interested,
            status,
            created_at,
            last_activity_at,
            deal_value
        FROM leads
        WHERE assigned_to = :rep_id
          AND (:start_date IS NULL OR created_at >= :start_date)
          AND (:end_date IS NULL OR created_at <= :end_date)
        ORDER BY created_at DESC
    """), {"rep_id": rep_id, "start_date": start_date, "end_date": end_date}).mappings().all()

    lead_list = [{
        "lead_id": l["lead_id"],
        "customer_name": l["customer_name"],
        "source": l["source"],
        "model_interested": l["model_interested"],
        "status": l["status"],
        "created_at": l["created_at"].isoformat(),
        "last_activity_at": l["last_activity_at"].isoformat(),
        "deal_value": float(l["deal_value"]) if l["deal_value"] else None,
    } for l in leads]

    # --- Lead sources ---
    sources = db.execute(text("""
        SELECT
            source,
            COUNT(*) AS total_leads,
            COUNT(*) FILTER (WHERE status = 'delivered') AS delivered
        FROM leads
        WHERE assigned_to = :rep_id
          AND (:start_date IS NULL OR created_at >= :start_date)
          AND (:end_date IS NULL OR created_at <= :end_date)
        GROUP BY source
        ORDER BY total_leads DESC
    """), {"rep_id": rep_id, "start_date": start_date, "end_date": end_date}).mappings().all()

    lead_sources = [{
        "source": s["source"],
        "total_leads": s["total_leads"],
        "delivered": s["delivered"],
        "conversion_rate_pct": round(s["delivered"] / s["total_leads"] * 100, 1) if s["total_leads"] else None,
    } for s in sources]

    # --- Funnel (current status distribution) ---
    # Same STAGE_ORDER assumption as branch detail — verify against
    # `SELECT DISTINCT status FROM leads;` if this comes back all zeros.
    STAGE_ORDER = ["new", "contacted", "test_drive", "negotiation", "order", "delivered"]

    funnel_rows = db.execute(text("""
        SELECT status, COUNT(*) AS count
        FROM leads
        WHERE assigned_to = :rep_id
          AND (:start_date IS NULL OR created_at >= :start_date)
          AND (:end_date IS NULL OR created_at <= :end_date)
        GROUP BY status
    """), {"rep_id": rep_id, "start_date": start_date, "end_date": end_date}).mappings().all()

    counts_by_status = {r["status"]: r["count"] for r in funnel_rows}
    funnel = [
        {"stage": stage, "count": counts_by_status.get(stage, 0)}
        for stage in STAGE_ORDER
    ]

    # --- Lost reasons ---
    lost_rows = db.execute(text("""
        SELECT lost_reason, COUNT(*) AS count
        FROM leads
        WHERE assigned_to = :rep_id
          AND status = 'lost'
          AND lost_reason IS NOT NULL
          AND (:start_date IS NULL OR created_at >= :start_date)
          AND (:end_date IS NULL OR created_at <= :end_date)
        GROUP BY lost_reason
        ORDER BY count DESC
    """), {"rep_id": rep_id, "start_date": start_date, "end_date": end_date}).mappings().all()

    lost_reasons = [{"reason": r["lost_reason"], "count": r["count"]} for r in lost_rows]

    return {
        "rep": dict(rep_row),
        "total_leads": total_leads,
        "delivered": delivered,
        "revenue": revenue,
        "conversion_rate_pct": round(delivered / total_leads * 100, 1) if total_leads else None,
        "monthly_trend": monthly_trend,
        "lead_sources": lead_sources,
        "funnel": funnel,
        "lost_reasons": lost_reasons,
        "leads": lead_list,
    }