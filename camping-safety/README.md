Dataset on fatalities in Ontario while camping, or at least while within a provincial park -- drownings, bear/wildlife attacks, falling trees, and similar causes. Target window is roughly the past 15-20 years (2005-present), though a couple of older, well-documented reference cases are kept in the CSV too.

## Why this isn't a simple data pull

Unlike the YRP vehicle accidents project, there's no single public feed or API for this. Building `data/Ontario_camping_fatalities.csv` means combining a few different sources, some automatable and some not:

- **Bear/wildlife attacks** -- Wikipedia's [List of fatal bear attacks in North America](https://en.wikipedia.org/wiki/List_of_fatal_bear_attacks_in_North_America) is sourced and dated, and is scrapable. Run `python scripts/fetch_bear_attacks.py` to list Ontario rows not yet in the CSV. It only stages candidates -- not every Ontario bear-attack fatality was camping or in a provincial park (e.g. berry-picking, a private cottage island), so review each one against scope before adding it by hand.
- **Drownings, falling trees, and everything else** -- no curated list exists, so these are found via news search. `python scripts/fetch_google_search_candidates.py [start_year] [end_year]` loops Google's Custom Search API over each year and cause-of-death keyword set and writes candidate articles to `data/news_candidates.csv` for review. This needs a one-time setup (a free Google API key + Programmable Search Engine ID) -- see the setup steps in the script's docstring. It's a keyword net, not a verdict: every row needs a human to open the link and confirm scope before it goes into the main CSV. The [Lifesaving Society's annual Ontario Drowning Report](https://www.lifesavingsociety.com/) is also worth checking for trend-level drowning numbers, though it doesn't name individual incidents.
- Note on coverage: Google's index thins out for older, often-broken local news pages, so years further back in the 15-20 year window will likely surface less than recent ones. Gaps there are best filled via library newspaper archives (e.g. Canadian Newsstream/PressReader through a public library) or the FOI route below.
- **The authoritative source** -- Ontario Parks (Ministry of Natural Resources and Forestry) and the Office of the Chief Coroner for Ontario hold the real incident records, but don't publish them. A [Freedom of Information request](https://www.ontario.ca/page/freedom-information-and-protection-privacy) to MNRF for a multi-year list of Ontario Parks fatalities is the most reliable way to get a complete list (this is how past media investigations into park drownings have been built) -- but it's a manual request, not something a script can do.
- **Coroner's inquests** -- when an inquest is held (more likely for unusual deaths like wildlife attacks), the jury verdict and recommendations are published publicly by the Office of the Chief Coroner and are worth checking for cases already in the CSV.

## CSV columns

`Date, Location, ProvincialPark, Cause, Activity, Name, Age, Gender, Source, Notes`

`ProvincialPark` is blank for cases that only meet the "camping" half of the scope (e.g. camping outside a park). `Source` should cite where the row came from (news outlet, Wikipedia, inquest verdict, FOI response) so entries can be checked later.

## Updating the data

- `python scripts/fetch_bear_attacks.py` -- checks for new/missing Ontario bear-attack rows from Wikipedia.
- `python scripts/fetch_google_search_candidates.py [start_year] [end_year]` -- searches Google (via SerpApi) for drowning/bear-attack/falling-tree news per year, staging results in `data/news_candidates.csv` for review.
- `python scripts/classify_candidates.py` -- sends each row of `news_candidates.csv` to a cheap OpenAI model (gpt-4o-mini) to pre-label it Relevant/Not relevant, writing `data/news_candidates_labeled.csv` sorted so the Relevant rows are on top. This cuts down what you have to eyeball, but it's a pre-filter, not a verdict -- still open each Relevant row's actual link before adding it to the main CSV.
- `python scripts/extract_incidents.py` -- turns the Relevant rows into a list of distinct incidents in `data/extracted_incidents.csv`. Stage 1 asks the model to cluster article snippets, in batches, into events. Stage 2 (`scripts/incident_merge.py`) deduplicates that output deterministically.
- `python scripts/test_incident_merge.py` -- unit tests for the dedup rules. Standard library only, no API key, runs in well under a second. Run it after changing any threshold.

All four scripts only stage candidates -- confirming scope and adding rows to `Ontario_camping_fatalities.csv` is a manual step.

## How deduplication decides

The same death reaches us several times over: different outlets cover it, the search tool tags the articles with inconsistent years, and batching means stage 1 describes one event once per batch. Matching on identical URLs alone never caught any of that, so the rule is the combined evidence of source URLs *and* the incident description.

Two incidents merge when the cause matches and any one of these holds:

| basis | meaning |
| --- | --- |
| `shared-url` | they cite the same article, after stripping tracking parameters |
| `name` | their victim names overlap |
| `location+date` | same place, compatible dates |
| `location+text` | same place, descriptions that say the same thing |
| `location+age` | same place, same victim age |
| `date+unverified-location` | the place name appears nowhere in its own sources, so it cannot be evidence of a *different* event; the dates agree to the day and both come from a source URL |
| `adjudicated` | the optional LLM tie-breaker judged a near-miss pair to be one event |

Any of those is vetoed by a hard contradiction: two different named victims, two different ages, or two dates that are each corroborated by a date in a source URL. That last veto is what keeps two separate drownings at one popular park apart.

Dates found in a source URL (`/2005/09/08/`, or a trailing `2022-05-21` in the slug) are treated as trustworthy. The model's own `date` field is not, because it tends to echo the search tool's Year tag. Where the two disagree, the URL wins and the row is flagged.

### Reading the output columns

`park_scope` is `provincial-park`, `national-park`, `other` or `unknown`, derived from the claimed location alone. It does not verify the place is real, so read it next to `location-unconfirmed`: the invented "Lake Erie Provincial Park" scores as a park and is not one.

`merged_from` and `merge_basis` say how many raw rows folded into a row and why. `date_source` is `url-path` or `model`. `model_dates` keeps the original guesses so a re-run stays faithful. `flags` is the review queue:

- `location-unconfirmed` -- the park name appears in none of the sources, so the model may have invented it. Observed repeatedly, including a bear attack on a northwestern Ontario island labelled as Algonquin.
- `date-uncertain` -- the merged rows disagreed on the year and no source URL settles it.
- `date-corrected-from-source` -- the date was overridden by a source URL.
- `single-source` / `unresolvable-sources` -- one article, or nothing but opaque redirect links.

The run also prints pairs that share a cause and a place but were deliberately left apart. Those are the judgement calls the rules refused to make, and they are worth a human's time first.

`--remerge` redoes stage 2 alone against the existing `extracted_incidents.csv`, with no API key and no cost, so the thresholds in `incident_merge.py` can be tuned and re-checked. It is idempotent: a second pass collapses nothing further.

### What it still cannot do

- It cannot undo a bad stage-1 merge. If the model fused four unrelated deaths into one row, stage 2 sees one row. The `location-unconfirmed` flag is usually the tell.
- One row means one incident, so an article reporting "two men drowned" is counted once.
- A distinct death that no source names precisely can still be merged into a similar one at the same place. Where the sources are vague, the rules favour merging, because an over-merge shows up as a suspiciously rich source list while a false split hides.

None of the counts here are verified. Treat `extracted_incidents.csv` as a review queue, not a result.

### Setup for the Google search script

Uses [SerpApi](https://serpapi.com/) (proxies real Google results through one API key) instead of Google's own Custom Search API -- that path got stuck in an unresolved Google Cloud account issue (project/billing/quota all checked out fine, but calls were still rejected) and wasn't worth more time chasing.

1. Sign up at https://serpapi.com/users/sign_up (free tier: 100 searches/month).
2. Copy your key from https://serpapi.com/manage-api-key.
3. Copy `.env.example` to `.env` in this folder and set `SERPAPI_KEY`. `.env` is gitignored, so the key never gets committed.
4. Run with the project's venv, which already has `requests` and `python-dotenv` installed: from the repo root, `.venv\Scripts\Activate.ps1`, then `cd camping-safety` and `python scripts/fetch_google_search_candidates.py 2011 2026`.

### Setup for the classifier script

1. Get an API key at https://platform.openai.com/api-keys.
2. Add it to `.env` as `OPENAI_API_KEY`.
3. Run `python scripts/classify_candidates.py` after `fetch_google_search_candidates.py` has produced `data/news_candidates.csv`. Cost is a few cents for a few hundred rows on gpt-4o-mini.
