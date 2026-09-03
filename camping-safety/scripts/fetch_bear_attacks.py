"""Pull Ontario entries from Wikipedia's "List of fatal bear attacks in North America"
and stage any not yet in the CSV for manual review.

Data source: https://en.wikipedia.org/wiki/List_of_fatal_bear_attacks_in_North_America
(fetched via the MediaWiki API so the tables can be parsed as rendered HTML rather than wikitext)

Bear attacks are the one cause in this dataset with an existing, sourced, dated public
list to start from. Drownings, falling trees, and other causes have no equivalent --
those have to be researched by hand from Lifesaving Society Ontario Drowning Reports,
OPP news releases, coroner's inquest verdicts, and news coverage (see ../README.md).

Not every Ontario row from this list belongs in the CSV: this dataset is scoped to
deaths that happened while camping or at least within a provincial park, and some
Wikipedia entries (e.g. someone attacked while berry-picking, or on a private cottage
island) don't fit that. This script only prints candidates -- it does not append to
the CSV itself. Review each one against the scope before adding it by hand.

Usage: python scripts/fetch_bear_attacks.py
"""
import csv
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

WIKI_API = "https://en.wikipedia.org/w/api.php"
PAGE_TITLE = "List_of_fatal_bear_attacks_in_North_America"
USER_AGENT = "ontario-camping-safety-research/1.0"
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "Ontario_camping_fatalities.csv"

ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
SUP_RE = re.compile(r"<sup\b.*?</sup>", re.S)
BR_RE = re.compile(r"<br\s*/?>", re.I)
TAG_RE = re.compile(r"<[^>]+>")


def fetch_page_html():
    params = {
        "action": "parse",
        "page": PAGE_TITLE,
        "format": "json",
        "prop": "text",
        "formatversion": 2,
    }
    url = WIKI_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    return data["parse"]["text"]


def clean_cell(raw):
    text = SUP_RE.sub("", raw)  # drop citation superscripts, e.g. [25]
    text = BR_RE.sub("; ", text)  # multi-victim cells separate entries with <br>
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    return " ".join(text.split())


def normalize_date(raw):
    """'October 11, 1991' -> '1991-10-11'; leave anything unparsable (e.g. 'June 1881') as-is."""
    for fmt in ("%B %d, %Y", "%B %Y"):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return dt.strftime("%Y-%m-%d") if fmt == "%B %d, %Y" else dt.strftime("%Y-%m")
    return raw


def find_ontario_rows(page_html):
    rows = []
    for row_html in ROW_RE.findall(page_html):
        cells = CELL_RE.findall(row_html)
        if len(cells) < 4:
            continue
        date, victim, bear_type, location = (clean_cell(c) for c in cells[:4])
        if "Ontario" in location:
            rows.append({
                "date": normalize_date(date),
                "victim": victim,
                "type": bear_type,
                "location": location,
            })
    return rows


def load_existing_dates():
    dates = set()
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Date"):
                dates.add(row["Date"].strip())
    return dates


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    existing = load_existing_dates()
    page_html = fetch_page_html()
    rows = find_ontario_rows(page_html)
    new_rows = [r for r in rows if r["date"] not in existing]

    print(f"{len(new_rows)} Ontario bear-attack row(s) from Wikipedia not yet in the CSV:\n")
    for r in new_rows:
        print(f"{r['date']} | {r['victim']} | {r['type']} | {r['location']}")

    if new_rows:
        print(
            "\nCheck each one against the dataset's scope (camping, or at least within a "
            "provincial park) before adding it to the CSV -- not all bear-attack fatalities do."
        )
    print("\nSource: https://en.wikipedia.org/wiki/List_of_fatal_bear_attacks_in_North_America")


if __name__ == "__main__":
    main()
