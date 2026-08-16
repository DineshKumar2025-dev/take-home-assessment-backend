from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter()

# Same assumption as branches.py / sales_reps.py — verify against
# `SELECT DISTINCT status FROM leads;` and adjust if your seed data differs.
STAGE_ORDER = ["new", "contacted", "test_drive", "negotiation", "order", "delivered"]
STALE_DAYS = 7  # a lead with no activity in this many days is "going cold"


def _get_date_range(time_range: Optional[str] = None):
    """Parse time_range parameter and return (start_date, end_date) tuple for filtering."""
    if not time_range or time_range == "all":
        return None, None  # No filtering
    
    if time_range.startswith("q3-"):
        year = int(time_range.split("-")[1])
        return date(year, 7, 1), date(year, 10, 1)
    elif time_range.startswith("q4-"):
        year = int(time_range.split("-")[1])
        return date(year, 10, 1), date(year, 12, 31)
    elif time_range.startswith("month-"):
        parts = time_range.split("-")
        year = int(parts[1])
        month = int(parts[2])
        # First day of month to first day of next month
        if month == 12:
            return date(year, month, 1), date(year + 1, 1, 1)
        else:
            return date(year, month, 1), date(year, month + 1, 1)
    
    return None, None


@router.get("/api/dashboard/overview")
def get_dashboard_overview(
    db: Session = Depends(get_db),
    time_range: Optional[str] = Query(None)
):
    start_date, end_date = _get_date_range(time_range)
    
    # Build WHERE clause for date filtering
    date_filter = ""
    params = {}
    if start_date and end_date:
        date_filter = "WHERE created_at >= :start_date AND created_at < :end_date"
        params["start_date"] = start_date
        params["end_date"] = end_date

    # --- Top-line totals ---
    totals = db.execute(text(f"""
        SELECT
            COUNT(*) AS total_leads,
            COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
            COUNT(*) FILTER (WHERE status NOT IN ('delivered', 'lost')) AS active_pipeline,
            SUM(deal_value) FILTER (WHERE status = 'delivered') AS total_revenue,
            AVG(deal_value) FILTER (WHERE status = 'delivered') AS avg_deal_value
        FROM leads
        {date_filter}
    """), params).mappings().first()

    total_leads = totals["total_leads"]
    delivered = totals["delivered"]
    active_pipeline = totals["active_pipeline"]
    total_revenue = float(totals["total_revenue"]) if totals["total_revenue"] else 0
    avg_deal_value = float(totals["avg_deal_value"]) if totals["avg_deal_value"] else 0

    # --- Targets (whole dataset spans Jun-Dec 2025 per assignment) ---
    target_totals = db.execute(text(f"""
        SELECT
            SUM(target_units) AS target_units,
            SUM(target_revenue) AS target_revenue
        FROM targets
        {("WHERE month >= :start_date AND month < :end_date" if date_filter else "")}
    """), params).mappings().first()

    target_units = target_totals["target_units"] or 0
    target_revenue = float(target_totals["target_revenue"]) if target_totals["target_revenue"] else 0

    # --- December target gap ---
    dec_target = db.execute(text("""
        SELECT
            SUM(target_units) AS target_units,
            SUM(target_revenue) AS target_revenue
        FROM targets
        WHERE month = '2025-12-01'
    """)).mappings().first()

    dec_actual = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'delivered') AS delivered_units,
            SUM(deal_value) FILTER (WHERE status = 'delivered') AS delivered_revenue
        FROM leads
        WHERE status = 'delivered'
          AND created_at >= '2025-12-01' AND created_at < '2026-01-01'
    """)).mappings().first()

    dec_target_units = dec_target["target_units"] or 0
    dec_target_revenue = float(dec_target["target_revenue"]) if dec_target["target_revenue"] else 0
    dec_actual_units = dec_actual["delivered_units"] or 0
    dec_actual_revenue = float(dec_actual["delivered_revenue"]) if dec_actual["delivered_revenue"] else 0

    dec_target_gap = {
        "target_units": dec_target_units,
        "actual_units": dec_actual_units,
        "unit_gap": dec_target_units - dec_actual_units,
        "target_revenue": dec_target_revenue,
        "actual_revenue": dec_actual_revenue,
        "revenue_gap": dec_target_revenue - dec_actual_revenue,
        "attainment_pct": round(dec_actual_revenue / dec_target_revenue * 100, 1) if dec_target_revenue else None,
    }

    # --- Stale leads: active, no activity in STALE_DAYS+ ---
    # Uses the latest last_activity_at in the dataset as "now" so this
    # still works correctly against a fixed historical dataset (Jun-Dec 2025)
    # instead of comparing to today's real-world date.
    latest_activity = db.execute(text("SELECT MAX(last_activity_at) AS ts FROM leads")).scalar()
    reference_time = latest_activity or datetime.utcnow()

    stale_params = {"ref_time": reference_time, "stale_days": STALE_DAYS}
    if start_date and end_date:
        stale_params["start_date"] = start_date
        stale_params["end_date"] = end_date
    
    stale_rows = db.execute(text(f"""
        SELECT
            l.id AS lead_id,
            l.customer_name,
            l.status,
            l.branch_id,
            b.name AS branch_name,
            l.assigned_to,
            sr.name AS rep_name,
            l.last_activity_at,
            EXTRACT(DAY FROM (:ref_time - l.last_activity_at)) AS days_stale
        FROM leads l
        JOIN branches b ON b.id = l.branch_id
        LEFT JOIN sales_reps sr ON sr.id = l.assigned_to
        WHERE l.status NOT IN ('delivered', 'lost')
          AND l.last_activity_at <= (CAST(:ref_time AS timestamptz) - (:stale_days || ' days')::interval)
          {("AND l.created_at >= :start_date AND l.created_at < :end_date" if start_date and end_date else "")}
        ORDER BY l.last_activity_at ASC
    """), stale_params).mappings().all()

    stale_leads = [{
        "lead_id": r["lead_id"],
        "customer_name": r["customer_name"],
        "status": r["status"],
        "branch_name": r["branch_name"],
        "rep_name": r["rep_name"],
        "days_stale": int(r["days_stale"]),
    } for r in stale_rows]

    # --- Revenue & lead trend (monthly) ---
    monthly = db.execute(text(f"""
        SELECT
            DATE_TRUNC('month', created_at)::date AS month,
            COUNT(*) AS total_leads,
            COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
            SUM(deal_value) FILTER (WHERE status = 'delivered') AS revenue
        FROM leads
        {date_filter}
        GROUP BY 1
        ORDER BY 1
    """), params).mappings().all()

    monthly_trend = [{
        "month": m["month"].strftime("%Y-%m"),
        "total_leads": m["total_leads"],
        "delivered": m["delivered"],
        "revenue": float(m["revenue"]) if m["revenue"] else 0,
    } for m in monthly]

    # --- Lead sources ---
    sources = db.execute(text(f"""
        SELECT
            source,
            COUNT(*) AS total_leads,
            COUNT(*) FILTER (WHERE status = 'delivered') AS delivered
        FROM leads
        {date_filter}
        GROUP BY source
        ORDER BY total_leads DESC
    """), params).mappings().all()

    lead_sources = [{
        "source": s["source"],
        "total_leads": s["total_leads"],
        "delivered": s["delivered"],
        "conversion_rate_pct": round(s["delivered"] / s["total_leads"] * 100, 1) if s["total_leads"] else None,
    } for s in sources]

    # --- Lost reasons ---
    lost_where = "WHERE status = 'lost' AND lost_reason IS NOT NULL"
    if start_date and end_date:
        lost_where += " AND created_at >= :start_date AND created_at < :end_date"
    
    lost_rows = db.execute(text(f"""
        SELECT lost_reason, COUNT(*) AS count
        FROM leads
        {lost_where}
        GROUP BY lost_reason
        ORDER BY count DESC
    """), params).mappings().all()

    lost_reasons = [{"reason": r["lost_reason"], "count": r["count"]} for r in lost_rows]

    # --- Branch attainment (for a company-wide comparison strip) ---
    branch_attainment = db.execute(text(f"""
        WITH ls AS (
            SELECT branch_id, SUM(deal_value) FILTER (WHERE status = 'delivered') AS revenue
            FROM leads
            {date_filter}
            GROUP BY branch_id
        ),
        ts AS (
            SELECT branch_id, SUM(target_revenue) AS target_revenue
            FROM targets
            {("WHERE month >= :start_date AND month < :end_date" if date_filter else "")}
            GROUP BY branch_id
        )
        SELECT
            b.id AS branch_id, b.name,
            COALESCE(ls.revenue, 0) AS revenue,
            COALESCE(ts.target_revenue, 0) AS target_revenue
        FROM branches b
        LEFT JOIN ls ON ls.branch_id = b.id
        LEFT JOIN ts ON ts.branch_id = b.id
        ORDER BY b.id
    """), params).mappings().all()

    branch_attainment_list = [{
        "branch_id": r["branch_id"],
        "name": r["name"],
        "attainment_pct": round(float(r["revenue"]) / float(r["target_revenue"]) * 100, 1) if r["target_revenue"] else None,
    } for r in branch_attainment]

    return {
        "totals": {
            "total_revenue": total_revenue,
            "target_revenue": target_revenue,
            "revenue_attainment_pct": round(total_revenue / target_revenue * 100, 1) if target_revenue else None,
            "units_delivered": delivered,
            "target_units": target_units,
            "units_attainment_pct": round(delivered / target_units * 100, 1) if target_units else None,
            "active_pipeline": active_pipeline,
            "conversion_rate_pct": round(delivered / total_leads * 100, 1) if total_leads else None,
            "avg_deal_value": avg_deal_value,
            "stale_lead_count": len(stale_leads),
        },
        "dec_target_gap": dec_target_gap,
        "stale_leads": stale_leads[:20],  # cap payload; full count still in totals
        "monthly_trend": monthly_trend,
        "lead_sources": lead_sources,
        "lost_reasons": lost_reasons,
        "branch_attainment": branch_attainment_list,
    }