import json
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import Request, TextResponse

from open_ire.items import ArticleItem
from open_ire.spiders.osti import OstiSpider


@pytest.fixture
def spider() -> Generator[OstiSpider, None, None]:
    with patch.object(OstiSpider, "logger", new_callable=MagicMock):
        yield OstiSpider(terms="university of washington")


def _page_response(
    records: list[dict[str, Any]], term: str = "university of washington"
) -> TextResponse:
    url = "https://www.osti.gov/api/v1/records?q=test"
    request = Request(url, meta={"search_term": term, "page": 1})
    return TextResponse(
        url=url,
        body=json.dumps(records).encode("utf-8"),
        encoding="utf-8",
        request=request,
    )


# Shapes mirror a live OSTI /api/v1/records result: authors carry a bracketed
# affiliation and an optional ORCID suffix.
UW_BY_RESEARCH_ORG: dict[str, Any] = {
    "osti_id": "3021156",
    "title": "Signatures of fractional charges via anyon-trions",
    "description": "An abstract.",
    "doi": "https://doi.org/10.1234/example",
    "publication_date": "2024-03-15T00:00:00Z",
    "research_orgs": ["University of Washington, Seattle, WA (United States)"],
    "authors": ["Li, Weijie [University of Washington, Seattle, WA (United States)]"],
    "links": [
        {"rel": "citation", "href": "https://www.osti.gov/biblio/3021156"},
        {"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/3021156"},
    ],
}

# UW appears only in one author's affiliation.  OSTI's `author:` index does not
# cover affiliation text, so a `research_org:`-scoped query would miss this.
UW_BY_AUTHOR_AFFILIATION_ONLY: dict[str, Any] = {
    "osti_id": "3024995",
    "title": "Numerically exact configuration interaction",
    "research_orgs": ["Lawrence Berkeley National Laboratory (LBNL), Berkeley, CA (United States)"],
    "authors": [
        "Shayit, Agam [University of Washington, Seattle, WA (United States)]",
        "Noireaux, Vincent [University of Minnesota, Minneapolis, MN (United States)]",
    ],
    "links": [{"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/3024995"}],
}

# The reported false positive: matched only because the scanned PDF mentions UW.
NO_UW_ANYWHERE: dict[str, Any] = {
    "osti_id": "10158090",
    "title": "Fish Passage Center; Columbia Basin Fish and Wildlife Authority, 1993 Annual Report.",
    "description": "The 1993 downstream migration of juvenile salmon.",
    "research_orgs": ["Columbia Basin Fish and Wildlife Authority, Fish Passage Center"],
    "sponsor_orgs": ["US Bonneville Power Administration"],
    "authors": ["author, Unknown"],
    "links": [{"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/10158090"}],
}

# Shares the "Washington" name but is a different institution.
WASHINGTON_UNIVERSITY_ST_LOUIS: dict[str, Any] = {
    "osti_id": "5551234",
    "title": "A study from St. Louis",
    "research_orgs": ["Washington Univ., St. Louis, MO (United States)"],
    "authors": ["Smith, John [Washington Univ., St. Louis, MO (United States)]"],
    "links": [{"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/5551234"}],
}

# Another Washington-named institution, seen live in OSTI research_orgs.
WESTERN_WASHINGTON_UNIVERSITY: dict[str, Any] = {
    "osti_id": "5559999",
    "title": "A study from Bellingham",
    "research_orgs": ["Western Washington University"],
    "authors": ["Jones, Alice [Western Washington University]"],
    "links": [{"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/5559999"}],
}

# UW is named only in `contributing_org`, which OSTI returns as a bare
# semicolon-separated string rather than a list.  Modelled on osti_id 1415347.
UW_BY_CONTRIBUTING_ORG_ONLY: dict[str, Any] = {
    "osti_id": "1415347",
    "title": "Toward the Development of a Cold Regions Regional-Scale Hydrologic Model",
    "research_orgs": ["Univ. of Alaska, Fairbanks, AK (United States)"],
    "contributing_org": "University of Washington, Department of Civil and Environmental Engineering",
    "authors": ["Hinzman, Larry D. [Univ. of Alaska, Fairbanks, AK (United States)]"],
    "links": [{"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/1415347"}],
}

# A patent assigned to UW; `assignee` is the only affiliation field.
UW_BY_ASSIGNEE_ONLY: dict[str, Any] = {
    "osti_id": "1128706",
    "title": "A patented device",
    "product_type": "Patent",
    "assignee": "University of Washington through its Center for Commercialization (Seattle, WA)",
    "authors": ["Inventor, An"],
    "links": [{"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/1128706"}],
}

