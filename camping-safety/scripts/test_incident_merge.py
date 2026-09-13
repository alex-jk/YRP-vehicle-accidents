"""Unit tests for incident_merge. Standard library only: run with

    python scripts/test_incident_merge.py

Cases are drawn from real defects observed in data/extracted_incidents.csv.
"""
import unittest

import incident_merge as im


def inc(**kw):
    base = {"date": "", "cause": "Drowning", "location": "", "names": "",
            "age_gender": "", "summary": "", "source_urls": ""}
    base.update(kw)
    return base


class TestUrls(unittest.TestCase):
    def test_normalize_strips_noise(self):
        self.assertEqual(
            im.normalize_url("https://WWW.Cbc.ca/news/story-1.234/?utm_source=x#top"),
            "cbc.ca/news/story-1.234",
        )

    def test_two_outlets_are_not_the_same_url(self):
        self.assertNotEqual(
            im.normalize_url("https://www.cp24.com/news/canada/2025/07/25/child-drowns-at-sandbanks/"),
            im.normalize_url("https://toronto.citynews.ca/2025/07/25/child-drowning-sandbanks/"),
        )

    def test_opaque_redirects_stay_distinct(self):
        a = "https://www.google.com/goto?url=CAESywEB6zswFc"
        b = "https://www.google.com/goto?url=CAEStwEB6zswFY"
        self.assertTrue(im.is_opaque_url(a))
        self.assertNotEqual(im.normalize_url(a), im.normalize_url(b))

    def test_opaque_url_never_merges(self):
        url = "https://www.google.com/goto?url=CAESywEB6zswFc"
        a = im.prepare(inc(location="Pinehurst Lake", cause="Falling Tree", source_urls=url))
        self.assertEqual(a["_norm_urls"], set())


class TestDates(unittest.TestCase):
    def test_date_from_url_path(self):
        self.assertEqual(
            im.url_path_date("https://www.orlandosentinel.com/2005/09/08/woman-dead-man-injured/"),
            ("2005-09-08", "day"),
        )

    def test_month_only_path(self):
        self.assertEqual(im.url_path_date("https://site.com/2019/07/story"), ("2019-07", "month"))

    def test_date_embedded_in_the_slug(self):
        self.assertEqual(
            im.url_path_date("https://www.reuters.com/business/environment/powerful-storm-rips-through-ontario-killing-least-two-2022-05-21/"),
            ("2022-05-21", "day"),
        )

    def test_id_like_hex_is_not_a_date(self):
        self.assertIsNone(im.url_path_date(
            "https://www.thestar.com/news/canada/bear-attack/article_2b666ac6-29b7-5748-8921-906211977074.html"))

    def test_no_date_in_path(self):
        self.assertIsNone(im.url_path_date("https://ottawacitizen.com/news/man-drowns-silver-lake"))

    def test_same_month_day_different_year_is_the_year_tag_artifact(self):
        self.assertTrue(im.dates_compatible(("2018-09-08", "day"), ("2005-09-08", "day")))

    def test_far_apart_same_year_is_incompatible(self):
        self.assertFalse(im.dates_compatible(("2019-07-07", "day"), ("2019-09-03", "day")))

    def test_unknown_date_matches_anything(self):
        self.assertTrue(im.dates_compatible(None, ("2020-06-27", "day")))


class TestLocations(unittest.TestCase):
    def test_same_park(self):
        self.assertTrue(im.locations_match("Sandbanks Provincial Park", "Sandbanks Provincial Park"))

    def test_silver_lake_is_not_silver_falls(self):
        self.assertFalse(im.locations_match("Silver Lake Provincial Park", "Silver Falls Provincial Park"))

    def test_subset_place_names_match(self):
        self.assertTrue(im.locations_match("Grotto, Bruce Peninsula", "Bruce Peninsula National Park"))

    def test_generic_words_alone_do_not_match(self):
        self.assertFalse(im.locations_match("Lake Erie Provincial Park", "Rice Lake"))


