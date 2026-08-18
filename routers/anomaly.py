import statistics
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter()

# How far below the peer average (in standard deviations) counts as an anomaly.
ZSCORE_THRESHOLD = 1.0


def format_inr(value: float) -> str:
    if value >= 10000000:
        return f"₹{value / 10000000:.2f}Cr"
    if value >= 100000:
        return f"₹{value / 100000:.1f}L"
    if value >= 1000:
        return f"₹{value / 1000:.0f}K"
    return f"₹{value:.0f}"


def zscore_flags(branch_values, metric_name, label_fn, direction="low"):
    """
    branch_values: list of (id, name, value) tuples, value may be None.
    Flags entries whose value is ZSCORE_THRESHOLD std-devs worse than the peer mean.
    """
    values = [v for _, _, v in branch_values if v is not None]
    if len(values) < 3:
        return []  # not enough data points to call anything statistically unusual

    mean = statistics.mean(values)
    stdev = statistics.pstdev(values)
    if stdev == 0:
        return []

    flags = []
    for entity_id, name, value in branch_values:
        if value is None:
            continue
        z = (value - mean) / stdev
        is_anomaly = z <= -ZSCORE_THRESHOLD if direction == "low" else z >= ZSCORE_THRESHOLD
        if is_anomaly:
            flags.append({
                "branch_id": entity_id,
                "branch_name": name,
                "metric": metric_name,
                "value": round(value, 1),
                "peer_avg": round(mean, 1),
                "z_score": round(z, 2),
                "message": label_fn(name, value, mean),
                "severity": "critical" if abs(z) >= 2 else "warning",
            })
    return flags


