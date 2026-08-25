Analysis of data from York Regional Police Road Safety Map on vehicle accidents. Dataset is from 2023 and beyond. Statistics on fatal accidents by gender, age, pedestrian and cyclist accidents, most dangerous roads / intersections.

## Updating the data

`data/YRP Data vehicle accidents 2023 - now.csv` combines two sources:

- Date, time, municipality, street(s), and cyclist/pedestrian involvement come from YRP's public "Year to Date Road Safety Data" feed: `https://services8.arcgis.com/lYI034SQcOoxRCR7/arcgis/rest/services/Road_Safety_View/FeatureServer/0`. Run `python scripts/fetch_fatal_collisions.py` to list fatal collisions (`case_type LIKE '%Fatal%'`) not yet in the CSV, with street names filled in via reverse geocoding (OpenStreetMap Nominatim) where YRP's own `IntersectionName` field is blank.
- Age, Gender, Motorcycle, Mobility Scooter, and Single Vehicle aren't published by YRP anywhere — they're researched from news coverage of each collision (CBC, CP24, Newmarket Today, YRP media releases, etc.) and added by hand. Not every fatal collision gets enough news coverage to fill these in; rows where they couldn't be confirmed use `NA`.

**Scope:** this dataset covers standard road vehicle collisions only (car/truck/motorcycle vs. vehicle/pedestrian/cyclist on a road). YRP's "Fatal" feed also includes incidents that don't fit that pattern — a vehicle crashing into a building, a pedestrian struck by a train, snow plow/works-vehicle incidents, parking lot crashes, etc. When researching new fatal collisions, flag anything like that for a decision rather than adding it automatically.
