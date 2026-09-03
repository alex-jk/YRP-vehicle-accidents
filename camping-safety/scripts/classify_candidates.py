"""Send each row of news_candidates.csv to a cheap OpenAI model to pre-label whether
it plausibly describes a fatality that happened while camping, or at least within an
Ontario provincial park -- so you only have to eyeball the ones flagged as relevant
instead of all of them.

This is a pre-filter, not a verdict: the model only sees a title + snippet, which is
often not enough to be sure. Treat "Relevant: Yes" as "worth opening the link," and
still confirm details yourself before adding anything to Ontario_camping_fatalities.csv.

Setup:
1. Get an API key at https://platform.openai.com/api-keys.
2. Add OPENAI_API_KEY=... to camping-safety/.env (alongside SERPAPI_KEY).
3. Free/cheap tier note: this uses gpt-4o-mini, priced per-token -- classifying a
   few hundred short rows costs a few cents total.

Usage: python scripts/classify_candidates.py
Reads:  data/news_candidates.csv
Writes: data/news_candidates_labeled.csv (same columns, plus Relevant and Reason,
        sorted so Relevant=Yes rows come first)
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
BATCH_SIZE = 15

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
IN_PATH = Path(__file__).resolve().parent.parent / "data" / "news_candidates.csv"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "news_candidates_labeled.csv"

SYSTEM_PROMPT = (
    "You are screening Google search results for a dataset of Ontario fatalities. "
    "A row is RELEVANT only if the title/snippet indicates a specific person died "
    "(not just injured, not a statistics/report page, not an unrelated social post) "
    "AND the death happened while camping, or at least within an Ontario provincial park. "
    "If the text is ambiguous or doesn't clearly describe a death, mark it not relevant -- "
    "a human will still check every relevant row's actual article before using it. "
    "Respond with JSON only: {\"results\": [{\"index\": <int>, \"relevant\": true|false, \"reason\": \"<one short phrase>\"}]}"
)


def classify_batch(api_key, batch):
    lines = [
        f"{row['index']}: [{row['Year']} | {row['Cause']}] {row['Title']} -- {row['Snippet']}"
        for row in batch
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
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return {r["index"]: r for r in json.loads(content).get("results", [])}


def main():
    load_dotenv(ENV_PATH)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit(f"Set OPENAI_API_KEY in {ENV_PATH} -- see the setup steps at the top of this script.")

    with open(IN_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for i, row in enumerate(rows):
        row["index"] = i

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        try:
            labels = classify_batch(api_key, batch)
        except (requests.RequestException, KeyError, json.JSONDecodeError) as exc:
            print(f"Batch {start}-{start + len(batch)} FAILED: {exc}", file=sys.stderr)
            labels = {}

        for row in batch:
            label = labels.get(row["index"])
            row["Relevant"] = "Yes" if label and label.get("relevant") else "No"
            row["Reason"] = label.get("reason", "") if label else "classification failed"

        print(f"{start + len(batch)}/{len(rows)} classified")

    rows.sort(key=lambda r: r["Relevant"] != "Yes")

    fieldnames = ["Year", "Cause", "Title", "Source", "URL", "Snippet", "Relevant", "Reason"]
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    relevant_count = sum(1 for r in rows if r["Relevant"] == "Yes")
    print(f"\n{relevant_count} of {len(rows)} rows flagged Relevant. Written to {OUT_PATH}")


if __name__ == "__main__":
    main()
