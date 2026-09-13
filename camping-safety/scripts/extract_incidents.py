"""Turn the Relevant=Yes rows of news_candidates_labeled.csv into a list of
distinct fatality incidents.

Two stages:

1. An LLM pass reads batches of article titles/snippets and groups the rows that
   describe the same death, extracting date, place, cause, names and a summary.
   Batching means the same real event is often described two or three times, once
   per batch, so stage 1 deliberately over-produces.

2. A deterministic dedup pass (incident_merge.py) collapses those duplicates
   using the combined evidence -- shared source URLs, victim names, place names,
   dates recovered from URL paths, and the wording of the descriptions. Two rows
   citing different outlets are still one incident if they describe the same
   death. Hard contradictions (different named victims, different ages, two dates
   each confirmed by a source URL) veto a merge.

This still isn't a verdict. Every incident needs a human to open at least one
source URL and confirm it before it goes into Ontario_camping_fatalities.csv --
see the `flags` column for the rows that most need it.

Setup: OPENAI_API_KEY in camping-safety/.env (see classify_candidates.py).

Usage:
  python scripts/extract_incidents.py              full run (needs the API key)
  python scripts/extract_incidents.py --remerge    redo stage 2 only, offline
  python scripts/extract_incidents.py --no-adjudicate   skip the LLM tie-breaker

Reads:  data/news_candidates_labeled.csv  (or data/extracted_incidents.csv with --remerge)
Writes: data/extracted_incidents.csv
"""
import csv
import json
import os
import sys
from pathlib import Path

import incident_merge as im

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"
CHUNK_SIZE = 60          # rows per extraction call; keeps each prompt readable
MAX_ADJUDICATIONS = 40   # cap the tie-breaker so a bad run cannot burn the quota

BASE = Path(__file__).resolve().parent.parent
ENV_PATH = BASE / ".env"
IN_PATH = BASE / "data" / "news_candidates_labeled.csv"
SEED_PATH = BASE / "data" / "Ontario_camping_fatalities.csv"
OUT_PATH = BASE / "data" / "extracted_incidents.csv"

FIELDNAMES = [
    "date", "date_source", "cause", "location", "park_scope", "names", "age_gender", "summary",
    "source_count", "source_urls", "merged_from", "merge_basis", "flags",
    "possible_duplicate_of_seed", "model_dates",
]

# User-generated platforms aren't verifiable sources for a real incident count.
SOCIAL_SOURCE_MARKERS = [
    "facebook", "instagram", "tiktok", "youtube", "reddit", "x ·", "twitter",
    "imdb", "gofundme", "tripadvisor", "spotify",
]

SYSTEM_PROMPT = (
    "You are consolidating news search results into a list of distinct real-world "
    "fatality incidents in Ontario, Canada. Several numbered rows below may describe the "
    "SAME death, covered by different outlets or matched under different 'Year' values. "
    "Group those together into one incident.\n"
    "Only merge rows you are confident describe the exact same event: matching cause and "
    "rough year is NOT enough on its own if the specific location or victim details don't "
    "line up. When unsure, keep them SEPARATE -- a later deduplication step catches "
    "leftover duplicates, but it cannot undo a wrong merge.\n"
    "Never invent a detail that isn't in the text. If the place, date, name or age is not "
    "stated, leave that field empty rather than guessing a plausible park name.\n"
    "For each DISTINCT incident, extract:\n"
    "- date: best known date (YYYY-MM-DD, or YYYY-MM, or YYYY). The bracketed 'Year' tag is "
    "often WRONG -- it is a search-match artifact, not a verified date. Prefer a date visible "
    "in the URL path (e.g. '/2005/09/08/' means September 8, 2005) or stated in the text.\n"
    "- location: the specific park/lake/campground name, exactly as the sources name it, or "
    "empty if none is stated\n"
    "- cause: Drowning, Bear Attack, or Falling Tree\n"
    "- names: victim name(s) if stated, else empty string\n"
    "- age_gender: age/gender if stated, else empty string\n"
    "- summary: one sentence of what happened\n"
    "- source_indexes: the row index numbers (the leading number of each line) for this incident\n"
    "Respond with JSON only: {\"incidents\": [{\"date\":..,\"location\":..,\"cause\":..,"
    "\"names\":..,\"age_gender\":..,\"summary\":..,\"source_indexes\":[..]}]}"
)

