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


@router.get("/api/overview")
def get_overview(
    start_date: Optional[date] = Query(None, description="Filter leads created_at >= this date"),
    end_date: Optional[date] = Query(None, description="Filter leads created_at <= this date"),
    month: Optional[str] = Query(None, description="YYYY-MM-01, filters targets to a single month"),
    range: Optional[str] = Query(None, alias="range", description="all | q3-2025 | q4-2025 | month-2025-06 ... month-2025-12"),
    db: Session = Depends(get_db),
):
    if start_date is None and end_date is None and range not in (None, "all"):
        start_date, end_date = resolve_time_range(range)

    query = text("""
        WITH lead_stats AS (
            SELECT
                branch_id,
                COUNT(*) AS total_leads,
                COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
                SUM(deal_value) FILTER (WHERE status = 'delivered') AS actual_revenue
            FROM leads
            WHERE (:start_date IS NULL OR created_at >= :start_date)
              AND (:end_date IS NULL OR created_at <= :end_date)
            GROUP BY branch_id
        ),
        delivery_stats AS (
            SELECT
                l.branch_id,
                AVG(d.days_to_deliver) AS avg_days_to_deliver
            FROM deliveries d
            JOIN leads l ON l.id = d.lead_id
            WHERE (:start_date IS NULL OR d.delivery_date >= :start_date)
              AND (:end_date IS NULL OR d.delivery_date <= :end_date)
            GROUP BY l.branch_id
        ),
        target_stats AS (
            SELECT
                branch_id,
                SUM(target_units) AS target_units,
                SUM(target_revenue) AS target_revenue
            FROM targets
            WHERE (:month IS NULL OR month = CAST(:month AS DATE))
            GROUP BY branch_id
        )
        SELECT
            b.id AS branch_id,
            b.name,
            b.city,
            COALESCE(ls.total_leads, 0) AS total_leads,
            COALESCE(ls.delivered, 0) AS delivered,
            COALESCE(ls.actual_revenue, 0) AS actual_revenue,
            ds.avg_days_to_deliver AS avg_days_to_deliver,
            COALESCE(ts.target_units, 0) AS target_units,
            COALESCE(ts.target_revenue, 0) AS target_revenue
        FROM branches b
        LEFT JOIN lead_stats ls ON ls.branch_id = b.id
        LEFT JOIN delivery_stats ds ON ds.branch_id = b.id
        LEFT JOIN target_stats ts ON ts.branch_id = b.id
        ORDER BY b.id
    """)

    result = db.execute(
        query,
        {"start_date": start_date, "end_date": end_date, "month": month},
    )
    rows = [dict(row._mapping) for row in result]

    branches = []
    for r in rows:
        total_leads = r["total_leads"]
        delivered = r["delivered"]
        target_units = r["target_units"]
        target_revenue = float(r["target_revenue"]) if r["target_revenue"] else 0
        actual_revenue = float(r["actual_revenue"]) if r["actual_revenue"] else 0

        branches.append({
            "branch_id": r["branch_id"],
            "name": r["name"],
            "city": r["city"],
            "total_leads": total_leads,
            "delivered": delivered,
            "actual_units": delivered,
            "actual_revenue": actual_revenue,
            "conversion_rate_pct": round(delivered / total_leads * 100, 1) if total_leads else None,
            "target_units": target_units,
            "target_revenue": target_revenue,
            "revenue_attainment_pct": round(actual_revenue / target_revenue * 100, 1) if target_revenue else None,
            "avg_days_to_deliver": round(r["avg_days_to_deliver"], 1) if r["avg_days_to_deliver"] is not None else None,
        })

    return {"branches": branches}









