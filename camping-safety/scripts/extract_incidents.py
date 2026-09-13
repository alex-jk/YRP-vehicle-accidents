"""Take the Relevant=Yes rows from news_candidates_labeled.csv -- which contain a lot
of duplicates, since the same real death often gets covered by multiple outlets and
matched under several mismatched "Year" values -- and consolidate them into a clean
list of distinct incidents using an LLM pass that clusters rows describing the same
event and extracts what's known about each (date, park, names, ages).

This still isn't a verdict: it's the model's best read of scattered, sometimes
inconsistent article snippets. Every incident here still needs a human to open at
least one of its source URLs and confirm the details before adding it to
Ontario_camping_fatalities.csv.

Setup: same as classify_candidates.py (OPENAI_API_KEY in camping-safety/.env).

Usage: python scripts/extract_incidents.py
Reads:  data/news_candidates_labeled.csv (must already have Relevant/Location columns
        from classify_candidates.py)
Writes: data/extracted_incidents.csv -- one row per distinct incident, with the
        source URLs that describe it
"""
import csv
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
IN_PATH = Path(__file__).resolve().parent.parent / "data" / "news_candidates_labeled.csv"
SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "Ontario_camping_fatalities.csv"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "extracted_incidents.csv"

# User-generated/social platforms aren't verifiable sources for a real incident count --
# only cluster from actual news outlets, government pages, etc.
SOCIAL_SOURCE_MARKERS = [
    "facebook", "instagram", "tiktok", "youtube", "reddit", "x ·", "twitter",
    "imdb", "gofundme", "tripadvisor", "spotify",
]

SYSTEM_PROMPT = (
    "You are consolidating news search results into a clean list of distinct real-world "
    "fatality incidents in Ontario, Canada. Many of the numbered rows below describe the "
    "SAME death -- covered by different outlets, or matched under different 'Year' values "
    "because the search that found them wasn't a reliable date filter. Group rows that "
    "describe the same person/event together into one incident, using the location, cause, "
    "names, and details in the title/snippet to tell events apart. Only merge rows you are "
    "confident describe the exact same event -- matching cause and rough year is NOT enough on "
    "its own if the specific location, victim details, or date don't actually line up. A false "
    "split (same event listed twice) is a minor annoyance a human will catch; a false merge "
    "(two different deaths combined into one, or a wrong location attached to a real death) is "
    "much worse and harder to catch, so when unsure, keep rows as SEPARATE incidents.\n"
    "For each DISTINCT incident, extract:\n"
    "- date: best known date, as specific as the sources allow (YYYY-MM-DD, or YYYY-MM, or "
    "just YYYY if that's all that's given). The 'Year' tag in brackets is often WRONG (it's "
    "just a search-match artifact, not a verified publication or event date) -- prefer any date "
    "visible in the URL path (e.g. '/2005/09/08/' means September 8, 2005) or explicitly stated "
    "in the title/snippet over the bracketed Year tag.\n"
    "- location: the specific park/lake/campground name\n"
    "- cause: Drowning, Bear Attack, or Falling Tree\n"
    "- names: victim name(s) if stated, else empty string\n"
    "- age_gender: age/gender if stated, else empty string\n"
    "- summary: one sentence of what happened\n"
    "- source_indexes: list of the row index numbers (the leading number of each line below) "
    "that describe this same incident\n"
    "Respond with JSON only: {\"incidents\": [{\"date\":..,\"location\":..,\"cause\":..,"
    "\"names\":..,\"age_gender\":..,\"summary\":..,\"source_indexes\":[..]}]}"
)


