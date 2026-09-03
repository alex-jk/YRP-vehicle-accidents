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

All three scripts only stage candidates -- confirming scope and adding rows to `Ontario_camping_fatalities.csv` is a manual step.

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