@router.get("/api/branches/{branch_id}")
def get_branch_detail(
    branch_id: str,
    range: Optional[str] = Query(None, alias="range", description="all  q4-2025"),
    db: Session = Depends(get_db),
):
    start_date = None
    end_date = None
    if range not in (None, "all"):
        start_date, end_date = resolve_time_range(range)

    # --- Branch header (reuse same shape as overview) ---
    branch_row = db.execute(
        text("SELECT id AS branch_id, name, city FROM branches WHERE id = :bid"),
        {"bid": branch_id},
    ).mappings().first()

    if not branch_row:
        return {"error": "not found"}, 404

    # --- Branch summary ---
    branch_summary = db.execute(text("""
        WITH lead_stats AS (
            SELECT
                COUNT(*) AS total_leads,
                COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
                SUM(deal_value) FILTER (WHERE status = 'delivered') AS actual_revenue
            FROM leads
            WHERE branch_id = :bid
              AND (:start_date IS NULL OR created_at >= :start_date)
              AND (:end_date IS NULL OR created_at <= :end_date)
        ),
        delivery_stats AS (
            SELECT AVG(d.days_to_deliver) AS avg_days_to_deliver
            FROM deliveries d
            JOIN leads l ON l.id = d.lead_id
            WHERE l.branch_id = :bid
              AND (:start_date IS NULL OR d.delivery_date >= :start_date)
              AND (:end_date IS NULL OR d.delivery_date <= :end_date)
        ),
        target_stats AS (
            SELECT
                SUM(target_units) AS target_units,
                SUM(target_revenue) AS target_revenue
            FROM targets
            WHERE branch_id = :bid
        )
        SELECT
            COALESCE(ls.total_leads, 0) AS total_leads,
            COALESCE(ls.delivered, 0) AS delivered,
            COALESCE(ls.actual_revenue, 0) AS actual_revenue,
            ds.avg_days_to_deliver AS avg_days_to_deliver,
            COALESCE(ts.target_units, 0) AS target_units,
            COALESCE(ts.target_revenue, 0) AS target_revenue
        FROM lead_stats ls
        CROSS JOIN delivery_stats ds
        CROSS JOIN target_stats ts
    """), {"bid": branch_id, "start_date": start_date, "end_date": end_date}).mappings().first()

    summary = {
        "total_leads": branch_summary["total_leads"] if branch_summary else 0,
        "delivered": branch_summary["delivered"] if branch_summary else 0,
        "actual_revenue": float(branch_summary["actual_revenue"]) if branch_summary and branch_summary["actual_revenue"] else 0,
        "conversion_rate_pct": round((branch_summary["delivered"] / branch_summary["total_leads"]) * 100, 1) if branch_summary and branch_summary["total_leads"] else None,
        "target_units": branch_summary["target_units"] if branch_summary else 0,
        "revenue_attainment_pct": round((float(branch_summary["actual_revenue"]) / float(branch_summary["target_revenue"])) * 100, 1) if branch_summary and branch_summary["target_revenue"] else None,
        "avg_days_to_deliver": round(branch_summary["avg_days_to_deliver"], 1) if branch_summary and branch_summary["avg_days_to_deliver"] is not None else None,
    }

    # --- Sales reps at this branch ---
    reps = db.execute(text("""
        SELECT
            sr.id AS rep_id,
            sr.name,
            sr.role,
            COUNT(l.id) AS total_leads,
            COUNT(l.id) FILTER (WHERE l.status = 'delivered') AS delivered,
            SUM(l.deal_value) FILTER (WHERE l.status = 'delivered') AS revenue
        FROM sales_reps sr
        LEFT JOIN leads l ON l.assigned_to = sr.id
        WHERE sr.branch_id = :bid
          AND (:start_date IS NULL OR l.created_at >= :start_date)
          AND (:end_date IS NULL OR l.created_at <= :end_date)
        GROUP BY sr.id, sr.name, sr.role
        ORDER BY revenue DESC NULLS LAST
    """), {"bid": branch_id, "start_date": start_date, "end_date": end_date}).mappings().all()

    sales_reps = [{
        "rep_id": r["rep_id"],
        "name": r["name"],
        "role": r["role"],
        "total_leads": r["total_leads"],
        "delivered": r["delivered"],
        "revenue": float(r["revenue"]) if r["revenue"] else 0,
        "conversion_rate_pct": round(r["delivered"] / r["total_leads"] * 100, 1) if r["total_leads"] else None,
    } for r in reps]

    # --- Lead sources ---
    sources = db.execute(text("""
        SELECT
            source,
            COUNT(*) AS total_leads,
            COUNT(*) FILTER (WHERE status = 'delivered') AS delivered
        FROM leads
        WHERE branch_id = :bid
          AND (:start_date IS NULL OR created_at >= :start_date)
          AND (:end_date IS NULL OR created_at <= :end_date)
        GROUP BY source
        ORDER BY total_leads DESC
    """), {"bid": branch_id, "start_date": start_date, "end_date": end_date}).mappings().all()

    lead_sources = [{
        "source": s["source"],
        "total_leads": s["total_leads"],
        "delivered": s["delivered"],
        "conversion_rate_pct": round(s["delivered"] / s["total_leads"] * 100, 1) if s["total_leads"] else None,
    } for s in sources]

    # --- Monthly trend ---
    monthly = db.execute(text("""
        SELECT
            DATE_TRUNC('month', created_at)::date AS month,
            COUNT(*) AS total_leads,
            COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
            SUM(deal_value) FILTER (WHERE status = 'delivered') AS revenue
        FROM leads
        WHERE branch_id = :bid
          AND (:start_date IS NULL OR created_at >= :start_date)
          AND (:end_date IS NULL OR created_at <= :end_date)
        GROUP BY 1
        ORDER BY 1
    """), {"bid": branch_id, "start_date": start_date, "end_date": end_date}).mappings().all()

    monthly_trend = [{
        "month": m["month"].strftime("%Y-%m"),
        "total_leads": m["total_leads"],
        "delivered": m["delivered"],
        "revenue": float(m["revenue"]) if m["revenue"] else 0,
    } for m in monthly]

    # --- Funnel (current status distribution) ---
    # NOTE: adjust STAGE_ORDER to match the actual `status` values seeded
    # in your leads table — this is a guess based on typical CRM stages.
    STAGE_ORDER = ["new", "contacted", "test_drive", "negotiation", "order", "delivered"]

    funnel_rows = db.execute(text("""
        SELECT status, COUNT(*) AS count
        FROM leads
        WHERE branch_id = :bid
          AND (:start_date IS NULL OR created_at >= :start_date)
          AND (:end_date IS NULL OR created_at <= :end_date)
        GROUP BY status
    """), {"bid": branch_id, "start_date": start_date, "end_date": end_date}).mappings().all()

    counts_by_status = {r["status"]: r["count"] for r in funnel_rows}
    funnel = [
        {"stage": stage, "count": counts_by_status.get(stage, 0)}
        for stage in STAGE_ORDER
    ]

    # --- Lost reasons ---
    lost_rows = db.execute(text("""
        SELECT lost_reason, COUNT(*) AS count
        FROM leads
        WHERE branch_id = :bid
          AND status = 'lost'
          AND lost_reason IS NOT NULL
          AND (:start_date IS NULL OR created_at >= :start_date)
          AND (:end_date IS NULL OR created_at <= :end_date)
        GROUP BY lost_reason
        ORDER BY count DESC
    """), {"bid": branch_id, "start_date": start_date, "end_date": end_date}).mappings().all()

    lost_reasons = [{"reason": r["lost_reason"], "count": r["count"]} for r in lost_rows]

    return {
        "branch": dict(branch_row),
        "summary": summary,
        "sales_reps": sales_reps,
        "lead_sources": lead_sources,
        "monthly_trend": monthly_trend,
        "funnel": funnel,
        "lost_reasons": lost_reasons,
    }