# Authored at Brookhaven, merely *about* a UW building.  UW appears in the
# title and abstract but in no affiliation field -- must still be dropped.
ABOUT_A_UW_FACILITY: dict[str, Any] = {
    "osti_id": "2311080",
    "title": "Evaluation of the Radioactive Material Release in the Harborview Research Building",
    "description": "An incident at the University of Washington Harborview facility.",
    "research_orgs": ["Brookhaven National Laboratory (BNL), Upton, NY (United States)"],
    "subjects": ["Harborview"],
    "authors": [
        "Musolino, Stephen V. [Brookhaven National Laboratory (BNL), Upton, NY (United States)]"
    ],
    "links": [{"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/2311080"}],
}

# OSTI packs co-authoring institutions into one semicolon-separated field.
UW_PACKED_WITH_CO_INSTITUTIONS: dict[str, Any] = {
    "osti_id": "5557777",
    "title": "A collaboration",
    "research_orgs": [
        "Washington Univ., St. Louis, MO (United States); "
        "Univ. of Washington, Seattle, WA (United States)"
    ],
    "authors": ["Roe, Pat [Univ. of Washington, Seattle, WA (United States); DOE/OSTI]"],
    "links": [{"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/5557777"}],
}


def _items(spider: OstiSpider, records: list[dict[str, Any]]) -> list[ArticleItem]:
    return [r for r in spider.parse(_page_response(records)) if isinstance(r, ArticleItem)]


class TestAffiliationFiltering:
    def test_keeps_record_affiliated_via_research_org(self, spider: OstiSpider) -> None:
        items = _items(spider, [UW_BY_RESEARCH_ORG])
        assert [item.reference for item in items] == ["3021156"]

    def test_keeps_record_affiliated_only_via_author(self, spider: OstiSpider) -> None:
        items = _items(spider, [UW_BY_AUTHOR_AFFILIATION_ONLY])
        assert [item.reference for item in items] == ["3024995"]

    def test_drops_record_matched_on_full_text_only(self, spider: OstiSpider) -> None:
        assert _items(spider, [NO_UW_ANYWHERE]) == []

    def test_drops_washington_university_st_louis(self, spider: OstiSpider) -> None:
        assert _items(spider, [WASHINGTON_UNIVERSITY_ST_LOUIS]) == []

    def test_drops_western_washington_university(self, spider: OstiSpider) -> None:
        assert _items(spider, [WESTERN_WASHINGTON_UNIVERSITY]) == []

    @pytest.mark.parametrize(
        "institution",
        [
            "University of Washington, Seattle, WA (United States)",
            "Univ. of Washington, Seattle, WA (United States)",
            "Washington Univ., Seattle (USA). Dept. of Physics",
            "University of Washington, Tacoma, WA (United States)",
            "Friday Harbor Laboratories",
            "Washington Sea Grant",
            "Harborview Injury Prevention and Research Center",
            "Board of Regents of University of Washington (Seattle, WA)",
        ],
    )
    def test_recognizes_our_institution(self, institution: str) -> None:
        assert OstiSpider._is_our_institution(institution)

    @pytest.mark.parametrize(
        "institution",
        [
            "Washington Univ., St. Louis, MO (United States)",
            "George Washington Univ., Washington, DC (United States)",
            "Washington State University, Pullman, WA (United States)",
            "Western Washington University",
            "Eastern Washington University, Cheney, WA (United States)",
            # Misspelled in OSTI's own data; seen live on osti_id 897663.
            "Easthern Washington University, Upper Columbia United Tribes Fisheries Research Center",
            "Pacific Northwest National Laboratory (PNNL), Richland, WA (United States)",
            "Columbia Basin Fish and Wildlife Authority, Fish Passage Center",
            "",
        ],
    )
    def test_rejects_other_institutions(self, institution: str) -> None:
        assert not OstiSpider._is_our_institution(institution)

    def test_keeps_record_affiliated_only_via_contributing_org(self, spider: OstiSpider) -> None:
        items = _items(spider, [UW_BY_CONTRIBUTING_ORG_ONLY])
        assert [item.reference for item in items] == ["1415347"]

    def test_keeps_patent_affiliated_only_via_assignee(self, spider: OstiSpider) -> None:
        items = _items(spider, [UW_BY_ASSIGNEE_ONLY])
        assert [item.reference for item in items] == ["1128706"]

    def test_drops_report_about_a_uw_facility(self, spider: OstiSpider) -> None:
        # UW is in the title, abstract and subjects, but no affiliation field.
        assert _items(spider, [ABOUT_A_UW_FACILITY]) == []

    def test_keeps_uw_packed_alongside_other_institutions(self, spider: OstiSpider) -> None:
        items = _items(spider, [UW_PACKED_WITH_CO_INSTITUTIONS])
        assert [item.reference for item in items] == ["5557777"]
        assert items[0].extra is not None
        # Only the UW institution is recorded, not the whole packed field.
        assert items[0].extra["affiliation_evidence"] == {
            "work": ["Univ. of Washington, Seattle, WA (United States)"],
            "author": ["Univ. of Washington, Seattle, WA (United States)"],
        }

    def test_filters_a_mixed_page(self, spider: OstiSpider) -> None:
        items = _items(
            spider,
            [
                UW_BY_RESEARCH_ORG,
                NO_UW_ANYWHERE,
                UW_BY_AUTHOR_AFFILIATION_ONLY,
                WASHINGTON_UNIVERSITY_ST_LOUIS,
            ],
        )
        assert [item.reference for item in items] == ["3021156", "3024995"]

    def test_still_drops_records_missing_title_or_id(self, spider: OstiSpider) -> None:
        untitled = {**UW_BY_RESEARCH_ORG, "osti_id": "999", "title": ""}
        assert _items(spider, [untitled]) == []


