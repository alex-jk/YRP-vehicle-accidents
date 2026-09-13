"""Deduplication for extracted fatality incidents.

The LLM clustering pass in extract_incidents.py splits a single real death into
several "incidents" whenever outlets word a story differently, or when the search
tool's bracketed Year tag disagrees between two articles about the same event.
This module collapses those back together using the combined evidence -- source
URLs *and* the incident description -- rather than exact URL identity alone.

Two incidents are merged when they share a cause AND any one of these holds:

  shared-url     they cite the same article (after normalization)
  name           their victim names overlap
  location+date  same place, and their dates are compatible
  location+text  same place, and their descriptions say the same thing

Any of those can be vetoed by hard contradictions -- two different named victims,
two different ages, or two dates that are each corroborated by a date in the
source URL path. URL-path dates are treated as trustworthy; the LLM's own `date`
field is not, because it routinely echoes the unreliable Year tag.

Pure standard library, so it can be unit-tested and re-run offline without any
API key.
"""
import re
from datetime import date as _date
from difflib import SequenceMatcher

# --- tunables -------------------------------------------------------------

SUMMARY_JACCARD = 0.50      # content-word overlap between two descriptions
SUMMARY_RATIO = 0.62        # difflib ratio fallback
LOCATION_JACCARD = 0.60     # token overlap between two place names
DATE_WINDOW_DAYS = 3        # how far apart two dates can be and still match

# Words that carry no identifying power in a place name.
PLACE_STOPWORDS = {
    "provincial", "park", "parks", "national", "conservation", "area",
    "campground", "camp", "ground", "ontario", "canada", "the", "of", "near", "at", "",
}
# Generic geography. Kept for matching two place names against each other, but
# ignored when asking "is this place actually named anywhere in the sources?",
# where matching on "lake" alone would confirm nothing.
GENERIC_PLACE_WORDS = {
    "lake", "beach", "bay", "river", "falls", "island", "county", "township",
    "north", "south", "east", "west", "upper", "lower", "point", "creek", "peninsula",
}
NAME_STOPWORDS = {
    "dr", "mr", "mrs", "ms", "jr", "sr", "the", "and", "of", "a", "an", "",
}
SUMMARY_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "from", "with", "while",
    "after", "was", "were", "is", "are", "been", "be", "had", "has", "have", "his",
    "her", "their", "he", "she", "they", "it", "that", "this", "and", "or", "but",
    "who", "which", "when", "during", "into", "over", "near", "by", "as", "one",
    "two", "incident", "occurred", "tragic", "reported", "following", "appears",
    "apparently", "ontario", "canada", "",
}

# Links we cannot resolve (opaque Google redirect blobs) must never act as a
# merge signal: every one of them lives on the same host and path.
OPAQUE_URL_MARKERS = ("google.com/goto", "google.com/url")
TRACKING_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "share")


# --- normalization --------------------------------------------------------

def is_opaque_url(url):
    """True for redirect wrappers whose real destination we cannot read."""
    return any(marker in (url or "").lower() for marker in OPAQUE_URL_MARKERS)


def normalize_url(url):
    """Reduce a URL to a comparable key: no scheme, no www., no tracking params,
    no fragment, no trailing slash. Opaque redirects are returned untouched so
    two different blobs never collide."""
    u = (url or "").strip()
    if not u:
        return ""
    if is_opaque_url(u):
        return u
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = u.split("#", 1)[0]
    if "?" in u:
        base, _, query = u.partition("?")
        kept = [
            p for p in query.split("&")
            if p and not any(p.lower().startswith(t) for t in TRACKING_PARAM_PREFIXES)
        ]
        u = base + ("?" + "&".join(kept) if kept else "")
    u = re.sub(r"^www\.", "", u, flags=re.I)
    return u.rstrip("/").lower()


