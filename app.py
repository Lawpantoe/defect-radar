"""Defect Radar: monthly scan of NHTSA complaints for mass-action candidates.

Run:  streamlit run app.py
The scan runs on its own; a person approves, rejects, or edits every flag.
"""
import os
from datetime import datetime

import pandas as pd
import streamlit as st

from pipeline import clean, tag, score

st.set_page_config(page_title="Defect Radar", page_icon="📡", layout="wide")
LIVE_DATA = "data/complaints_live.csv"
# The case dataset can be stored zipped or as the original CSV file.
CASE_DATA = next((p for p in ["data/complaints_raw.csv.zip", "data/complaints_raw.csv",
                              "data/nhtsa_vehicle_complaints_15k.csv"] if os.path.exists(p)),
                 "data/complaints_raw.csv.zip")
LANDSCAPE, DECISIONS = "data/landscape.csv", "data/decisions.csv"


# ---------------------------------------------------------------- data source
st.sidebar.title("📡 Defect Radar")
sources = {"Case dataset (Jun–Oct 2026)": CASE_DATA}
if os.path.exists(LIVE_DATA):
    sources["Live NHTSA pull"] = LIVE_DATA
DATA = sources[st.sidebar.radio("Data", list(sources))]
if st.sidebar.button("⬇️ Pull latest data from NHTSA"):
    with st.spinner("Downloading NHTSA's complaints file (large, can take a few minutes)…"):
        try:
            import fetch_nhtsa
            st.sidebar.success(fetch_nhtsa.main())
            st.cache_data.clear(); st.rerun()
        except Exception as e:
            st.sidebar.error(f"Pull failed: {e}")


@st.cache_data(show_spinner="Cleaning and tagging complaints…")
def run_pipeline(path, mtime):
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    df, steps, changes = clean(raw)
    return tag(df), steps, changes


def load_decisions():
    if os.path.exists(DECISIONS):
        return pd.read_csv(DECISIONS, dtype=str, keep_default_na=False)
    return pd.DataFrame(columns=["timestamp", "month", "item_type", "item", "decision", "edited_value", "note"])


def save_decision(month, item_type, item, decision, edited="", note=""):
    d = load_decisions()
    d = pd.concat([d, pd.DataFrame([[datetime.now().isoformat(timespec="seconds"), month,
                                     item_type, item, decision, edited, note]], columns=d.columns)])
    d.to_csv(DECISIONS, index=False)


df, steps, changes = run_pipeline(DATA, os.path.getmtime(DATA))
landscape = pd.read_csv(LANDSCAPE, dtype=str, keep_default_na=False)
months = sorted(df.month.dropna().unique())

# ---------------------------------------------------------------- sidebar
month = st.sidebar.selectbox("Scan month", months, index=len(months) - 1)
st.sidebar.caption(f"Data file last updated: {datetime.fromtimestamp(os.path.getmtime(DATA)):%b %d, %Y}")
st.sidebar.markdown("New data arrives automatically on the 1st of each month "
                    "(GitHub Actions runs `fetch_nhtsa.py`). The button above does the same pull on demand.")
scores = score(df, month, landscape)
decisions = load_decisions()
decided = set(decisions[decisions.month == month].item)

st.title(f"Defect Radar — {month}")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Complaints this month", f"{(df.month == month).sum():,}")
c2.metric("Tagged to a tracked defect", f"{((df.month == month) & (df.defect != '')).sum():,}")
c3.metric("Low-confidence tags to review", f"{((df.month == month) & df.needs_review).sum():,}")
c4.metric("Open decisions", f"{len(scores) - len(decided & set(scores.defect))}")

tab_radar, tab_review, tab_tags, tab_log, tab_brief = st.tabs(
    ["Radar", "Review flagged defects", "Check uncertain tags", "Cleaning log", "Brief & decisions"])

# ---------------------------------------------------------------- radar
with tab_radar:
    st.subheader("What changed this month")
    st.caption("Counts are through the selected month. Priority points are shown so "
               "you can see exactly why something ranks high.")
    view = scores[["defect", "priority_pts", "incidents", "this_month", "last_month", "growth_pct",
                   "young_pct", "serious", "repeat_after_dealer_pct", "gap", "top_models", "top_years"]]
    st.dataframe(view, hide_index=True, width='stretch', column_config={
        "priority_pts": st.column_config.ProgressColumn("Priority", min_value=0, max_value=11, format="%d"),
        "growth_pct": st.column_config.NumberColumn("Growth vs last month", format="%d%%"),
        "young_pct": st.column_config.NumberColumn("Cars ≤3 yrs old", format="%d%%"),
        "repeat_after_dealer_pct": st.column_config.NumberColumn("Repeat after dealer repair", format="%d%%"),
        "gap": "Recall/lawsuit gap",
    })
    trend = (df[df.defect != ""].groupby(["month", "defect"]).size()
             .unstack(fill_value=0).loc[:month])
    st.line_chart(trend, height=260)
    with st.expander("How priority points work"):
        st.markdown("- **+3** open gap (no recall or lawsuit) · **+2** partial gap · **+1** unknown\n"
                    "- **+2** 200+ incidents · **+1** 100+ incidents\n"
                    "- **+2** half or more fail on cars ≤3 years old (defect, not wear)\n"
                    "- **+2** 20+ serious incidents (crash, fire, injury, death, medical)\n"
                    "- **+1** 10%+ say it failed again after a dealer repair")

