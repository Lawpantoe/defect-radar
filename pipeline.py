"""Defect Radar pipeline: clean -> merge -> tag defects -> score clusters.

Same rules approved during the ClaimsHero case cleaning (see project_log.md).
In production the tagging step calls an LLM; this prototype uses transparent
keyword rules so every tag can be explained.
"""
import re
import warnings
warnings.filterwarnings("ignore", message="This pattern is interpreted as a regular expression")
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- cleaning
def clean(raw: pd.DataFrame):
    """Apply the approved cleaning rules. Returns (clean_df, step_log, change_log)."""
    df = raw.copy().astype(str).replace("nan", "")
    steps, changes = [("Raw rows loaded", len(df), "")], []

    def record(mask, field, new, reason):
        for _, r in df[mask].iterrows():
            changes.append((r.complaint_id, field, r[field], new, reason))

    newcity = df.city.str.strip().str.replace(r"\s+", " ", regex=True).str.title()
    m = df.city != newcity
    record(m, "city", "", "Standardize capitalization/spaces")
    df["city"] = newcity
    steps.append(("Standardize city names", len(df), f"{m.sum():,} cells"))

    m = df.state == "00"
    record(m, "state", "", "Invalid state code"); df.loc[m, "state"] = ""
    steps.append(('State "00" -> blank', len(df), f"{m.sum():,} cells"))

    m1 = df.fail_date == "19691231"
    m2 = (df.fail_date < "20000101") & (df.fail_date != "") & ~m1
    m3 = (df.fail_date > df.date_received) & (df.fail_date != "")
    record(m1 | m2 | m3, "fail_date", "", "Impossible/placeholder failure date")
    df.loc[m1 | m2 | m3, "fail_date"] = ""
    steps.append(("Blank impossible failure dates", len(df), f"{(m1|m2|m3).sum():,} cells"))

    m = df.year == "9999"
    record(m, "year", "", "Invalid model year"); df.loc[m, "year"] = ""
    steps.append(("Model year 9999 -> blank", len(df), f"{m.sum():,} cells"))

    m = df.mileage == "0"
    record(m, "mileage", "", "Mileage 0 = missing"); df.loc[m, "mileage"] = ""
    steps.append(("Mileage 0 -> blank", len(df), f"{m.sum():,} cells"))

    df["recall_notice_text"] = np.where(
        df.complaint_text.str.contains("authorized your dealer", case=False), "Y", "N")
    steps.append(("Flag pasted recall notices", len(df),
                  f"{(df.recall_notice_text == 'Y').sum():,} rows flagged"))

    # Merge rows from one complaint: every field identical except ID + component.
    # If NHTSA's shared complaint number (ODINO) is available, merge on it directly.
    if "ODINO" in df.columns and (df.ODINO != "").all():
        key = ["ODINO"]
    else:
        key = [c for c in df.columns if c not in ("complaint_id", "component")]
    df["_id"] = pd.to_numeric(df.complaint_id, errors="coerce")
    df = df.sort_values("_id")
    df["_g"] = df.groupby(key, sort=False, dropna=False).ngroup()
    agg = df.groupby("_g").agg(
        complaint_id=("complaint_id", "first"),
        rows_merged=("complaint_id", "size"),
        merged_complaint_ids=("complaint_id", lambda x: "; ".join(x)),
        component=("component", lambda x: "; ".join(dict.fromkeys(x))))
    keep = [c for c in df.columns if c not in ("complaint_id", "component", "_id", "_g")]
    out = agg.join(df.drop_duplicates("_g").set_index("_g")[keep]).reset_index(drop=True)
    steps.append(("Merge rows from the same complaint", len(out),
                  f"{len(df) - len(out):,} rows merged away"))

    for c in ["year", "injured", "deaths", "mileage"]:
        out[c] = pd.to_numeric(out[c].replace("", np.nan), errors="coerce")
    for c in ["fail_date", "date_received"]:
        out[c] = pd.to_datetime(out[c], format="%Y%m%d", errors="coerce")
    for c in ["crash", "fire", "police_report", "medical_attention", "vehicle_towed"]:
        out[c] = (out[c] == "Y").astype(int)
    out["age_yrs"] = out.fail_date.dt.year - out.year
    out["serious"] = ((out.crash + out.fire + out.medical_attention) > 0) | \
                     (out.injured.fillna(0) > 0) | (out.deaths.fillna(0) > 0)
    out["month"] = out.date_received.dt.to_period("M").astype(str)
    return out, pd.DataFrame(steps, columns=["step", "rows_after", "detail"]), \
        pd.DataFrame(changes, columns=["complaint_id", "field", "old", "new", "reason"])


