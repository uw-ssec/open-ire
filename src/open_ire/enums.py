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
