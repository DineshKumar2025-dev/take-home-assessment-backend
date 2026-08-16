from datetime import date, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter()

PERIOD_START = date(2025, 6, 1)
PERIOD_END = date(2025, 12, 31)  # last day of the last target month


@router.get("/api/forecast")
def get_forecast(db: Session = Depends(get_db)):

    # "Today" = latest activity in the dataset, so day-count math stays
    # meaningful for a historical dataset instead of comparing to the
    # real calendar date (which would put us months past PERIOD_END).
    reference_time = db.execute(text("SELECT MAX(last_activity_at) FROM leads")).scalar()
    today = reference_time.date() if reference_time else PERIOD_END

    total_period_days = (PERIOD_END - PERIOD_START).days + 1
    days_elapsed = max((today - PERIOD_START).days + 1, 1)
    days_elapsed = min(days_elapsed, total_period_days)
    days_left = max(total_period_days - days_elapsed, 0)

    # --- Overall target per branch, summed across ALL months (Jun-Dec) ---
    targets = db.execute(text("""
        SELECT branch_id,
               SUM(target_units) AS target_units,
               SUM(target_revenue) AS target_revenue
        FROM targets
        WHERE month >= :start AND month <= :end
        GROUP BY branch_id
    """), {"start": PERIOD_START, "end": PERIOD_END}).mappings().all()
    target_by_branch = {t["branch_id"]: t for t in targets}

    # --- Overall actuals per branch, summed across the whole period ---
    actuals = db.execute(text("""
        SELECT branch_id,
               COUNT(*) FILTER (WHERE status = 'delivered') AS actual_units,
               SUM(deal_value) FILTER (WHERE status = 'delivered') AS actual_revenue
        FROM leads
        WHERE created_at >= :start AND created_at < (:end + INTERVAL '1 day')
        GROUP BY branch_id
    """), {"start": PERIOD_START, "end": PERIOD_END}).mappings().all()
    actual_by_branch = {a["branch_id"]: a for a in actuals}

    branches = db.execute(text("SELECT id, name, city FROM branches ORDER BY id")).mappings().all()

    forecast = []
    for b in branches:
        bid = b["id"]
        t = target_by_branch.get(bid, {})
        a = actual_by_branch.get(bid, {})

        target_units = t.get("target_units") or 0
        target_revenue = float(t.get("target_revenue") or 0)
        actual_units = a.get("actual_units") or 0
        actual_revenue = float(a.get("actual_revenue") or 0)

        # Run-rate projection across the WHOLE period, not one month
        projected_units = round(actual_units / days_elapsed * total_period_days)
        projected_revenue = round(actual_revenue / days_elapsed * total_period_days, 2)

        revenue_attainment_pct = round(actual_revenue / target_revenue * 100, 1) if target_revenue else None
        projected_attainment_pct = round(projected_revenue / target_revenue * 100, 1) if target_revenue else None
        revenue_gap = target_revenue - actual_revenue

        # Where attainment "should" be if pacing evenly across the whole period
        expected_pace_pct = round(days_elapsed / total_period_days * 100, 1)
        behind_by_pct = round(expected_pace_pct - (revenue_attainment_pct or 0), 1)

        status = "on_track"
        if revenue_attainment_pct is not None:
            if projected_attainment_pct is not None and projected_attainment_pct < 85:
                status = "at_risk"
            if behind_by_pct >= 25:
                status = "critical"

        warning = None
        if status in ("at_risk", "critical") and revenue_gap > 0:
            pct_behind_target = round(100 - (revenue_attainment_pct or 0), 0)
            warning = (
                f"{b['name']} is {int(pct_behind_target)}% behind target "
                f"with {days_left} day{'s' if days_left != 1 else ''} left. "
                f"Gap: {format_inr(revenue_gap)}."
            )

        forecast.append({
            "branch_id": bid,
            "name": b["name"],
            "city": b["city"],
            "target_units": target_units,
            "actual_units": actual_units,
            "projected_units": projected_units,
            "target_revenue": target_revenue,
            "actual_revenue": actual_revenue,
            "projected_revenue": projected_revenue,
            "revenue_attainment_pct": revenue_attainment_pct,
            "projected_attainment_pct": projected_attainment_pct,
            "revenue_gap": revenue_gap,
            "status": status,
            "warning": warning,
        })

    return {
        "period_start": PERIOD_START.isoformat(),
        "period_end": PERIOD_END.isoformat(),
        "days_elapsed": days_elapsed,
        "days_left": days_left,
        "total_period_days": total_period_days,
        "branches": forecast,
    }


def format_inr(value: float) -> str:
    if value >= 10000000:
        return f"₹{value / 10000000:.2f} Cr"
    if value >= 100000:
        return f"₹{value / 100000:.1f} L"
    if value >= 1000:
        return f"₹{value / 1000:.0f}K"
    return f"₹{value:.0f}"