def extract(api_key, rows):
    lines = [
        f"{i}: [{r['Year']} | {r['Cause']} | {r.get('Location', '')}] {r['Title']} -- {r['Snippet']} "
        f"(url: {r['URL']})"
        for i, r in enumerate(rows)
    ]
    resp = requests.post(
        CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": MODEL,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines)},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content).get("incidents", [])


STOPWORDS = {"ontario", "canada", "provincial", "park", "parks", "lake", "county", "the", "of", "near", ""}


def is_likely_seed_duplicate(incident, seed_rows):
    """Rough heuristic: same cause, and location or a name-word overlaps a seed row.
    Common words like 'ontario'/'park' are excluded so every bear attack doesn't
    spuriously match every other one just because they're all in Ontario parks."""
    cause = (incident.get("cause") or "").lower()
    location_words = set((incident.get("location") or "").lower().replace(",", " ").split()) - STOPWORDS
    name_words = set((incident.get("names") or "").lower().replace(",", " ").replace(";", " ").split()) - STOPWORDS
    for seed in seed_rows:
        if cause and cause not in seed.get("Cause", "").lower():
            continue
        seed_location = set(seed.get("Location", "").lower().replace(",", " ").split()) - STOPWORDS
        seed_names = set(seed.get("Name", "").lower().replace(",", " ").replace(";", " ").split()) - STOPWORDS
        if (location_words & seed_location) or (name_words & seed_names):
            return f"possibly {seed.get('Date', '')} {seed.get('Name', '')}"
    return ""


def merge_by_shared_url(incidents):
    """Two 'distinct' incidents that cite the exact same source URL can't actually be
    distinct -- that's not a fuzzy judgment call like the LLM's clustering, it's a
    contradiction. Union-merge any incidents sharing at least one URL, keeping the
    first incident's fields (whichever was extracted first) and the union of URLs."""
    groups = []  # list of {"urls": set(...), "incidents": [...]}
    for inc in incidents:
        urls = set(inc["_urls"])
        matched = [g for g in groups if g["urls"] & urls]
        if not matched:
            groups.append({"urls": urls, "incidents": [inc]})
            continue
        target = matched[0]
        target["urls"] |= urls
        target["incidents"].append(inc)
        for other in matched[1:]:
            target["urls"] |= other["urls"]
            target["incidents"].extend(other["incidents"])
            groups.remove(other)

    merged = []
    for g in groups:
        primary = g["incidents"][0]
        primary["_urls"] = [u for inc in g["incidents"] for u in inc["_urls"]]
        primary["_urls"] = list(dict.fromkeys(primary["_urls"]))  # dedupe, keep order
        merged.append(primary)
    return merged


def main():
    load_dotenv(ENV_PATH)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit(f"Set OPENAI_API_KEY in {ENV_PATH} -- see classify_candidates.py's setup steps.")

    with open(IN_PATH, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    relevant = [r for r in all_rows if r.get("Relevant") == "Yes"]
    if not relevant:
        sys.exit(f"No Relevant=Yes rows found in {IN_PATH} -- run classify_candidates.py first.")

    rows = [
        r for r in relevant
        if not any(m in r.get("Source", "").lower() for m in SOCIAL_SOURCE_MARKERS)
    ]
    print(f"Excluded {len(relevant) - len(rows)} social/UGC-sourced row(s), kept {len(rows)} from real outlets.")
    if not rows:
        sys.exit("Nothing left after filtering out social/UGC sources.")

    print(f"Clustering {len(rows)} relevant rows into distinct incidents...")
    incidents = extract(api_key, rows)

    seed_rows = []
    if SEED_PATH.exists():
        with open(SEED_PATH, newline="", encoding="utf-8") as f:
            seed_rows = list(csv.DictReader(f))

    # Compute each incident's deduped URL list.
    for inc in incidents:
        seen = set()
        urls = []
        for i in inc.get("source_indexes", []):
            if 0 <= i < len(rows) and rows[i]["URL"] not in seen:
                seen.add(rows[i]["URL"])
                urls.append(rows[i]["URL"])
        inc["_urls"] = urls

    merged = merge_by_shared_url(incidents)
    if len(merged) < len(incidents):
        print(f"Merged {len(incidents) - len(merged)} incident(s) that shared an identical source URL "
              "(same event split apart by inconsistent Year tags).")

    fieldnames = ["date", "cause", "location", "names", "age_gender", "summary",
                  "source_count", "source_urls", "possible_duplicate_of_seed"]
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for inc in merged:
            dup = is_likely_seed_duplicate(inc, seed_rows)
            writer.writerow({
                "date": inc.get("date", ""),
                "cause": inc.get("cause", ""),
                "location": inc.get("location", ""),
                "names": inc.get("names", ""),
                "age_gender": inc.get("age_gender", ""),
                "summary": inc.get("summary", ""),
                "source_count": len(inc["_urls"]),
                "source_urls": " | ".join(inc["_urls"]),
                "possible_duplicate_of_seed": dup,
            })

    single_source = sum(1 for inc in merged if len(inc["_urls"]) == 1)
    print(f"\n{len(merged)} distinct incident(s) written to {OUT_PATH}")
    print(f"{single_source} of those are backed by only a single source -- treat with extra caution.")
    print("Still needs human review: open at least one source URL per incident to confirm")
    print("before adding it to Ontario_camping_fatalities.csv.")


if __name__ == "__main__":
    main()