class TestSummaries(unittest.TestCase):
    def test_same_event_worded_differently(self):
        self.assertTrue(im.summaries_match(
            "A 26-year-old man drowned after jumping from a canoe at Silver Lake Provincial Park.",
            "A man drowned at Silver Lake Provincial Park after swimming from a canoe.",
        ))

    def test_different_events_at_one_park(self):
        self.assertFalse(im.summaries_match(
            "Muhammad Azmat drowned at North Beach Provincial Park while swimming with friends.",
            "A 26-year-old Michigan man was killed when a tree fell on his tent during a storm.",
        ))


class TestMergeDecision(unittest.TestCase):
    def test_same_event_different_outlets_merges(self):
        """The defect that motivated this change: identical event, two domains."""
        a = im.prepare(inc(date="2018-07-22", location="Sandbanks Provincial Park",
                           summary="A three-year-old child drowned at Sandbanks Provincial Park.",
                           source_urls="https://www.cp24.com/news/canada/2025/07/25/child-drowns-at-sandbanks/"))
        b = im.prepare(inc(date="2024-07-24", location="Sandbanks Provincial Park",
                           summary="A child drowned at Sandbanks Provincial Park while playing with family.",
                           source_urls="https://toronto.citynews.ca/2025/07/25/child-drowning-sandbanks/"))
        self.assertEqual(im.merge_reason(a, b), "location+date")

    def test_description_match_without_any_date_agreement(self):
        a = im.prepare(inc(date="2024-08-16", location="Silver Lake Provincial Park", age_gender="26",
                           summary="A 26-year-old man drowned after jumping from a canoe at Silver Lake Provincial Park.",
                           source_urls="https://www.cbc.ca/news/canada/ottawa/swimmer-drowns-1.7611045"))
        b = im.prepare(inc(date="2026-07-27", location="Silver Lake Provincial Park", age_gender="26",
                           summary="A man drowned at Silver Lake Provincial Park after swimming from a canoe.",
                           source_urls="https://www.ctvnews.ca/ottawa/article/swimmer-drowns-in-silver-lake/"))
        self.assertIn(im.merge_reason(a, b), ("location+date", "location+text"))

    def test_different_causes_never_merge(self):
        a = im.prepare(inc(cause="Drowning", location="Algonquin Provincial Park"))
        b = im.prepare(inc(cause="Falling Tree", location="Algonquin Provincial Park"))
        self.assertIsNone(im.merge_reason(a, b))

    def test_different_named_victims_block_merge(self):
        a = im.prepare(inc(location="North Beach Provincial Park", names="Muhammad Azmat"))
        b = im.prepare(inc(location="North Beach Provincial Park", names="Andre Bourgeois"))
        self.assertIsNone(im.merge_reason(a, b))

    def test_different_ages_block_merge(self):
        a = im.prepare(inc(location="Sandbanks Provincial Park", age_gender="3"))
        b = im.prepare(inc(location="Sandbanks Provincial Park", age_gender="45"))
        self.assertIsNone(im.merge_reason(a, b))

    def test_source_confirmed_dates_block_merge(self):
        """Two drownings at one park in different years must stay apart when the
        source URLs independently confirm both dates."""
        a = im.prepare(inc(location="Chutes Provincial Park",
                           summary="A man drowned at Chutes Provincial Park.",
                           source_urls="https://news.example.com/2018/07/04/drowning-at-chutes/"))
        b = im.prepare(inc(location="Chutes Provincial Park",
                           summary="A man drowned at Chutes Provincial Park.",
                           source_urls="https://other.example.com/2022/06/27/drowning-at-chutes/"))
        self.assertIsNone(im.merge_reason(a, b))

    def test_shared_url_still_merges(self):
        url = "https://www.cbc.ca/news/canada/ottawa/story-1.6141902"
        a = im.prepare(inc(location="Fitzroy Provincial Park", source_urls=url))
        b = im.prepare(inc(location="Ottawa River", source_urls=url + "?utm_source=twitter"))
        self.assertEqual(im.merge_reason(a, b), "shared-url")

    def test_matching_age_at_one_place_merges(self):
        """A body reported missing and recovered days later, same park, same age."""
        a = im.prepare(inc(location="Grotto, Bruce Peninsula", age_gender="24",
                           summary="The body of a 24-year-old man was recovered by OPP divers who were searching for him at the Grotto.",
                           source_urls="https://brucepeninsulapress.com/2019/07/10/drowned-swimmers-body-found/"))
        b = im.prepare(inc(date="2019-07-14", location="Bruce Peninsula National Park", age_gender="24",
                           summary="The body of a 24-year-old man was recovered from the Bruce Peninsula National Park after going missing the previous day.",
                           source_urls="https://lfpress.com/news/local-news/story"))
        self.assertFalse(im.summaries_match(a["summary"], b["summary"]))
        self.assertEqual(im.merge_reason(a, b), "location+age")

    def test_invented_place_cannot_hold_two_records_apart(self):
        """Both rows describe one bear attack; one carries a hallucinated park."""
        lookup = {}
        a = im.prepare(inc(cause="Bear Attack", location="Algonquin Provincial Park",
                           summary="OPP investigating a fatality as a possible bear attack.",
                           source_urls="https://www.cp24.com/news/2019/09/04/american-woman-bear-attack-remote-island/"),
                       lookup)
        b = im.prepare(inc(cause="Bear Attack", location="Rainy Lake", age_gender="62",
                           summary="A 62-year-old woman died on an island following an evident bear attack.",
                           source_urls="https://www.boreal.org/2019/09/04/282631/woman-62-dies-northwestern-ontario/"),
                       lookup)
        self.assertFalse(a["_location_confirmed"])
        self.assertEqual(im.merge_reason(a, b), "date+unverified-location")

    def test_confirmed_places_are_never_overridden_by_the_date_fallback(self):
        """Two real, differently-named parks on one day stay separate."""
        a = im.prepare(inc(location="Rice Lake",
                           source_urls="https://kawarthanow.com/2023/07/06/oshawa-man-drowned-in-rice-lake/"))
        b = im.prepare(inc(location="Silver Falls Provincial Park",
                           source_urls="https://www.tbnewswatch.com/2023/07/06/man-dies-at-silver-falls-provincial-park/"))
        self.assertTrue(a["_location_confirmed"])
        self.assertTrue(b["_location_confirmed"])
        self.assertIsNone(im.merge_reason(a, b))

    def test_evidence_lookup_can_confirm_a_place_absent_from_the_url(self):
        url = "https://www.reuters.com/business/environment/powerful-storm-rips-through-ontario/"
        lookup = {im.normalize_url(url): "A tree fell on a trailer at Pinehurst Lake Conservation Area."}
        rec = im.prepare(inc(cause="Falling Tree", location="Pinehurst Lake", source_urls=url), lookup)
        self.assertTrue(rec["_location_confirmed"])
        self.assertFalse(im.prepare(inc(cause="Falling Tree", location="Pinehurst Lake",
                                        source_urls=url))["_location_confirmed"])

    def test_name_overlap_merges_across_locations(self):
        a = im.prepare(inc(cause="Bear Attack", location="Missinaibi Lake Provincial Park",
                           names="Dr. Jacqueline Perry"))
        b = im.prepare(inc(cause="Bear Attack", location="Missinaibi Lake", names="Jacqueline Perry"))
        self.assertEqual(im.merge_reason(a, b), "name")


