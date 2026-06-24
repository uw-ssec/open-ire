from enum import StrEnum


class DepositStatus(StrEnum):
    """Status indicating the readiness of an article for deposit in ResearchWorks.

    Values represent progressive stages of deposit readiness:
    - PUBLISHED: Article has been deposited and is publicly available.
    - READY: Article is fully prepared and eligible for deposit.
    - PARTIAL: Article has some requirements met but is not yet ready for deposit.
    """

    PUBLISHED = "published"
    READY = "ready"
    PARTIAL = "partial"


class DepositWarrant(StrEnum):
    """Kinds of warrant that can support depositing an article in ResearchWorks.

    Each kind names a different source or method of establishing deposit eligibility:
    - LICENSE: A redistribution-permitting license (e.g., Creative Commons).
    - EXTERNAL_OA: External open-access availability checks.
    - VERSION: Article version (e.g., accepted manuscript).
    - MANUAL: Manual review.
    - FACULTY_AUTHOR: Faculty authorship status.
    """

    LICENSE = "license"
    EXTERNAL_OA = "external_oa"
    VERSION = "version"
    MANUAL = "manual"
    FACULTY_AUTHOR = "faculty_author"


class DepositTransitionReason(StrEnum):
    """Standardized reasons for article deposit status transitions.

    These values correspond logically to DepositWarrant:
    - LICENSE_OA: Transition based on license warrant (DepositWarrant.LICENSE)
    - EXTERNAL_OA: Transition based on external OA availability (DepositWarrant.EXTERNAL_OA)
    - VERSION_AVAILABLE: Transition based on version warrant (DepositWarrant.VERSION)
    - MANUAL_REVIEW: Transition based on manual review (DepositWarrant.MANUAL)
    - FACULTY_AUTHOR: Transition based on faculty authorship (DepositWarrant.FACULTY_AUTHOR)
    """

    LICENSE_OA = "license_oa"
    EXTERNAL_OA = "external_oa"
    VERSION_AVAILABLE = "version_available"
    MANUAL_REVIEW = "manual_review"
    FACULTY_AUTHOR = "faculty_author"


class ArticleType(StrEnum):
    """Normalized classification for publication types.

    Used to determine if a publication falls under the faculty Open Access Policy.
    Per the library guide, peer-reviewed journal articles and conference papers
    created without expectation of payment are considered scholarly articles subject
    to the OA Policy. Books, book chapters, data sets, and creative works do not
    fall under the policy (though deposit is still encouraged).

    Values:
    - SCHOLARLY_ARTICLE: Peer-reviewed journal articles and conference papers
    - OTHER: Books, book chapters, editorials, reviews, and other non-scholarly works
    """

    SCHOLARLY_ARTICLE = "scholarly-article"
    OTHER = "other"