# ---------------------------------------------------------------- review flagged defects
with tab_review:
    st.subheader("Approve, reject, or edit each flagged defect")
    for _, r in scores.iterrows():
        status = "✅ decided" if r.defect in decided else "🟡 needs a decision"
        with st.expander(f"{r.defect} — {r.priority_pts} pts — {status}", expanded=r.defect not in decided and r.priority_pts >= 7):
            a, b = st.columns(2)
            a.markdown(f"**Incidents:** {r.incidents:,} (this month {r.this_month}, last month {r.last_month})  \n"
                       f"**Models / years:** {r.top_models} · {r.top_years}  \n"
                       f"**Cars ≤3 yrs old:** {r.young_pct}% · **Serious:** {r.serious} "
                       f"(crashes {r.crashes}, injured {int(r.injured)})  \n"
                       f"**Repeat after dealer repair:** {r.repeat_after_dealer_pct}%")
            b.markdown(f"**Recall:** {r.get('recall_status', '')}  \n"
                       f"**Investigation:** {r.get('investigation_status', '')}  \n"
                       f"**Lawsuits:** {r.get('lawsuit_status', '')}  \n"
                       f"**Gap:** {r.gap}" + (f"  \n[Source]({r.source})" if isinstance(r.get("source"), str) and r.get("source") else ""))
            sample = df[(df.defect == r.defect) & (df.month <= month)].sample(
                min(3, int(r.incidents)), random_state=1)
            for s in sample.itertuples():
                st.caption(f"#{s.complaint_id} · {s.model} {'' if pd.isna(s.year) else int(s.year)} — {s.complaint_text[:260]}…")
            note = st.text_input("Note / edit (e.g. narrow to 2024–2026 models)", key=f"note_{r.defect}")
            x, y, z = st.columns(3)
            if x.button("Approve → send to Head of Ops", key=f"ap_{r.defect}"):
                save_decision(month, "defect", r.defect, "approved", note=note); st.rerun()
            if y.button("Reject", key=f"rj_{r.defect}"):
                save_decision(month, "defect", r.defect, "rejected", note=note); st.rerun()
            if z.button("Approve with edit", key=f"ed_{r.defect}", disabled=not note):
                save_decision(month, "defect", r.defect, "approved_with_edit", edited=note, note=note); st.rerun()

# ---------------------------------------------------------------- uncertain tags
with tab_tags:
    st.subheader("Tags the scan wasn't sure about")
    st.caption("Partial matches go here instead of being counted silently. Confirm, relabel, or remove.")
    unsure = df[(df.month == month) & df.needs_review & ~df.complaint_id.isin(decided)].head(15)
    if unsure.empty:
        st.success("Nothing uncertain left for this month.")
    labels = sorted({r for r in df.defect.unique() if r}) + ["Not a tracked defect"]
    for s in unsure.itertuples():
        st.markdown(f"**#{s.complaint_id} · {s.make} {s.model} {'' if pd.isna(s.year) else int(s.year)}** — suggested: *{s.defect}* ({s.confidence:.0%})")
        st.caption(s.complaint_text[:400])
        a, b, c = st.columns([1, 2, 1])
        if a.button("Confirm", key=f"cf_{s.complaint_id}"):
            save_decision(month, "tag", s.complaint_id, "confirmed", s.defect); st.rerun()
        new = b.selectbox("Relabel", labels, index=labels.index(s.defect), key=f"rl_{s.complaint_id}", label_visibility="collapsed")
        if c.button("Save label", key=f"sv_{s.complaint_id}"):
            save_decision(month, "tag", s.complaint_id, "relabeled", new); st.rerun()
        st.divider()

# ---------------------------------------------------------------- cleaning log
with tab_log:
    st.subheader("Row counts before and after each cleaning step")
    st.dataframe(steps, hide_index=True, width='stretch')
    st.caption(f"{len(changes):,} individual cell changes logged.")
    st.dataframe(changes.head(500), hide_index=True, width='stretch')

# ---------------------------------------------------------------- brief
with tab_brief:
    st.subheader("Monthly brief for the Head of Ops")
    approved = decisions[(decisions.month == month) & decisions.decision.str.startswith("approved")
                         & (decisions.item_type == "defect")]
    if approved.empty:
        st.info("Approve at least one defect in the Review tab to build the brief.")
    else:
        lines = [f"# Defect Radar brief — {month}", ""]
        for _, a in approved.iterrows():
            r = scores[scores.defect == a["item"]].iloc[0]
            lines += [f"## {r.defect}",
                      f"- {r.incidents:,} incidents ({r.this_month} this month); models: {r.top_models}",
                      f"- {r.young_pct}% on cars ≤3 yrs old; {r.serious} serious; gap: {r.gap}",
                      f"- Recall: {r.get('recall_status','')} · Lawsuits: {r.get('lawsuit_status','')}"]
            if a.edited_value:
                lines.append(f"- Reviewer note: {a.edited_value}")
            lines.append("")
        brief = "\n".join(lines)
        st.markdown(brief)
        st.download_button("Download brief (.md)", brief, file_name=f"defect_brief_{month}.md")
    st.subheader("Decision log")
    st.dataframe(decisions, hide_index=True, width='stretch')