class TestClustering(unittest.TestCase):
    def test_transitive_merge_collapses_a_chain(self):
        rows = [
            inc(date="2018-07-01", location="Silver Lake Provincial Park",
                summary="A man drowned at Silver Lake Provincial Park.",
                source_urls="https://ottawacitizen.com/news/man-drowns-silver-lake"),
            inc(date="2025-07-21", location="Silver Lake Provincial Park",
                summary="A man drowned at Silver Lake Provincial Park during a recreational activity.",
                source_urls="https://www.rmoutlook.com/ontario-news/26-year-old-man-dead-silver-lake"),
            inc(date="2026-07-27", location="Silver Lake Provincial Park", age_gender="26",
                summary="A man drowned at Silver Lake Provincial Park after swimming from a canoe.",
                source_urls="https://www.ctvnews.ca/ottawa/article/swimmer-drowns-in-silver-lake/"),
        ]
        merged, stats = im.merge_incidents(rows)
        self.assertEqual(stats["output"], 1)
        self.assertEqual(merged[0]["merged_from"], 3)
        self.assertEqual(len(merged[0]["_urls"]), 3)

    def test_date_is_corrected_from_the_source_url(self):
        rows = [inc(date="2018-09-08", cause="Bear Attack", location="Missinaibi Lake Provincial Park",
                    names="Dr. Jacqueline Perry", age_gender="30",
                    summary="Killed in a bear attack at a remote campsite.",
                    source_urls="https://www.orlandosentinel.com/2005/09/08/woman-dead-man-injured/")]
        merged, _ = im.merge_incidents(rows)
        self.assertEqual(merged[0]["date"], "2005-09-08")
        self.assertEqual(merged[0]["date_source"], "url-path")
        self.assertIn("date-corrected-from-source", im.flags_for(merged[0]))