class TestAffiliationEvidence:
    def test_evidence_separates_work_from_author_affiliations(self) -> None:
        assert OstiSpider._affiliation_evidence(UW_BY_AUTHOR_AFFILIATION_ONLY) == {
            "work": [],
            "author": ["University of Washington, Seattle, WA (United States)"],
        }
        assert OstiSpider._affiliation_evidence(UW_BY_CONTRIBUTING_ORG_ONLY) == {
            "work": ["University of Washington, Department of Civil and Environmental Engineering"],
            "author": [],
        }

    def test_evidence_is_empty_for_unaffiliated_record(self) -> None:
        assert OstiSpider._affiliation_evidence(NO_UW_ANYWHERE) == {"work": [], "author": []}

    def test_recorded_evidence_is_deduplicated(self, spider: OstiSpider) -> None:
        # The same institution appears on the research org and on the author.
        [item] = _items(spider, [UW_BY_RESEARCH_ORG])
        assert item.extra is not None
        assert item.extra["affiliation_evidence"] == {
            "work": ["University of Washington, Seattle, WA (United States)"],
            "author": ["University of Washington, Seattle, WA (United States)"],
        }

    def test_work_fields_exclude_narrative_fields(self) -> None:
        # Guards the decision that title/description/subjects are not evidence.
        assert "title" not in OstiSpider.WORK_AFFILIATION_FIELDS
        assert "description" not in OstiSpider.WORK_AFFILIATION_FIELDS
        assert "subjects" not in OstiSpider.WORK_AFFILIATION_FIELDS

    def test_work_institutions_reads_orgs_and_sponsors(self) -> None:
        assert OstiSpider._work_institutions(NO_UW_ANYWHERE) == [
            "Columbia Basin Fish and Wildlife Authority, Fish Passage Center",
            "US Bonneville Power Administration",
        ]

    def test_extra_records_the_evidence_and_search_term(self, spider: OstiSpider) -> None:
        [item] = _items(spider, [UW_BY_AUTHOR_AFFILIATION_ONLY])
        assert item.extra is not None
        assert item.extra["affiliation_evidence"] == {
            "work": [],
            "author": ["University of Washington, Seattle, WA (United States)"],
        }
        assert item.extra["search_term"] == "university of washington"

    def test_author_names_still_exclude_affiliation_text(self, spider: OstiSpider) -> None:
        [item] = _items(spider, [UW_BY_AUTHOR_AFFILIATION_ONLY])
        assert item.authors is not None
        assert "University of Washington" not in item.authors


class TestPagination:
    def test_follows_pagination_when_page_is_full(self, spider: OstiSpider) -> None:
        spider.page_size = 2
        results = list(
            spider.parse(_page_response([NO_UW_ANYWHERE, WASHINGTON_UNIVERSITY_ST_LOUIS]))
        )
        requests = [r for r in results if isinstance(r, Request)]
        # A full page of dropped records must still advance the crawl.
        assert len(requests) == 1
        assert "page=2" in requests[0].url

    def test_stops_paginating_on_a_partial_page(self, spider: OstiSpider) -> None:
        results = list(spider.parse(_page_response([UW_BY_RESEARCH_ORG])))
        assert [r for r in results if isinstance(r, Request)] == []
