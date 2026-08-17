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


@router.get("/api/leaderboard")
def get_leaderboard(
    db: Session = Depends(get_db),
    range: Optional[str] = Query(None, alias="range"),
):
    start_date, end_date = resolve_time_range(range)

    rep_join = ""
    rep_params = {}
    if start_date and end_date:
        rep_join = "LEFT JOIN leads l ON l.assigned_to = sr.id AND l.created_at >= :start_date AND l.created_at <= :end_date"
        rep_params["start_date"] = start_date
        rep_params["end_date"] = end_date
    else:
        rep_join = "LEFT JOIN leads l ON l.assigned_to = sr.id"

    # --- Rep performance (only reps with at least 1 lead, so idle/new
    # reps with zero activity don't clutter the "worst performer" list) ---
    rep_rows = db.execute(text(f"""
        SELECT
            sr.id AS rep_id,
            sr.name,
            sr.role,
            sr.branch_id,
            b.name AS branch_name,
            COUNT(l.id) AS total_leads,
            COUNT(l.id) FILTER (WHERE l.status = 'delivered') AS delivered,
            COALESCE(SUM(l.deal_value) FILTER (WHERE l.status = 'delivered'), 0) AS revenue
        FROM sales_reps sr
        JOIN branches b ON b.id = sr.branch_id
        {rep_join}
        GROUP BY sr.id, sr.name, sr.role, sr.branch_id, b.name
        HAVING COUNT(l.id) > 0
        ORDER BY revenue DESC
    """), rep_params).mappings().all()

    reps = []
    for r in rep_rows:
        conv = round(r["delivered"] / r["total_leads"] * 100, 1) if r["total_leads"] else 0
        reps.append({
            "rep_id": r["rep_id"],
            "name": r["name"],
            "role": r["role"],
            "branch_id": r["branch_id"],
            "branch_name": r["branch_name"],
            "total_leads": r["total_leads"],
            "delivered": r["delivered"],
            "revenue": float(r["revenue"]),
            "conversion_rate_pct": conv,
        })

    # Ranked by revenue (primary), already sorted from SQL, but re-sort in
    # Python to be explicit and add rank numbers.
    ranked_by_revenue = sorted(reps, key=lambda r: r["revenue"], reverse=True)
    for i, r in enumerate(ranked_by_revenue, start=1):
        r["rank"] = i

    top_10 = ranked_by_revenue[:10]

    # Bottom performers: lowest revenue among reps who've actually had a
    # meaningful shot (3+ leads) — otherwise a rep with 1 lead and 0
    # conversions unfairly tops the "worst" list over someone with real volume.
    eligible_for_bottom = [r for r in reps if r["total_leads"] >= 3]
    bottom_5 = sorted(eligible_for_bottom, key=lambda r: r["revenue"])[:5]

    # --- Branch ranking ---
    branch_params = {}
    branch_where = ""
    if start_date and end_date:
        branch_where = "WHERE created_at >= :start_date AND created_at <= :end_date"
        branch_params["start_date"] = start_date
        branch_params["end_date"] = end_date

    branch_rows = db.execute(text(f"""
        WITH ls AS (
            SELECT
                branch_id,
                COUNT(*) AS total_leads,
                COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
                COALESCE(SUM(deal_value) FILTER (WHERE status = 'delivered'), 0) AS revenue
            FROM leads
            {branch_where}
            GROUP BY branch_id
        ),
        ts AS (
            SELECT branch_id, SUM(target_revenue) AS target_revenue
            FROM targets
            WHERE (:start_date IS NULL OR month >= :start_date)
              AND (:end_date IS NULL OR month <= :end_date)
            GROUP BY branch_id
        )
        SELECT
            b.id AS branch_id,
            b.name,
            b.city,
            COALESCE(ls.total_leads, 0) AS total_leads,
            COALESCE(ls.delivered, 0) AS delivered,
            COALESCE(ls.revenue, 0) AS revenue,
            COALESCE(ts.target_revenue, 0) AS target_revenue
        FROM branches b
        LEFT JOIN ls ON ls.branch_id = b.id
        LEFT JOIN ts ON ts.branch_id = b.id
    """), {**branch_params, "start_date": start_date, "end_date": end_date}).mappings().all()

    branches = []
    for b in branch_rows:
        target_revenue = float(b["target_revenue"])
        revenue = float(b["revenue"])
        attainment_pct = round(revenue / target_revenue * 100, 1) if target_revenue else None
        conv = round(b["delivered"] / b["total_leads"] * 100, 1) if b["total_leads"] else None
        branches.append({
            "branch_id": b["branch_id"],
            "name": b["name"],
            "city": b["city"],
            "total_leads": b["total_leads"],
            "delivered": b["delivered"],
            "revenue": revenue,
            "target_revenue": target_revenue,
            "attainment_pct": attainment_pct,
            "conversion_rate_pct": conv,
        })

    # Rank branches by attainment_pct (falls back to revenue if no target)
    branches_ranked = sorted(
        branches,
        key=lambda b: (b["attainment_pct"] is not None, b["attainment_pct"] or b["revenue"]),
        reverse=True,
    )
    for i, b in enumerate(branches_ranked, start=1):
        b["rank"] = i

    return {
        "top_reps": top_10,
        "bottom_reps": bottom_5,
        "branch_ranking": branches_ranked,
    }