from typing import Any

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

        doi = doi.strip()
        prefixes = [
            "https://doi.org/",
            "http://doi.org/",
            "https://dx.doi.org/",
            "http://dx.doi.org/",
            "doi: ",
            "doi:",
        ]
        for prefix in prefixes:
            if doi.lower().startswith(prefix.lower()):
                doi = doi[len(prefix) :]
                break

        doi = doi.strip()

        if not doi.startswith("10.") or "/" not in doi:
            return None

        return doi

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item

        item.doi = self.normalize(item.doi)
        return item
