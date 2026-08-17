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


@router.get("/api/deliveries")
def get_deliveries(
    db: Session = Depends(get_db),
    range: Optional[str] = Query(None, alias="range"),
):
    start_date, end_date = resolve_time_range(range)

    delivery_where = ""
    delivery_params = {}
    if start_date and end_date:
        delivery_where = "WHERE delivery_date >= :start_date AND delivery_date <= :end_date"
        delivery_params["start_date"] = start_date
        delivery_params["end_date"] = end_date

    # --- Overall on-time vs late ---
    overall = db.execute(text(f"""
        SELECT
            COUNT(*) AS total_deliveries,
            COUNT(*) FILTER (WHERE delay_reason IS NULL) AS on_time,
            COUNT(*) FILTER (WHERE delay_reason IS NOT NULL) AS late,
            AVG(days_to_deliver) AS avg_days_to_deliver,
            AVG(days_to_deliver) FILTER (WHERE delay_reason IS NULL) AS avg_days_on_time,
            AVG(days_to_deliver) FILTER (WHERE delay_reason IS NOT NULL) AS avg_days_late
        FROM deliveries
        {delivery_where}
    """), delivery_params).mappings().first()

    total = overall["total_deliveries"] or 0
    on_time = overall["on_time"] or 0
    late = overall["late"] or 0

    summary = {
        "total_deliveries": total,
        "on_time": on_time,
        "late": late,
        "on_time_pct": round(on_time / total * 100, 1) if total else None,
        "late_pct": round(late / total * 100, 1) if total else None,
        "avg_days_to_deliver": round(overall["avg_days_to_deliver"], 1) if overall["avg_days_to_deliver"] else None,
        "avg_days_on_time": round(overall["avg_days_on_time"], 1) if overall["avg_days_on_time"] else None,
        "avg_days_late": round(overall["avg_days_late"], 1) if overall["avg_days_late"] else None,
    }

    # --- Delay reasons breakdown ---
    reasons = db.execute(text(f"""
        SELECT delay_reason, COUNT(*) AS count, AVG(days_to_deliver) AS avg_days
        FROM deliveries
        WHERE delay_reason IS NOT NULL
          {('AND delivery_date >= :start_date AND delivery_date <= :end_date' if start_date and end_date else '')}
        GROUP BY delay_reason
        ORDER BY count DESC
    """), delivery_params).mappings().all()

    delay_reasons = [{
        "reason": r["delay_reason"],
        "count": r["count"],
        "avg_days": round(r["avg_days"], 1) if r["avg_days"] else None,
    } for r in reasons]

    # --- Branch comparison ---
    branch_rows = db.execute(text(f"""
        SELECT
            b.id AS branch_id,
            b.name,
            b.city,
            COUNT(d.id) AS total_deliveries,
            COUNT(d.id) FILTER (WHERE d.delay_reason IS NULL) AS on_time,
            COUNT(d.id) FILTER (WHERE d.delay_reason IS NOT NULL) AS late,
            AVG(d.days_to_deliver) AS avg_days_to_deliver
        FROM branches b
        LEFT JOIN leads l ON l.branch_id = b.id
        LEFT JOIN deliveries d ON d.lead_id = l.id
        {('AND d.delivery_date >= :start_date AND d.delivery_date <= :end_date' if start_date and end_date else '')}
        GROUP BY b.id, b.name, b.city
        ORDER BY b.id
    """), delivery_params).mappings().all()

    branch_comparison = []
    for r in branch_rows:
        t = r["total_deliveries"] or 0
        branch_comparison.append({
            "branch_id": r["branch_id"],
            "name": r["name"],
            "city": r["city"],
            "total_deliveries": t,
            "on_time": r["on_time"] or 0,
            "late": r["late"] or 0,
            "on_time_pct": round((r["on_time"] or 0) / t * 100, 1) if t else None,
            "avg_days_to_deliver": round(r["avg_days_to_deliver"], 1) if r["avg_days_to_deliver"] else None,
        })

    # --- Monthly delivery trend (on-time vs late, by delivery month) ---
    monthly = db.execute(text(f"""
        SELECT
            DATE_TRUNC('month', delivery_date)::date AS month,
            COUNT(*) FILTER (WHERE delay_reason IS NULL) AS on_time,
            COUNT(*) FILTER (WHERE delay_reason IS NOT NULL) AS late
        FROM deliveries
        {delivery_where}
        GROUP BY 1
        ORDER BY 1
    """), delivery_params).mappings().all()

    monthly_trend = [{
        "month": m["month"].strftime("%Y-%m"),
        "on_time": m["on_time"],
        "late": m["late"],
    } for m in monthly]

    return {
        "summary": summary,
        "delay_reasons": delay_reasons,
        "branch_comparison": branch_comparison,
        "monthly_trend": monthly_trend,
    }