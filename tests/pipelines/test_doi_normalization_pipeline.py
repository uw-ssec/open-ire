from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from open_ire.items import ArticleItem
from open_ire.pipelines import DOINormalizationPipeline


class TestDOINormalizationPipeline:
    """Tests the DOI normalization pipeline."""

    @pytest.fixture
    def pipeline(self) -> DOINormalizationPipeline:
        """Create a pipeline instance for testing."""
        return DOINormalizationPipeline()

    def test_passes_through_non_article_items(self, pipeline: DOINormalizationPipeline) -> None:
        """Test that non-ArticleItem items are passed through unchanged."""
        item = MagicMock(spec=Any)

        result = pipeline.process_item(item)

        assert result is item

    def test_normalize_full_doi_url(
        self, pipeline: DOINormalizationPipeline, item: ArticleItem
    ) -> None:
        """Test normalizing a full DOI URL."""
        item.doi = " https://doi.org/10.1234/test.doi"

        result = pipeline.process_item(item)

        assert result.doi == "10.1234/test.doi"

    def test_normalize_already_normalized_doi(
        self, pipeline: DOINormalizationPipeline, item: ArticleItem
    ) -> None:
        """Test that already normalized DOIs are unchanged."""
        item.doi = "10.1234/test.doi "

        result = pipeline.process_item(item)

        assert result.doi == "10.1234/test.doi"

    def test_normalize_none_doi(
        self, pipeline: DOINormalizationPipeline, item: ArticleItem
    ) -> None:
        """Test that None DOI values are handled correctly."""
        item.doi = None

        result = pipeline.process_item(item)

        assert result.doi is None

    def test_normalize_empty_string_doi(
        self, pipeline: DOINormalizationPipeline, item: ArticleItem
    ) -> None:
        """Test that empty string DOI values are normalized to None."""
        item.doi = ""

        result = pipeline.process_item(item)

        assert result.doi is None

    def test_normalize_whitespace_only_doi(
        self, pipeline: DOINormalizationPipeline, item: ArticleItem
    ) -> None:
        """Test that whitespace-only DOI values are normalized to None."""
        item.doi = "   "

        result = pipeline.process_item(item)

        assert result.doi is None

    def test_normalize_non_string_doi(
        self, pipeline: DOINormalizationPipeline, item: ArticleItem
    ) -> None:
        """Test that non-string DOI values are normalized to None."""
        item.doi = cast(Any, 12345)

        result = pipeline.process_item(item)

        assert result.doi is None
