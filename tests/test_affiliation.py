import pytest

from open_ire.affiliation import is_uw_affiliation, split_institutions, uw_affiliations

# Affiliation strings taken verbatim from live OSTI records.
UW_AFFILIATIONS = [
    "University of Washington, Seattle, WA (United States)",
    "Univ. of Washington, Seattle, WA (United States)",
    "University of Washington",
    "University of Washington, Seattle, WA (US)",
    # OSTI records older UW affiliations without the "of".
    "Washington Univ., Seattle, WA (United States). Inst. for Nuclear Theory",
    "Washington Univ., Seattle (USA). Dept. of Physics",
    "Washington Univ., Seattle. Dept. of Oceanography",
    "Friday Harbor Laboratories",
    "Washington Sea Grant",
    "Harborview Injury Prevention and Research Center",
    "jane.doe@uw.edu",
    "jane.doe@washington.edu",
]

# Institutions that share the "Washington" name but are not UW, and agencies
# that merely sit in Washington, DC.
NON_UW_AFFILIATIONS = [
    "Washington Univ., St. Louis, MO (United States)",
    "Washington University, St. Louis, MO (United States)",
    "Washington Univ., St. Louis, Mo. (USA). Dept. of Chemistry",
    "Washington Univ., St Louis, Mo.",
    "George Washington Univ., Washington, DC (United States)",
    "The George Washington University, Washington, DC (United States)",
    "Washington State University, Pullman, WA (United States)",
    "Western Washington University",
    "Eastern Washington University, Cheney, WA (United States)",
    "Central Washington Univ., Ellensburg, WA (United States)",
    "Atomic Energy Commission (AEC), Washington, DC (United States)",
    "Energy Research and Development Administration, Washington, D.C. (USA)",
    "Columbia Basin Fish and Wildlife Authority, Fish Passage Center",
    "Pacific Northwest National Laboratory (PNNL), Richland, WA (United States)",
    "Lawrence Berkeley National Laboratory (LBNL), Berkeley, CA (United States)",
    "University of Wisconsin, Madison, WI (United States)",
    "",
]


@pytest.mark.parametrize("text", UW_AFFILIATIONS)
def test_recognises_uw_affiliations(text: str) -> None:
    assert is_uw_affiliation(text)


@pytest.mark.parametrize("text", NON_UW_AFFILIATIONS)
def test_rejects_non_uw_affiliations(text: str) -> None:
    assert not is_uw_affiliation(text)


def test_matching_is_case_and_whitespace_insensitive() -> None:
    assert is_uw_affiliation("UNIVERSITY   OF\n WASHINGTON, Seattle")


def test_uw_affiliations_keeps_only_uw_entries_in_order() -> None:
    candidates = [
        "Lawrence Berkeley National Laboratory (LBNL), Berkeley, CA (United States)",
        "University of Washington, Seattle, WA (United States)",
        "Washington Univ., St. Louis, MO (United States)",
        "Friday Harbor Laboratories",
    ]
    assert uw_affiliations(candidates) == [
        "University of Washington, Seattle, WA (United States)",
        "Friday Harbor Laboratories",
    ]


def test_uw_affiliations_drops_empty_entries() -> None:
    assert uw_affiliations(["", "   ", "University of Washington"]) == ["University of Washington"]


class TestSplitInstitutions:
    def test_splits_a_semicolon_packed_field(self) -> None:
        packed = (
            "Univ. of Washington, Seattle, WA (United States); "
            "Oak Ridge National Lab. (ORNL), Oak Ridge, TN (United States)"
        )
        assert split_institutions(packed) == [
            "Univ. of Washington, Seattle, WA (United States)",
            "Oak Ridge National Lab. (ORNL), Oak Ridge, TN (United States)",
        ]

    def test_leaves_a_single_institution_intact(self) -> None:
        # The commas inside one institution name are not separators.
        assert split_institutions("Univ. of Washington, Seattle, WA (United States)") == [
            "Univ. of Washington, Seattle, WA (United States)"
        ]

    @pytest.mark.parametrize("text", ["", "   ", ";", " ; ; "])
    def test_yields_nothing_for_empty_input(self, text: str) -> None:
        assert split_institutions(text) == []

    def test_a_co_institution_does_not_suppress_a_uw_match(self) -> None:
        # Matched whole, "St. Louis" would disqualify the entire string and
        # lose a genuine UW affiliation.
        packed = "Washington Univ., St. Louis, MO (United States); University of Washington"
        assert not is_uw_affiliation(packed)
        assert uw_affiliations(split_institutions(packed)) == ["University of Washington"]