ADJUDICATE_PROMPT = (
    "Each numbered item below is a PAIR of candidate fatality incidents that share a cause "
    "and a location. Decide, for each pair, whether both descriptions refer to the SAME single "
    "death or to two DIFFERENT deaths that happened at the same place.\n"
    "Answer same=true only when the evidence positively indicates one event. Two separate "
    "drownings at one popular park in different years are DIFFERENT. When the pair is simply "
    "vague, answer same=false.\n"
    "Respond with JSON only: {\"pairs\": [{\"id\": <int>, \"same\": true|false}]}"
)


def _post(api_key, messages):
    import requests
    resp = requests.post(
        CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": MODEL,
            "response_format": {"type": "json_object"},
            "messages": messages,
        },
        timeout=180,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])


def extract_chunk(api_key, rows, offset):
    """Cluster one chunk. Row indexes shown to the model are global, so the
    source_indexes it returns map straight back into the full row list."""
    lines = [
        f"{offset + i}: [{r['Year']} | {r['Cause']} | {r.get('Location', '')}] {r['Title']} "
        f"-- {r['Snippet']} (url: {r['URL']})"
        for i, r in enumerate(rows)
    ]
    payload = _post(api_key, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ])
    return payload.get("incidents", [])


def adjudicate(api_key, merged, pairs):
    """Ask the model to break ties on near-miss pairs the rules left separate.
    Returns the index pairs it judged to be the same death."""
    pairs = pairs[:MAX_ADJUDICATIONS]
    if not pairs:
        return []
    lines = []
    for n, (i, j, _) in enumerate(pairs):
        a, b = merged[i], merged[j]
        lines.append(
            f"{n}:\n"
            f"  A: {a.get('date', '')} | {a.get('location', '')} | {a.get('names', '')} "
            f"{a.get('age_gender', '')} | {a.get('summary', '')}\n"
            f"  B: {b.get('date', '')} | {b.get('location', '')} | {b.get('names', '')} "
            f"{b.get('age_gender', '')} | {b.get('summary', '')}"
        )
    payload = _post(api_key, [
        {"role": "system", "content": ADJUDICATE_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ])
    confirmed = []
    for entry in payload.get("pairs", []):
        try:
            n = int(entry["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if entry.get("same") and 0 <= n < len(pairs):
            confirmed.append((pairs[n][0], pairs[n][1]))
    return confirmed


def load_seed():
    if not SEED_PATH.exists():
        return []
    with open(SEED_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_output(merged, seed_rows, evidence_by_url):
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for rec in merged:
            evidence = " ".join(
                evidence_by_url.get(im.normalize_url(u), "") for u in rec["_urls"]
            )
            writer.writerow({
                "date": rec["date"],
                "date_source": rec["date_source"],
                "cause": rec["cause"],
                "location": rec["location"],
                "park_scope": im.park_scope(rec),
                "names": rec["names"],
                "age_gender": rec["age_gender"],
                "summary": rec["summary"],
                "source_count": len(rec["_urls"]),
                "source_urls": " | ".join(rec["_urls"]),
                "merged_from": rec["merged_from"],
                "merge_basis": rec["merge_basis"],
                "flags": "; ".join(im.flags_for(rec, evidence)),
                "possible_duplicate_of_seed": im.is_likely_seed_duplicate(rec, seed_rows),
                # What the LLM originally guessed, kept so --remerge stays lossless.
                "model_dates": "; ".join(rec.get("_model_dates", [])),
            })


def report(merged, stats, pairs, evidence_by_url):
    print(f"\n{stats['output']} distinct incident(s) written to {OUT_PATH}")
    print(f"  collapsed {stats['collapsed']} duplicate row(s) out of {stats['input']}")

    counts = {}
    for rec in merged:
        evidence = " ".join(evidence_by_url.get(im.normalize_url(u), "") for u in rec["_urls"])
        for flag in im.flags_for(rec, evidence):
            counts[flag] = counts.get(flag, 0) + 1
    if counts:
        print("\nRows needing attention:")
        for flag, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {n:3d}  {flag}")

    scope = {}
    for rec in merged:
        key = (im.park_scope(rec), rec["cause"])
        scope[key] = scope.get(key, 0) + 1
    print("\nBy scope and cause (claimed location, not verified):")
    for (where, cause), n in sorted(scope.items()):
        print(f"  {n:3d}  {cause} in {where}")

    if pairs:
        print(f"\n{len(pairs)} pair(s) share a cause and place but were left separate:")
        for i, j, why in pairs:
            print(f"  - {merged[i]['date']} vs {merged[j]['date']} at "
                  f"{merged[i]['location'] or '?'} ({why})")

    print("\nStill needs human review: open at least one source URL per incident")
    print("before adding it to Ontario_camping_fatalities.csv.")


def run_remerge():
    """Redo the dedup stage on an existing extracted_incidents.csv. No API key
    needed, so the rules can be re-tuned without paying for another LLM run."""
    if not OUT_PATH.exists():
        sys.exit(f"{OUT_PATH} not found -- run a full extraction first.")
    with open(OUT_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"Re-merging {len(rows)} row(s) from {OUT_PATH}...")
    merged, stats = im.merge_incidents(rows)
    pairs = im.near_miss_pairs(merged)
    write_output(merged, load_seed(), {})
    report(merged, stats, pairs, {})


def run_full(adjudicate_enabled):
    from dotenv import load_dotenv
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
        if not any(m in (r.get("Source") or "").lower() for m in SOCIAL_SOURCE_MARKERS)
    ]
    print(f"Excluded {len(relevant) - len(rows)} social/UGC-sourced row(s), "
          f"kept {len(rows)} from real outlets.")
    if not rows:
        sys.exit("Nothing left after filtering out social/UGC sources.")

    raw = []
    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start:start + CHUNK_SIZE]
        print(f"Clustering rows {start}-{start + len(chunk) - 1} of {len(rows)}...")
        for inc in extract_chunk(api_key, chunk, start):
            seen, urls = set(), []
            for i in inc.get("source_indexes", []):
                if isinstance(i, int) and 0 <= i < len(rows) and rows[i]["URL"] not in seen:
                    seen.add(rows[i]["URL"])
                    urls.append(rows[i]["URL"])
            inc["_urls"] = urls
            raw.append(inc)
    print(f"Stage 1 produced {len(raw)} candidate incident(s) across "
          f"{(len(rows) + CHUNK_SIZE - 1) // CHUNK_SIZE} batch(es).")

    # Article text, so a place name can be corroborated against what the
    # sources actually said and not just against the URL slug.
    evidence_by_url = {
        im.normalize_url(r["URL"]): f"{r.get('Title', '')} {r.get('Snippet', '')} {r.get('Location', '')}"
        for r in rows
    }

    merged, stats = im.merge_incidents(raw, evidence_lookup=evidence_by_url)
    print(f"Stage 2 collapsed {stats['collapsed']} duplicate(s) -> {stats['output']} incident(s).")

    pairs = im.near_miss_pairs(merged, evidence_by_url)
    if adjudicate_enabled and pairs:
        print(f"Adjudicating {min(len(pairs), MAX_ADJUDICATIONS)} near-miss pair(s)...")
        confirmed = adjudicate(api_key, merged, pairs)
        if confirmed:
            merged, stats2 = im.merge_incidents(merged, extra_pairs=confirmed,
                                                evidence_lookup=evidence_by_url)
            stats["output"] = stats2["output"]
            stats["collapsed"] += stats2["collapsed"]
            print(f"  tie-breaker merged {stats2['collapsed']} more pair(s).")
        pairs = im.near_miss_pairs(merged, evidence_by_url)
    write_output(merged, load_seed(), evidence_by_url)
    report(merged, stats, pairs, evidence_by_url)


def main():
    args = set(sys.argv[1:])
    unknown = args - {"--remerge", "--no-adjudicate"}
    if unknown:
        sys.exit(f"Unknown option(s): {', '.join(sorted(unknown))}\n{__doc__}")
    if "--remerge" in args:
        run_remerge()
    else:
        run_full(adjudicate_enabled="--no-adjudicate" not in args)


if __name__ == "__main__":
    main()
