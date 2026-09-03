"""Loop Google search over each year and cause-of-death keyword, looking for news
coverage of Ontario camping / provincial park fatalities, and save the results to
a CSV for manual review.

Uses SerpApi (https://serpapi.com/), which proxies real Google search results
through a single API key -- no Google Cloud project/billing/enablement setup
required (that path was tried first and got stuck in an unresolved Google-side
account issue). Setup:

1. Sign up at https://serpapi.com/users/sign_up (free tier: 100 searches/month).
2. Copy your API key from https://serpapi.com/manage-api-key.
3. Copy camping-safety/.env.example to camping-safety/.env and set SERPAPI_KEY.
   .env is gitignored -- it never gets committed.
4. Free tier is 100 searches/month. Each (year x cause) combination pages up to
   PAGES_PER_QUERY times (stopping early once a page returns fewer than 10 results),
   so a full run costs up to (years x causes x PAGES_PER_QUERY) searches --
   sized by default to fit one month's free quota for the default year range.

Note on Google's own limits: Google deprecated returning more than ~10 organic
results per request back in 2023 -- asking for more per page (a "num" parameter
above 10) does nothing now. The only way to get more than 10 results for a query
is pagination (separate requests at result offsets 10, 20, 30...), which is what
PAGES_PER_QUERY does here, at the cost of one more search per extra page. Google
also stops serving genuinely new results after some point regardless (often within
the first 100-200), no matter how many pages you request -- the "About 518,000
results" style estimate Google shows is a rough index-match count, not something
you can actually page through.

Results are a keyword net, not a verdict -- open each link and confirm the death
happened while camping, or at least within a provincial park, before adding it to
Ontario_camping_fatalities.csv.

Usage: python scripts/fetch_google_search_candidates.py [start_year] [end_year]
  Defaults to 2011 through the current year.
"""
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

SEARCH_URL = "https://serpapi.com/search"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "news_candidates.csv"
PAGES_PER_QUERY = 3
RESULTS_PER_PAGE = 10

LOCATION = '("provincial park" OR "Ontario Parks" OR OPP OR camping OR campground OR campsite)'

QUERIES = {
    "Drowning": f'Ontario {LOCATION} (drowning OR drowned)',
    "Bear Attack": f'Ontario {LOCATION} "bear attack"',
    "Falling Tree": f'Ontario {LOCATION} ("tree fell" OR "tree crushed" OR "tree collapsed")',
}


def search_year_cause(api_key, query, year):
    """Paginate up to PAGES_PER_QUERY pages, stopping early if a page isn't full."""
    all_items = []
    seen_urls = set()
    for page in range(PAGES_PER_QUERY):
        try:
            resp = requests.get(SEARCH_URL, params={
                "engine": "google",
                "q": f"{query} {year}",
                "api_key": api_key,
                "start": page * RESULTS_PER_PAGE,
            }, timeout=30)
        except requests.RequestException as exc:
            print(f"  FAILED (network error): {exc}")
            break

        if resp.status_code != 200:
            print(f"  FAILED ({resp.status_code}): {resp.text[:200]}")
            break

        data = resp.json()
        if "error" in data:
            print(f"  FAILED: {data['error']}")
            break

        items = data.get("organic_results", [])
        if not items:
            break  # a genuinely empty page means we've actually run out

        for item in items:
            url = item.get("link")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_items.append(item)

        # Google's own pagination info, not result count, tells us if there's more --
        # a page can return fewer than RESULTS_PER_PAGE organic slots (other content
        # like "People also ask" crowds them out) while later pages still have results.
        if "next" not in data.get("pagination", {}):
            break

    return all_items


def main():
    load_dotenv(ENV_PATH)
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        sys.exit(f"Set SERPAPI_KEY in {ENV_PATH} -- see the setup steps at the top of this script.")

    args = sys.argv[1:]
    start_year = int(args[0]) if len(args) > 0 else 2011
    end_year = int(args[1]) if len(args) > 1 else datetime.now().year

    fieldnames = ["Year", "Cause", "Title", "Source", "URL", "Snippet"]
    total = 0
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for year in range(start_year, end_year + 1):
            for cause, query in QUERIES.items():
                items = search_year_cause(api_key, query, year)
                print(f"{year} | {cause:<14} | {len(items)} result(s)")
                for item in items:
                    writer.writerow({
                        "Year": year,
                        "Cause": cause,
                        "Title": item.get("title", ""),
                        "Source": item.get("source", ""),
                        "URL": item.get("link", ""),
                        "Snippet": item.get("snippet", "").replace("\n", " "),
                    })
                    total += 1
                f.flush()  # so a stall or Ctrl+C after this point doesn't lose what's already written

    print(f"\n{total} result(s) written to {OUT_PATH}")


if __name__ == "__main__":
    main()