class TestIdempotence(unittest.TestCase):
    ROWS = [
        inc(date="2018-07-22", location="Sandbanks Provincial Park",
            summary="A three-year-old child drowned at Sandbanks Provincial Park.",
            source_urls="https://www.cp24.com/news/canada/2025/07/25/child-drowns-at-sandbanks/"),
        inc(date="2024-07-24", location="Sandbanks Provincial Park",
            summary="A child drowned at Sandbanks Provincial Park while playing with family.",
            source_urls="https://toronto.citynews.ca/2025/07/25/child-drowning-sandbanks/"),
        inc(date="2021-08-07", location="Fitzroy Provincial Park", age_gender="18",
            summary="An 18-year-old Ottawa woman drowned in the Ottawa River at Fitzroy Provincial Park.",
            source_urls="https://www.cbc.ca/news/canada/ottawa/body-swimmer-fitzroy-1.6141902"),
    ]

    def test_second_pass_changes_nothing(self):
        first, _ = im.merge_incidents(self.ROWS)
        second, stats = im.merge_incidents(first)
        self.assertEqual(stats["collapsed"], 0)
        self.assertEqual(len(second), len(first))

    def test_cluster_size_survives_a_second_pass(self):
        first, _ = im.merge_incidents(self.ROWS)
        sandbanks = [r for r in first if "Sandbanks" in r["location"]][0]
        self.assertEqual(sandbanks["merged_from"], 2)
        second, _ = im.merge_incidents(first)
        again = [r for r in second if "Sandbanks" in r["location"]][0]
        self.assertEqual(again["merged_from"], 2)
        self.assertIn("location+date", again["merge_basis"])

    def test_model_dates_round_trip_through_the_csv_column(self):
        first, _ = im.merge_incidents(self.ROWS)
        sandbanks = [r for r in first if "Sandbanks" in r["location"]][0]
        as_csv = dict(sandbanks)
        as_csv["model_dates"] = "; ".join(sandbanks["_model_dates"])
        del as_csv["_model_dates"]
        restored = im.prepare(as_csv)
        self.assertEqual(restored["_model_dates"], ["2018-07-22", "2024-07-24"])


class TestUncertainDates(unittest.TestCase):
    def test_cluster_of_disagreeing_model_dates_is_flagged(self):
        rows = [
            inc(date="2018-07-01", location="Silver Lake Provincial Park",
                summary="A man drowned at Silver Lake Provincial Park.",
                source_urls="https://ottawacitizen.com/news/man-drowns-silver-lake"),
            inc(date="2026-07-27", location="Silver Lake Provincial Park",
                summary="A man drowned at Silver Lake Provincial Park after swimming from a canoe.",
                source_urls="https://www.ctvnews.ca/ottawa/article/swimmer-drowns-in-silver-lake/"),
        ]
        merged, _ = im.merge_incidents(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["date_source"], "model")
        self.assertIn("date-uncertain", im.flags_for(merged[0]))

    def test_a_source_backed_date_is_not_called_uncertain(self):
        rows = [inc(date="2018-07-22", location="Sandbanks Provincial Park",
                    summary="A child drowned at Sandbanks Provincial Park.",
                    source_urls="https://www.cp24.com/news/canada/2025/07/25/child-drowns-at-sandbanks/")]
        merged, _ = im.merge_incidents(rows)
        self.assertNotIn("date-uncertain", im.flags_for(merged[0]))


