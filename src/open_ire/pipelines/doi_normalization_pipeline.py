import re
from typing import Any
from urllib.parse import unquote

from open_ire.items import ArticleItem


class DOINormalizationPipeline:
    """
    Normalizes DOI field format across all spiders for consistency.

    Strips 'https://doi.org/' prefix from DOI field to store just the identifier,
    while preserving the full URL format in the url field when appropriate.
    """

    @staticmethod
    def normalize(doi: str | None) -> str | None:
        """Extract a valid DOI identifier from various formats."""
        if not isinstance(doi, str) or not doi.strip():
            return None

        doi = unquote(doi.strip())
        prefixes = [
            "https://doi.org/",
            "http://doi.org/",
            "https://dx.doi.org/",
            "http://dx.doi.org/",
            "doi: ",
            "doi:",
        ]
        for prefix in prefixes:
            while doi.lower().startswith(prefix.lower()):
                doi = doi[len(prefix) :].strip()

        # DOIs are case-insensitive, so might as well keep them uniform
        doi = doi.strip().lstrip("/").lower()

        if doi.startswith("10.") and "/" in doi:
            return doi

        doi_pattern = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
        match = doi_pattern.search(doi)
        if match:
            return match.group(0)

        return None

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item
        item.doi = self.normalize(item.doi)
        return item