# ---------------------------------------------------------------- tagging
# Each rule: make filter, a STRONG pattern (high confidence) and a WEAK pattern
# (partial match -> sent to the human review queue).
DEFECT_RULES = [
    dict(defect="Honda idle-stop no-restart", make="HONDA",
         strong=r"(idle stop|start.?stop|stop.?start|auto stop).{0,120}(restart|start back|turn back on)|(fail|fails|failed|won.t|will not|does not|did not) (to )?restart",
         weak=r"idle stop|start.?stop|stop.?start|auto stop"),
    dict(defect="Honda sticky steering", make="HONDA",
         strong=r"steer\w*.{0,60}(stick|sticky|stuck|notch|bind)|(stick|sticky|notchy).{0,40}steer",
         weak=r"steering (rack|gear ?box)|hard to steer"),
    dict(defect="Ford EcoBoost coolant intrusion", make="FORD",
         strong=r"coolant.{0,80}(intrusion|into the cylinder|in(to)? cylinder|cylinder|combustion)",
         weak=r"coolant (loss|leak)|losing coolant|no (visible|external) leak"),
    dict(defect="Ford transmission shifting", make="FORD",
         strong=r"transmission.{0,120}(downshift|harsh|slam|lurch|clunk)|10r80|6r80|10.speed",
         weak=r"transmission"),
    dict(defect="Tesla FSD / Autopilot behavior", make="TESLA",
         strong=r"fsd|full self.driving|autopilot|phantom brak",
         weak=r"self.driving|cruise|sudden(ly)? brak"),
    dict(defect="Ford rearview camera", make="FORD",
         strong=r"(rear ?view|back.?up|backup|reverse) camera",
         weak=r"camera"),
    dict(defect="Ford seat belt pretensioner", make="FORD",
         strong=r"pretension", weak=r"seat ?belt.{0,40}(lock|retract)"),
]


def tag(df: pd.DataFrame) -> pd.DataFrame:
    """Give each complaint at most one defect label + a confidence score."""
    df = df.copy()
    t = df.complaint_text.str.lower()
    df["defect"], df["confidence"] = "", 0.0
    for r in DEFECT_RULES:
        open_ = (df.defect == "") & (df.make == r["make"])
        s = open_ & t.str.contains(r["strong"], regex=True)
        w = open_ & ~s & t.str.contains(r["weak"], regex=True)
        df.loc[s, ["defect", "confidence"]] = [r["defect"], 0.9]
        df.loc[w, ["defect", "confidence"]] = [r["defect"], 0.55]
    df["needs_review"] = (df.defect != "") & (df.confidence < 0.7)
    return df


# ---------------------------------------------------------------- scoring
def score(df: pd.DataFrame, month: str, landscape: pd.DataFrame) -> pd.DataFrame:
    """Summarize each defect: volume, monthly growth, young-car share, severity, gap status."""
    months = sorted(df.month.dropna().unique())
    prev = months[months.index(month) - 1] if months.index(month) > 0 else None
    d = df[(df.defect != "") & (df.month <= month)]
    t = d.complaint_text.str.lower()
    d = d.assign(repeat_dealer=t.str.contains(r"again|still|repeat|keeps").astype(int) &
                 t.str.contains(r"dealer").astype(int))
    g = d.groupby("defect").agg(
        incidents=("complaint_id", "size"),
        this_month=("month", lambda m: int((m == month).sum())),
        last_month=("month", lambda m: int((m == prev).sum()) if prev else 0),
        young_pct=("age_yrs", lambda a: round((a <= 3).mean() * 100)),
        serious=("serious", "sum"),
        crashes=("crash", "sum"),
        injured=("injured", "sum"),
        repeat_after_dealer_pct=("repeat_dealer", lambda x: round(x.mean() * 100)),
        low_confidence=("needs_review", "sum"),
        top_models=("model", lambda m: ", ".join(m.value_counts().head(3).index)),
        top_years=("year", lambda y: ", ".join(str(int(v)) for v in y.value_counts().head(3).index)),
    )
    g["growth_pct"] = np.where(g.last_month > 0,
                               ((g.this_month - g.last_month) / g.last_month * 100).round(),
                               np.nan)
    g = g.join(landscape.set_index("defect"), how="left")
    g["gap"] = g["gap"].fillna("Unknown")
    # Transparent priority points (shown in the app so the reviewer sees why).
    g["priority_pts"] = (
        (g.incidents >= 200).astype(int) * 2 + (g.incidents >= 100).astype(int)
        + (g.young_pct >= 50).astype(int) * 2
        + (g.serious >= 20).astype(int) * 2
        + (g.repeat_after_dealer_pct >= 10).astype(int)
        + g.gap.map({"Open gap": 3, "Partial gap": 2, "Crowded": 0}).fillna(1))
    return g.sort_values("priority_pts", ascending=False).reset_index()
