from enum import StrEnum


class OAStatus(StrEnum):
    PUBLISHED = "published"
    READY = "ready"
    PARTIAL = "partial"


class OAEvidenceKind(StrEnum):
    LICENSE = "license"
    EXTERNAL_OA = "external_oa"
    VERSION = "version"
    MANUAL = "manual"
    FACULTY_AUTHOR = "faculty_author"


class OATransitionReason(StrEnum):
    """Standardized reasons for OA status transitions.

    These values correspond logically to OAEvidenceKind:
    - LICENSE_OA: Transition based on open access license evidence (OAEvidenceKind.LICENSE)
    - EXTERNAL_OA: Transition based on external OA availability (OAEvidenceKind.EXTERNAL_OA)
    - VERSION_AVAILABLE: Transition based on version evidence (OAEvidenceKind.VERSION)
    - MANUAL_REVIEW: Transition based on manual review (OAEvidenceKind.MANUAL)
    - FACULTY_AUTHOR: Transition based on faculty authorship (OAEvidenceKind.FACULTY_AUTHOR)
    """

    LICENSE_OA = "license_oa"
    EXTERNAL_OA = "external_oa"
    VERSION_AVAILABLE = "version_available"
    MANUAL_REVIEW = "manual_review"
    FACULTY_AUTHOR = "faculty_author"
