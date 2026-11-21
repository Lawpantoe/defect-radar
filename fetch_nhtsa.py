"""Monthly download of new NHTSA complaints -> data/complaints_live.csv.

Runs automatically (GitHub Actions, 1st of each month) or by hand:
    python fetch_nhtsa.py

Source: NHTSA ODI complaints flat file (tab-delimited, no header row)
https://catalog.data.gov/dataset/nhtsas-office-of-defects-investigation-odi-complaints-complaints-flat-file

BEFORE FIRST USE: confirm the column order below against NHTSA's field
description file (CMPL.txt, published alongside FLAT_CMPL.zip). The list here
follows NHTSA's documented layout but should be verified once.
"""
import io
import sys
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

URL = "https://static.nhtsa.gov/odi/ffdd/cmpl/FLAT_CMPL.zip"
FALLBACK_URL = "http://www-odi.nhtsa.dot.gov/downloads/folders/Complaints/FLAT_CMPL.zip"
MAKES = ["FORD", "HONDA", "HYUNDAI", "TOYOTA", "TESLA"]  # scope of the case; widen anytime

NHTSA_COLUMNS = [
    "CMPLID", "ODINO", "MFR_NAME", "MAKETXT", "MODELTXT", "YEARTXT", "CRASH",
    "FAILDATE", "FIRE", "INJURED", "DEATHS", "COMPDESC", "CITY", "STATE", "VIN",
    "DATEA", "LDATE", "MILES", "OCCURENCES", "CDESCR", "CMPL_TYPE",
    "POLICE_RPT_YN", "PURCH_DT", "ORIG_OWNER_YN", "ANTI_BRAKES_YN",
    "CRUISE_CONT_YN", "NUM_CYLS", "DRIVE_TRAIN", "FUEL_SYS", "FUEL_TYPE",
    "TRANS_TYPE", "VEH_SPEED", "DOT", "TIRE_SIZE", "LOC_OF_TIRE",
    "TIRE_FAIL_TYPE", "ORIG_EQUIP_YN", "MANUF_DT", "SEAT_TYPE",
    "RESTRAINT_TYPE", "DEALER_NAME", "DEALER_TEL", "DEALER_CITY",
    "DEALER_STATE", "DEALER_ZIP", "PROD_TYPE", "REPAIRED_YN", "MEDICAL_ATTN",
    "VEHICLES_TOWED_YN",
]

# Map NHTSA names -> the column names used in the case file and the app.
RENAME = {
    "CMPLID": "complaint_id", "MFR_NAME": "manufacturer", "MAKETXT": "make",
    "MODELTXT": "model", "YEARTXT": "year", "CRASH": "crash", "FAILDATE": "fail_date",
    "FIRE": "fire", "INJURED": "injured", "DEATHS": "deaths", "COMPDESC": "component",
    "CITY": "city", "STATE": "state", "DATEA": "date_received", "MILES": "mileage",
    "CDESCR": "complaint_text", "CMPL_TYPE": "complaint_source",
    "POLICE_RPT_YN": "police_report", "MEDICAL_ATTN": "medical_attention",
    "VEHICLES_TOWED_YN": "vehicle_towed", "PROD_TYPE": "product_type",
}
# Extra NHTSA fields that answer questions our case file could not:
#   ODINO       shared complaint number -> merge multi-row complaints exactly
#   PURCH_DT    purchase date           -> when the car was sold
#   MANUF_DT    manufacture date        -> production-period clustering
#   REPAIRED_YN / DEALER_NAME           -> who repaired it
KEEP_EXTRA = ["ODINO", "PURCH_DT", "MANUF_DT", "ORIG_OWNER_YN", "REPAIRED_YN", "DEALER_NAME"]


OUT_FILE = "data/complaints_live.csv"   # separate from the case dataset


def download() -> bytes:
    errors = []
    for url in (URL, FALLBACK_URL):
        try:
            r = requests.get(url, timeout=600)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError("Could not download the NHTSA complaints file. " + " | ".join(errors))


def main(months_back: int = 6) -> str:
    """Download NHTSA's latest complaints file and keep the last `months_back` months.

    NHTSA publishes ONE cumulative file at the same address and refreshes it
    with new complaints, so each run gets the newest version automatically.
    Returns a short status message."""
    raw = download()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = [n for n in z.namelist() if n.lower().endswith(".txt")][0]
        df = pd.read_csv(z.open(name), sep="\t", header=None, names=NHTSA_COLUMNS,
                         dtype=str, keep_default_na=False, on_bad_lines="skip",
                         encoding="latin-1", quoting=3)
    cutoff = (date.today().replace(day=1) - timedelta(days=31 * months_back)).strftime("%Y%m%d")
    df = df[df.MAKETXT.isin(MAKES) & (df.DATEA >= cutoff)]
    out = df.rename(columns=RENAME)[list(RENAME.values()) + KEEP_EXTRA]
    out.to_csv(OUT_FILE, index=False)
    return f"Saved {len(out):,} complaints received since {cutoff} to {OUT_FILE}"


if __name__ == "__main__":
    try:
        print(main())
    except RuntimeError as e:
        sys.exit(str(e))
