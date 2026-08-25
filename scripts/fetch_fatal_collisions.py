"""Pull new fatal collisions from YRP's public Road Safety feed and geocode street names.

Data source: https://services8.arcgis.com/lYI034SQcOoxRCR7/arcgis/rest/services/Road_Safety_View/FeatureServer/0
(the live feed behind York Regional Police's public Road Safety Map / Community Safety Portal)

This script only pulls what YRP publishes: date, time, municipality, district,
cyclist/pedestrian involvement, and location (reverse-geocoded to a street name via
OpenStreetMap Nominatim when YRP doesn't supply one). Fields the CSV also tracks
(Age, Gender, Motorcycle, Mobility Scooter, Single Vehicle, Night) aren't published
by YRP and must be researched from news coverage per collision -- this script prints
a staging list of new occurrences to research, it does not append to the CSV itself.

Usage: python scripts/fetch_fatal_collisions.py
"""
import csv
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

FEATURE_SERVER = (
    "https://services8.arcgis.com/lYI034SQcOoxRCR7/arcgis/rest/services/"
    "Road_Safety_View/FeatureServer/0/query"
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "yrp-fatal-collisions-research/1.0"
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "YRP Data vehicle accidents 2023 - now.csv"


def fetch_fatal_features():
    params = {
        "where": "case_type LIKE '%Fatal%'",
        "outFields": "*",
        "returnGeometry": "true",
        "f": "json",
        "resultRecordCount": 2000,
    }
    url = FEATURE_SERVER + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("features", [])


def base_id(unique_identifier):
    """'2025_299136_1' -> '2025_299136'"""
    parts = unique_identifier.split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else unique_identifier


def webmercator_to_lonlat(x, y):
    lon = x / 20037508.34 * 180
    lat = 180 / math.pi * (2 * math.atan(math.exp(y / 20037508.34 * math.pi)) - math.pi / 2)
    return lon, lat


def reverse_geocode(lon, lat):
    params = {"lat": lat, "lon": lon, "format": "json", "zoom": 17}
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    return data.get("address", {}).get("road")


def load_existing_ids():
    ids = set()
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("ID"):
                ids.add(row["ID"].strip())
    return ids


def main():
    existing = load_existing_ids()
    features = fetch_fatal_features()

    occurrences = {}
    for feat in features:
        attrs = feat["attributes"]
        bid = base_id(attrs["UniqueIdentifier"])
        if bid in existing:
            continue
        occ = occurrences.setdefault(bid, {
            "occ_date": attrs.get("occ_date"),
            "time_est": attrs.get("time_est"),
            "municipality": attrs.get("municipality"),
            "district": attrs.get("district"),
            "IntersectionName": attrs.get("IntersectionName"),
            "InvolveCyclist": "N",
            "InvolvePed": "N",
            "geometry": feat.get("geometry"),
        })
        if attrs.get("InvolveCyclist") == "Y":
            occ["InvolveCyclist"] = "Y"
        if attrs.get("InvolvePed") == "Y":
            occ["InvolvePed"] = "Y"

    print(f"{len(occurrences)} new fatal occurrence(s) not yet in the CSV:\n")

    for bid, occ in sorted(occurrences.items()):
        street = occ["IntersectionName"]
        if not street and occ["geometry"]:
            lon, lat = webmercator_to_lonlat(occ["geometry"]["x"], occ["geometry"]["y"])
            try:
                street = reverse_geocode(lon, lat)
            except Exception:
                street = None
            time.sleep(1)  # Nominatim usage policy: max 1 request/sec
        print(
            f"{bid} | {occ['occ_date']} {occ['time_est']} | {occ['municipality']} "
            f"(district {occ['district']}) | street={street} "
            f"| cyclist={occ['InvolveCyclist']} ped={occ['InvolvePed']}"
        )


if __name__ == "__main__":
    main()
