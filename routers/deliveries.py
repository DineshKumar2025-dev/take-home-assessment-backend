from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter()


@router.get("/api/deliveries")
def get_deliveries(db: Session = Depends(get_db)):

    # --- Overall on-time vs late ---
    overall = db.execute(text("""
        SELECT
            COUNT(*) AS total_deliveries,
            COUNT(*) FILTER (WHERE delay_reason IS NULL) AS on_time,
            COUNT(*) FILTER (WHERE delay_reason IS NOT NULL) AS late,
            AVG(days_to_deliver) AS avg_days_to_deliver,
            AVG(days_to_deliver) FILTER (WHERE delay_reason IS NULL) AS avg_days_on_time,
            AVG(days_to_deliver) FILTER (WHERE delay_reason IS NOT NULL) AS avg_days_late
        FROM deliveries
    """)).mappings().first()

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
    reasons = db.execute(text("""
        SELECT delay_reason, COUNT(*) AS count, AVG(days_to_deliver) AS avg_days
        FROM deliveries
        WHERE delay_reason IS NOT NULL
        GROUP BY delay_reason
        ORDER BY count DESC
    """)).mappings().all()

    delay_reasons = [{
        "reason": r["delay_reason"],
        "count": r["count"],
        "avg_days": round(r["avg_days"], 1) if r["avg_days"] else None,
    } for r in reasons]

    # --- Branch comparison ---
    branch_rows = db.execute(text("""
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
        GROUP BY b.id, b.name, b.city
        ORDER BY b.id
    """)).mappings().all()

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
    monthly = db.execute(text("""
        SELECT
            DATE_TRUNC('month', delivery_date)::date AS month,
            COUNT(*) FILTER (WHERE delay_reason IS NULL) AS on_time,
            COUNT(*) FILTER (WHERE delay_reason IS NOT NULL) AS late
        FROM deliveries
        GROUP BY 1
        ORDER BY 1
    """)).mappings().all()

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