@router.get("/api/anomalies")
def get_anomalies(db: Session = Depends(get_db)):
    anomalies = []

    # --- 1. Branch conversion rate vs peers ---
    conv_rows = db.execute(text("""
        SELECT
            b.id AS branch_id, b.name,
            COUNT(l.id) AS total_leads,
            COUNT(l.id) FILTER (WHERE l.status = 'delivered') AS delivered
        FROM branches b
        LEFT JOIN leads l ON l.branch_id = b.id
        GROUP BY b.id, b.name
    """)).mappings().all()

    conv_values = [
        (r["branch_id"], r["name"], round(r["delivered"] / r["total_leads"] * 100, 1) if r["total_leads"] else None)
        for r in conv_rows
    ]
    anomalies += zscore_flags(
        conv_values, "conversion_rate",
        lambda name, val, avg: f"{name}'s conversion rate ({val}%) is well below the network average ({round(avg,1)}%).",
    )

    # --- 2. Branch revenue attainment vs peers ---
    attain_rows = db.execute(text("""
        WITH ls AS (
            SELECT branch_id, SUM(deal_value) FILTER (WHERE status = 'delivered') AS revenue
            FROM leads GROUP BY branch_id
        ),
        ts AS (
            SELECT branch_id, SUM(target_revenue) AS target_revenue
            FROM targets GROUP BY branch_id
        )
        SELECT b.id AS branch_id, b.name,
               COALESCE(ls.revenue, 0) AS revenue,
               COALESCE(ts.target_revenue, 0) AS target_revenue
        FROM branches b
        LEFT JOIN ls ON ls.branch_id = b.id
        LEFT JOIN ts ON ts.branch_id = b.id
    """)).mappings().all()

    attain_values = [
        (r["branch_id"], r["name"],
         round(float(r["revenue"]) / float(r["target_revenue"]) * 100, 1) if r["target_revenue"] else None)
        for r in attain_rows
    ]
    anomalies += zscore_flags(
        attain_values, "target_attainment",
        lambda name, val, avg: f"{name} is at {val}% of target while the network averages {round(avg,1)}%.",
    )

    # --- 3. Branch on-time delivery rate vs peers ---
    delivery_rows = db.execute(text("""
        SELECT
            b.id AS branch_id, b.name,
            COUNT(d.id) AS total_deliveries,
            COUNT(d.id) FILTER (WHERE d.delay_reason IS NULL) AS on_time
        FROM branches b
        LEFT JOIN leads l ON l.branch_id = b.id
        LEFT JOIN deliveries d ON d.lead_id = l.id
        GROUP BY b.id, b.name
    """)).mappings().all()

    delivery_values = [
        (r["branch_id"], r["name"], round(r["on_time"] / r["total_deliveries"] * 100, 1) if r["total_deliveries"] else None)
        for r in delivery_rows
    ]
    anomalies += zscore_flags(
        delivery_values, "on_time_delivery_rate",
        lambda name, val, avg: f"{name}'s on-time delivery rate ({val}%) trails the network average ({round(avg,1)}%).",
    )

    # --- 4. Sales rep lost-rate vs peers (flags reps, not branches) ---
    rep_rows = db.execute(text("""
        SELECT
            sr.id AS rep_id, sr.name, b.name AS branch_name, b.id AS branch_id,
            COUNT(l.id) AS total_leads,
            COUNT(l.id) FILTER (WHERE l.status = 'lost') AS lost
        FROM sales_reps sr
        JOIN branches b ON b.id = sr.branch_id
        LEFT JOIN leads l ON l.assigned_to = sr.id
        GROUP BY sr.id, sr.name, b.name, b.id
        HAVING COUNT(l.id) >= 5
    """)).mappings().all()

    rep_lost_values = [
        (r["rep_id"], r["name"], round(r["lost"] / r["total_leads"] * 100, 1) if r["total_leads"] else None)
        for r in rep_rows
    ]
    rep_flags = zscore_flags(
        rep_lost_values, "rep_lost_rate",
        lambda name, val, avg: f"{name} has a lost-lead rate of {val}%, higher than peers ({round(avg,1)}% avg).",
        direction="high",
    )
    # zscore_flags puts the tuple's first element into "branch_id" generically —
    # here that was actually rep_id, so relabel and attach branch context.
    rep_branch_map = {r["rep_id"]: r["branch_name"] for r in rep_rows}
    for f in rep_flags:
        f["rep_id"] = f.pop("branch_id")
        f["branch_name"] = rep_branch_map.get(f["rep_id"])
    anomalies += rep_flags

    # --- 5. Revenue outlier months: for each branch, compare all 7 months
    # (Jun-Dec) against EACH OTHER at once, then flag any single month that's
    # a statistical outlier relative to that branch's own Jun-Dec pattern —
    # not just a drop vs the previous month.
    monthly_rows = db.execute(text("""
        SELECT
            branch_id,
            DATE_TRUNC('month', created_at)::date AS month,
            SUM(deal_value) FILTER (WHERE status = 'delivered') AS revenue
        FROM leads
        WHERE created_at >= '2025-06-01' AND created_at < '2026-01-01'
        GROUP BY branch_id, month
        ORDER BY branch_id, month
    """)).mappings().all()

    branch_names = {r["branch_id"]: r["name"] for r in conv_rows}
    by_branch = {}
    for r in monthly_rows:
        by_branch.setdefault(r["branch_id"], []).append({
            "month": r["month"],
            "revenue": float(r["revenue"]) if r["revenue"] else 0,
        })

    for branch_id, months in by_branch.items():
        months.sort(key=lambda m: m["month"])
        revenues = [m["revenue"] for m in months]

        if len(revenues) < 3:
            continue  # not enough months to judge what's "normal" for this branch

        mean_rev = statistics.mean(revenues)
        stdev_rev = statistics.pstdev(revenues)
        if stdev_rev == 0:
            continue  # every month identical — nothing to flag

        for m in months:
            z = (m["revenue"] - mean_rev) / stdev_rev
            if z <= -ZSCORE_THRESHOLD:
                anomalies.append({
                    "branch_id": branch_id,
                    "branch_name": branch_names.get(branch_id, "Unknown"),
                    "metric": "revenue_month_outlier",
                    "value": round(m["revenue"], 0),
                    "month": m["month"].strftime("%b %Y"),
                    "peer_avg": round(mean_rev, 0),
                    "z_score": round(z, 2),
                    "message": (
                        f"{branch_names.get(branch_id)}'s revenue in {m['month'].strftime('%b %Y')} "
                        f"({format_inr(m['revenue'])}) was unusually low compared to its "
                        f"Jun–Dec average ({format_inr(mean_rev)})."
                    ),
                    "severity": "critical" if abs(z) >= 2 else "warning",
                })

    # Sort worst first: critical before warning, then by how extreme the z-score is
    severity_rank = {"critical": 0, "warning": 1}
    anomalies.sort(key=lambda a: (severity_rank.get(a["severity"], 2), -abs(a.get("z_score", 0))))

    return {
        "total_anomalies": len(anomalies),
        "critical_count": sum(1 for a in anomalies if a["severity"] == "critical"),
        "warning_count": sum(1 for a in anomalies if a["severity"] == "warning"),
        "anomalies": anomalies,
    }