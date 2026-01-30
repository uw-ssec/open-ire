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


class OAEvidenceKind(StrEnum):
    """Categories of evidence used to determine Open Access (OA) compliance status.

    Each kind represents a different source or method of establishing OA eligibility:
    - LICENSE: Evidence from license information (e.g., Creative Commons licenses).
    - EXTERNAL_OA: Evidence from external OA availability checks.
    - VERSION: Evidence based on article version (e.g., accepted manuscript).
    - MANUAL: Evidence from manual review processes.
    - FACULTY_AUTHOR: Evidence based on faculty authorship status.
    """

    LICENSE = "license"
    EXTERNAL_OA = "external_oa"
    VERSION = "version"
    MANUAL = "manual"
    FACULTY_AUTHOR = "faculty_author"


class DepositTransitionReason(StrEnum):
    """Standardized reasons for article deposit status transitions.

    These values correspond logically to DepositEvidenceKind:
    - LICENSE_OA: Transition based on open access license evidence (DepositEvidenceKind.LICENSE)
    - EXTERNAL_OA: Transition based on external OA availability (DepositEvidenceKind.EXTERNAL_OA)
    - VERSION_AVAILABLE: Transition based on version evidence (DepositEvidenceKind.VERSION)
    - MANUAL_REVIEW: Transition based on manual review (DepositEvidenceKind.MANUAL)
    - FACULTY_AUTHOR: Transition based on faculty authorship (DepositEvidenceKind.FACULTY_AUTHOR)
    """

    LICENSE_OA = "license_oa"
    EXTERNAL_OA = "external_oa"
    VERSION_AVAILABLE = "version_available"
    MANUAL_REVIEW = "manual_review"
    FACULTY_AUTHOR = "faculty_author"
