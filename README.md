# Defect Radar

Monthly scan of NHTSA vehicle complaints that flags possible mass-action candidates. The scan runs on its own; a person approves, rejects, or edits every flag.

Built for the ClaimsHero Operations Associate case (November 2026).

## Run it on your computer

```bash
pip install -r requirements.txt
streamlit run app.py
```

It opens in your browser. It uses the case dataset in `data/` (15,000 complaints, Jun–Oct 2026). Click **Pull latest data from NHTSA** in the sidebar to download live data; then switch the sidebar **Data** choice to "Live NHTSA pull".

## How the monthly flow works

1. **Pull (automatic).** `.github/workflows/monthly_scan.yml` runs `fetch_nhtsa.py` on the 1st of each month. It downloads NHTSA's public complaints file, keeps recent complaints for the tracked makes, and saves `data/complaints_live.csv` back to the repo (the case dataset is never overwritten). There's also a manual **Run workflow** button in GitHub.
2. **Clean.** `pipeline.clean()` applies the same approved rules from the case, logging row counts at each step (15,000 → 11,248 on the case data).
3. **Tag.** `pipeline.tag()` labels each complaint with a defect and a confidence score. Partial matches go to a review queue instead of being counted silently. *(Prototype uses transparent keyword rules; production would call an LLM.)*
4. **Score.** `pipeline.score()` ranks defects by volume, growth vs. last month, share failing on cars ≤3 years old, severity, repeat failures after dealer repair, and recall/lawsuit gap. Priority points are shown so the reviewer sees why.
5. **Review.** In the app: approve / reject / approve-with-edit each defect, and confirm or relabel uncertain tags. Every decision is saved to `data/decisions.csv`.
6. **Brief.** Approved defects become a downloadable one-page brief for the Head of Ops.

## Files

| File | What it does |
|---|---|
| `app.py` | Streamlit review app |
| `pipeline.py` | Cleaning, tagging, scoring |
| `fetch_nhtsa.py` | Monthly NHTSA download |
| `data/landscape.csv` | Recall / investigation / lawsuit status per defect, with sources |
| `.github/workflows/monthly_scan.yml` | Schedules the monthly pull |

## Before production

- Confirm `fetch_nhtsa.py`'s download URL and column order against NHTSA's field description file (`CMPL.txt`). The prototype was not able to test the live download.
- If the full file includes NHTSA's shared complaint number (`ODINO`), the pipeline merges multi-row complaints on it directly. Fields like purchase date, manufacture date, and repairing dealer would also become available.
- Replace keyword tagging with an LLM call that returns a defect label + confidence.
- Automate `landscape.csv` from NHTSA's recalls/investigations API plus litigation alerts.