def url_path_date(url):
    """Pull a publication date out of a URL path, e.g. /2005/09/08/ -> 2005-09-08.

    Returns (iso_string, precision) with precision in {"day", "month"}, or None.
    These are far more reliable than the model's own date field, which tends to
    echo the search tool's bogus Year tag."""
    if not url or is_opaque_url(url):
        return None
    m = re.search(r"/(19\d\d|20\d\d)/(\d{1,2})/(\d{1,2})(?:/|\b)", url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                return _date(y, mo, d).isoformat(), "day"
            except ValueError:
                return f"{y:04d}-{mo:02d}", "month"
    m = re.search(r"\b(19\d\d|20\d\d)-(\d{2})-(\d{2})\b", url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return _date(y, mo, d).isoformat(), "day"
        except ValueError:
            pass
    m = re.search(r"/(19\d\d|20\d\d)/(\d{1,2})(?:/|\b)", url)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}", "month"
    return None


def parse_date(value):
    """Parse YYYY, YYYY-MM or YYYY-MM-DD into (iso, precision)."""
    v = (value or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", v)
    if m:
        return v, "day"
    m = re.match(r"^(\d{4})-(\d{2})$", v)
    if m:
        return v, "month"
    m = re.match(r"^(\d{4})$", v)
    if m:
        return v, "year"
    return None


def _date_parts(iso):
    bits = [int(b) for b in iso.split("-")]
    while len(bits) < 3:
        bits.append(0)
    return bits


def dates_compatible(a, b):
    """Could these two (iso, precision) dates describe the same event?

    Unknown dates are compatible with anything. Comparison happens at the
    coarsest precision the pair shares. Same month-and-day in different years is
    treated as compatible because that is the exact fingerprint of the Year-tag
    artifact this pipeline keeps producing."""
    if not a or not b:
        return True
    (ia, pa), (ib, pb) = a, b
    ya, ma, da = _date_parts(ia)
    yb, mb, db = _date_parts(ib)
    coarse = "year"
    for level in ("day", "month", "year"):
        if pa == level or pb == level:
            coarse = level
            break
    if coarse == "day" and pa == "day" and pb == "day":
        if ya == yb:
            return abs((_date(ya, ma, da) - _date(yb, mb, db)).days) <= DATE_WINDOW_DAYS
        return (ma, da) == (mb, db)
    if coarse in ("day", "month") and pa in ("day", "month") and pb in ("day", "month"):
        if ya == yb:
            return ma == mb
        return ma == mb
    return ya == yb


# --- token helpers --------------------------------------------------------

def _tokens(text, stopwords):
    raw = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {t for t in raw if t and t not in stopwords and len(t) > 1}


def location_tokens(location):
    return _tokens(location, PLACE_STOPWORDS)


def distinctive_location_tokens(location):
    """Place tokens that actually identify somewhere, with generic geography
    ('lake', 'beach', 'north') removed."""
    return location_tokens(location) - GENERIC_PLACE_WORDS


def name_tokens(names):
    return {t for t in _tokens(names, NAME_STOPWORDS) if len(t) >= 4 and not t.isdigit()}


def summary_tokens(summary):
    return _tokens(summary, SUMMARY_STOPWORDS)


def ages(age_gender):
    return {int(n) for n in re.findall(r"\b(\d{1,3})\b", age_gender or "") if 0 < int(n) < 120}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def locations_match(loc_a, loc_b):
    ta, tb = location_tokens(loc_a), location_tokens(loc_b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    # A subset match only counts when the shared part is actually identifying,
    # so "Silver Lake" and "Silver Falls" stay apart.
    if (ta <= tb or tb <= ta) and (distinctive_location_tokens(loc_a) & distinctive_location_tokens(loc_b)):
        return True
    return _jaccard(ta, tb) >= LOCATION_JACCARD


def summaries_match(sum_a, sum_b):
    ta, tb = summary_tokens(sum_a), summary_tokens(sum_b)
    if not ta or not tb:
        return False
    if _jaccard(ta, tb) >= SUMMARY_JACCARD:
        return True
    return SequenceMatcher(None, (sum_a or "").lower(), (sum_b or "").lower()).ratio() >= SUMMARY_RATIO


# --- per-incident preparation --------------------------------------------

def prepare(incident, evidence_lookup=None):
    """Attach derived comparison keys to a raw incident dict (mutates and returns).

    evidence_lookup maps a normalized URL to the article text behind it, letting
    the place name be checked against titles and snippets as well as URL slugs."""
    urls = incident.get("_urls")
    if urls is None:
        urls = [u.strip() for u in (incident.get("source_urls") or "").split("|") if u.strip()]
        incident["_urls"] = urls

    incident["_norm_urls"] = {normalize_url(u) for u in urls if u and not is_opaque_url(u)}

    # Best date recoverable from the source URLs themselves.
    best = None
    for u in urls:
        found = url_path_date(u)
        if found and (best is None or (best[1] == "month" and found[1] == "day")):
            best = found
    incident["_trusted_date"] = best
    incident["_model_date"] = parse_date(incident.get("date"))
    if incident.get("model_dates") and not incident.get("_model_dates"):
        incident["_model_dates"] = [
            d.strip() for d in str(incident["model_dates"]).split(";") if d.strip()
        ]
    incident["_effective_date"] = best or incident["_model_date"]

    incident["_cause"] = (incident.get("cause") or "").strip().lower()
    incident["_names"] = name_tokens(incident.get("names"))
    incident["_ages"] = ages(incident.get("age_gender"))

    evidence = ""
    if evidence_lookup:
        evidence = " ".join(evidence_lookup.get(normalize_url(u), "") for u in urls)
    incident["_location_confirmed"] = location_confirmed(incident, evidence)
    return incident


# --- pairwise decision ----------------------------------------------------

def contradiction(a, b):
    """A hard reason two incidents cannot be the same death. Returns a string or None."""
    if a["_names"] and b["_names"] and not (a["_names"] & b["_names"]):
        return "different named victims"
    if a["_ages"] and b["_ages"] and not (a["_ages"] & b["_ages"]):
        return "different ages"
    if a["_trusted_date"] and b["_trusted_date"] and not dates_compatible(a["_trusted_date"], b["_trusted_date"]):
        return "source-confirmed dates disagree"
    return None


def merge_reason(a, b):
    """Why these two incidents are the same death, or None if they are not.

    Cause must always agree. Beyond that any single positive signal is enough,
    but a hard contradiction vetoes all of them."""
    if a["_cause"] != b["_cause"]:
        return None
    if contradiction(a, b):
        return None

    if a["_norm_urls"] & b["_norm_urls"]:
        return "shared-url"
    if a["_names"] & b["_names"]:
        return "name"

    same_place = locations_match(a.get("location"), b.get("location"))
    if same_place and dates_compatible(a["_effective_date"], b["_effective_date"]):
        return "location+date"
    if same_place and summaries_match(a.get("summary"), b.get("summary")):
        return "location+text"
    if same_place and (a["_ages"] & b["_ages"]):
        return "location+age"

    # A place name that appears nowhere in its own sources is the model's
    # invention, and an invention must not be allowed to hold two records
    # apart. Where one is unverifiable, fall back to the dates -- but only
    # when both are pinned to the same day by a source URL, which is the one
    # date signal that does not come from the model.
    if not (a.get("_location_confirmed", True) and b.get("_location_confirmed", True)):
        da, db = a["_trusted_date"], b["_trusted_date"]
        if da and db and da[1] == "day" and da == db:
            return "date+unverified-location"
    return None


# --- union-find -----------------------------------------------------------

class _DSU:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return False
        self.parent[max(ri, rj)] = min(ri, rj)
        return True


def _int_or_one(value):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _pick_date(cluster):
    """Most trustworthy, most specific date in the cluster."""
    order = {"day": 0, "month": 1, "year": 2}
    trusted = [c["_trusted_date"] for c in cluster if c["_trusted_date"]]
    if trusted:
        best = sorted(trusted, key=lambda d: order[d[1]])[0]
        return best[0], "url-path"
    modelled = [c["_model_date"] for c in cluster if c["_model_date"]]
    if modelled:
        best = sorted(modelled, key=lambda d: order[d[1]])[0]
        return best[0], "model"
    return "", ""


def _join_unique(values, sep="; "):
    out = []
    for v in values:
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)
    return sep.join(out)


def combine(cluster):
    """Fold a cluster of duplicate incidents into one record."""
    date_value, date_source = _pick_date(cluster)

    # A corroborated place name wins over one the model supplied from nowhere;
    # after that, the most specific name, then the longer string.
    location = sorted(
        cluster,
        key=lambda c: (
            not c.get("_location_confirmed", True),
            -len(distinctive_location_tokens(c.get("location"))),
            -len(c.get("location") or ""),
        ),
    )[0].get("location", "")

    summary = sorted(cluster, key=lambda c: -len(c.get("summary") or ""))[0].get("summary", "")

    urls, seen = [], set()
    for c in cluster:
        for u in c["_urls"]:
            key = normalize_url(u)
            if key and key not in seen:
                seen.add(key)
                urls.append(u)

    return {
        "date": date_value,
        "date_source": date_source,
        "cause": _join_unique([c.get("cause") for c in cluster], sep=" / "),
        "location": location,
        "names": _join_unique([c.get("names") for c in cluster]),
        "age_gender": _join_unique([c.get("age_gender") for c in cluster]),
        "summary": summary,
        "_urls": urls,
        "_cluster": cluster,
        "_model_dates": sorted({
            d for c in cluster
            for d in (c.get("_model_dates") or ([c["_model_date"][0]] if c["_model_date"] else []))
        }),
    }


def merge_incidents(incidents, extra_pairs=(), evidence_lookup=None):
    """Collapse duplicate incidents. Returns (merged_records, stats).

    extra_pairs are index pairs judged to be the same event by something
    outside these rules, such as the optional LLM adjudication pass. They are
    unioned in as-is, but still recorded so the basis stays visible."""
    items = [prepare(dict(inc), evidence_lookup) for inc in incidents]
    dsu = _DSU(len(items))
    reasons = {}

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            why = merge_reason(items[i], items[j])
            if why:
                dsu.union(i, j)
                reasons.setdefault(dsu.find(i), set()).add(why)

    for i, j in extra_pairs:
        if 0 <= i < len(items) and 0 <= j < len(items):
            dsu.union(i, j)
            reasons.setdefault(dsu.find(i), set()).add("adjudicated")

    # Re-key reasons onto final roots (roots can change as unions chain).
    clusters = {}
    for idx, item in enumerate(items):
        clusters.setdefault(dsu.find(idx), []).append(item)
    final_reasons = {}
    for root_guess, why_set in reasons.items():
        final_reasons.setdefault(dsu.find(root_guess), set()).update(why_set)

    merged = []
    for root, cluster in clusters.items():
        record = combine(cluster)
        record["merged_from"] = sum(_int_or_one(c.get("merged_from")) for c in cluster)
        prior = {b.strip() for c in cluster for b in (c.get("merge_basis") or "").split(",") if b.strip()}
        record["merge_basis"] = ", ".join(sorted(final_reasons.get(root, set()) | prior))
        merged.append(record)

    merged.sort(key=lambda r: (r["date"] or "9999", r["cause"], r["location"]))
    stats = {
        "input": len(items),
        "output": len(merged),
        "collapsed": len(items) - len(merged),
    }
    return merged, stats


# --- review flags ---------------------------------------------------------

def near_miss_pairs(merged, evidence_lookup=None):
    """Pairs left separate that still share a cause and a place. These are the
    judgement calls the rules deliberately refused to make, and the rows most
    worth a human's attention."""
    prepared = [prepare(dict(m), evidence_lookup) for m in merged]
    pairs = []
    for i in range(len(prepared)):
        for j in range(i + 1, len(prepared)):
            a, b = prepared[i], prepared[j]
            if a["_cause"] == b["_cause"] and locations_match(a.get("location"), b.get("location")):
                pairs.append((i, j, contradiction(a, b) or "no contradiction found"))
    return pairs


def location_confirmed(record, evidence=""):
    """Does the place name actually appear in the sources, or did the model
    supply it from nowhere? Generic words like 'lake' cannot confirm anything."""
    distinctive = distinctive_location_tokens(record.get("location"))
    if not distinctive:
        return True
    haystack = (" ".join(record.get("_urls", [])) + " " + (evidence or "")).lower()
    return any(tok in haystack for tok in distinctive)


def flags_for(record, evidence=""):
    out = []
    if not location_confirmed(record, evidence):
        out.append("location-unconfirmed")
    if record.get("date_source") == "url-path":
        years = {d[:4] for d in record.get("_model_dates", [])}
        if years and record["date"][:4] not in years:
            out.append("date-corrected-from-source")
    if record.get("date_source") == "model":
        years = {d[:4] for d in record.get("_model_dates", [])}
        if len(years) > 1:
            out.append("date-uncertain")
    if len(record.get("_urls", [])) == 1:
        out.append("single-source")
    if record.get("_urls") and all(is_opaque_url(u) for u in record["_urls"]):
        out.append("unresolvable-sources")
    return out


def park_scope(record):
    """Whether the record's own location names an Ontario provincial park.

    This reads the claimed location only. It does not verify that the place is
    real or that the model got it right, so read it alongside the
    location-unconfirmed flag: "Lake Erie Provincial Park" scores as a park
    here and is not one."""
    loc = (record.get("location") or "").lower()
    if "provincial park" in loc:
        return "provincial-park"
    if "national park" in loc:
        return "national-park"
    if not loc.strip():
        return "unknown"
    return "other"


def is_likely_seed_duplicate(incident, seed_rows):
    """Flag incidents that look like a row already in the hand-checked seed CSV.

    Requires a name match, or a place match backed by compatible dates -- a bare
    place-name collision across three decades is not evidence of anything."""
    prepared = prepare(dict(incident))
    for seed in seed_rows:
        if prepared["_cause"] and prepared["_cause"] not in (seed.get("Cause") or "").lower():
            continue
        seed_names = name_tokens(seed.get("Name"))
        if prepared["_names"] & seed_names:
            return f"matches seed {seed.get('Date', '')} {seed.get('Name', '')} (name)"
        if locations_match(incident.get("location"), seed.get("Location")):
            if dates_compatible(prepared["_effective_date"], parse_date(seed.get("Date"))):
                return f"matches seed {seed.get('Date', '')} {seed.get('Name', '')} (place+date)"
    return ""
