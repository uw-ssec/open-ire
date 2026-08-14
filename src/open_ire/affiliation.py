"""Recognise University of Washington affiliations in free-text institution strings.

Repository search APIs generally match a query against indexed *full text*, not
just metadata, so a report that merely mentions "University of Washington" in
its references or acknowledgements is returned as a hit.  Spiders therefore
cannot treat "the source returned it" as evidence of a UW connection; they need
to confirm the affiliation from the record's own structured fields.

:func:`is_uw_affiliation` decides whether a single institution string names UW.
It deliberately answers "no" for the other institutions whose names contain
"Washington" -- Washington University in St. Louis, George Washington
University, Washington State University, and the Western/Eastern/Central
Washington universities -- and for federal agencies headquartered in
Washington, DC.

Known ambiguity: a bare "Washington University" carrying no city is accepted as
UW, because OSTI records many genuine UW affiliations that way (e.g. "Washington
Univ., Seattle (USA)").  Washington University in St. Louis is written that way
too, but in practice its records name the city; of 600 live OSTI records
sampled, the only one matched on the bare form also carried an explicit
"Univ. of Washington, Seattle" affiliation.
"""

import re
from collections.abc import Iterable

__all__ = ["is_uw_affiliation", "split_institutions", "uw_affiliations"]

# Institution names that denote UW.  ``washington univ`` is included because
# OSTI records the older UW affiliations that way (e.g. "Washington Univ.,
# Seattle (USA). Dept. of Physics"); _NOT_UW below rules out the St. Louis and
# Pullman institutions that share the pattern.
_UW_NAME = re.compile(
    r"""
    university \s+ of \s+ washington
  | univ \.? \s+ of \s+ washington
  | u \. \s* of \s+ washington
  | washington \s+ univ (?: \. | ersity )?
  | friday \s+ harbor \s+ lab
  | washington \s+ sea \s+ grant
  | harborview
  | \b uw \. edu \b
  | \b washington \. edu \b
    """,
    re.VERBOSE,
)

# Disqualifiers, matched against the same string as _UW_NAME.  A name match
# that also matches one of these belongs to a different institution.
_NOT_UW = re.compile(
    r"""
    \b st \.? \s* louis \b
  | \b saint \s+ louis \b
  | ,\s* mo \b
  | george \s+ washington
  | washington \s* ,? \s* d \.? \s* c \.?
  | (?: washington \s+ state | western \s+ washington
      | eastern \s+ washington | central \s+ washington )
    \s+ univ (?: \. | ersity )?
  | \b wsu \b
    """,
    re.VERBOSE,
)

# OSTI packs several institutions into one field, separated by semicolons.
_INSTITUTION_SEPARATOR = re.compile(r"\s*;\s*")


def _normalise(text: str) -> str:
    """Collapse whitespace and case-fold *text* for matching."""
    return re.sub(r"\s+", " ", text or "").casefold()


def split_institutions(text: str) -> list[str]:
    """Split a packed affiliation field into its individual institutions.

    A single OSTI field can name several institutions at once, e.g.
    ``"Univ. of Washington, Seattle, WA (United States); DOE/OSTI"``.  They
    have to be matched separately: otherwise one institution's disqualifying
    words -- "St. Louis" in a co-authoring institution, say -- would suppress
    a genuine UW match elsewhere in the same string.
    """
    return [part.strip() for part in _INSTITUTION_SEPARATOR.split(text or "") if part.strip()]


def is_uw_affiliation(text: str) -> bool:
    """Return ``True`` if *text* names the University of Washington.

    *text* is a single institution string, such as an OSTI ``research_orgs``
    entry or the bracketed affiliation of one author.  Passing a whole document
    (a title, or an abstract) defeats the disqualifier check, because the
    disqualifying words must belong to the *same* institution name.
    """
    normalised = _normalise(text)
    return bool(_UW_NAME.search(normalised)) and not _NOT_UW.search(normalised)


def uw_affiliations(candidates: Iterable[str]) -> list[str]:
    """Return the entries of *candidates* that name UW, preserving order."""
    return [text for text in candidates if text and is_uw_affiliation(text)]