class TestFlags(unittest.TestCase):
    def test_park_name_absent_from_sources_is_flagged(self):
        rec = {"location": "Algonquin Provincial Park",
               "_urls": ["https://www.sootoday.com/around-ontario/woman-fatally-mauled-by-bear-in-northwestern-ontario-1671459"]}
        self.assertFalse(im.location_confirmed(rec))

    def test_park_name_present_is_not_flagged(self):
        rec = {"location": "Silver Lake Provincial Park",
               "_urls": ["https://ottawacitizen.com/news/man-drowns-silver-lake"]}
        self.assertTrue(im.location_confirmed(rec))

    def test_generic_word_alone_does_not_confirm(self):
        rec = {"location": "Lake Erie Provincial Park",
               "_urls": ["https://lfpress.com/news/local-news/man-dies-after-diving-from-boat-in-lake-huron-opp"]}
        self.assertFalse(im.location_confirmed(rec))


class TestCombine(unittest.TestCase):
    def test_corroborated_place_name_wins(self):
        """An invented park must not outrank a place the sources actually name."""
        rows = [
            inc(cause="Bear Attack", location="Algonquin Provincial Park",
                summary="OPP investigating a fatality as a possible bear attack.",
                source_urls="https://www.cp24.com/news/2019/09/04/woman-bear-attack-remote-island/"),
            inc(cause="Bear Attack", location="Rainy Lake", age_gender="62",
                summary="A 62-year-old woman died on an island after an evident bear attack.",
                source_urls="https://www.boreal.org/2019/09/04/282631/woman-62-dies-rainy-lake-island/"),
        ]
        merged, stats = im.merge_incidents(rows)
        self.assertEqual(stats["output"], 1)
        self.assertEqual(merged[0]["location"], "Rainy Lake")


class TestParkScope(unittest.TestCase):
    def test_provincial_park(self):
        self.assertEqual(im.park_scope({"location": "Sandbanks Provincial Park"}), "provincial-park")

    def test_national_park_is_separated_out(self):
        self.assertEqual(im.park_scope({"location": "Bruce Peninsula National Park"}), "national-park")

    def test_campground_is_other(self):
        self.assertEqual(im.park_scope({"location": "Mallorytown"}), "other")

    def test_blank_location(self):
        self.assertEqual(im.park_scope({"location": ""}), "unknown")

    def test_scope_reads_the_claim_not_the_truth(self):
        """An invented park still scores as a park; the flag is what warns you."""
        rec = {"location": "Lake Erie Provincial Park",
               "_urls": ["https://lfpress.com/news/local-news/man-dies-diving-from-boat-lake-huron"]}
        self.assertEqual(im.park_scope(rec), "provincial-park")
        self.assertFalse(im.location_confirmed(rec))


class TestSeedDuplicate(unittest.TestCase):
    SEED = [
        {"Date": "1991-10-11", "Location": "Bates Island, Algonquin Provincial Park",
         "Cause": "Bear Attack", "Name": "Raymond Jakubauskas; Carola Frehe"},
        {"Date": "2005-09-06", "Location": "Missinaibi Lake Provincial Park",
         "Cause": "Bear Attack", "Name": "Jacqueline Perry"},
    ]

    def test_name_match_is_flagged(self):
        got = im.is_likely_seed_duplicate(
            inc(cause="Bear Attack", location="Missinaibi Lake Provincial Park",
                names="Dr. Jacqueline Perry", date="2005-09-08"), self.SEED)
        self.assertIn("Jacqueline Perry", got)

    def test_place_collision_decades_apart_is_not_flagged(self):
        """The old heuristic wrongly tied a 2019 bear attack to the 1991 Algonquin case."""
        got = im.is_likely_seed_duplicate(
            inc(cause="Bear Attack", location="Algonquin Provincial Park", date="2019-07-20",
                summary="OPP investigating a fatality as a possible bear attack."), self.SEED)
        self.assertEqual(got, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
