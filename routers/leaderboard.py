from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter()


@router.get("/api/leaderboard")
def get_leaderboard(db: Session = Depends(get_db)):

    # --- Rep performance (only reps with at least 1 lead, so idle/new
    # reps with zero activity don't clutter the "worst performer" list) ---
    rep_rows = db.execute(text("""
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
        LEFT JOIN leads l ON l.assigned_to = sr.id
        GROUP BY sr.id, sr.name, sr.role, sr.branch_id, b.name
        HAVING COUNT(l.id) > 0
        ORDER BY revenue DESC
    """)).mappings().all()

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
    branch_rows = db.execute(text("""
        WITH ls AS (
            SELECT
                branch_id,
                COUNT(*) AS total_leads,
                COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
                COALESCE(SUM(deal_value) FILTER (WHERE status = 'delivered'), 0) AS revenue
            FROM leads
            GROUP BY branch_id
        ),
        ts AS (
            SELECT branch_id, SUM(target_revenue) AS target_revenue
            FROM targets
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
    """)).mappings().all()

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