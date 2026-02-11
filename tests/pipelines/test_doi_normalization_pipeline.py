import pytest

from open_ire.pipelines import DOINormalizationPipeline


class TestDOINormalizationPipeline:
    """Tests the DOI normalization pipeline."""

    @pytest.fixture
    def pipeline(self):
        """Create a pipeline instance for testing."""
        return DOINormalizationPipeline()

    def test_normalize_full_doi_url(self, pipeline, spider, item):
        """Test normalizing a full DOI URL."""
        item.doi = " https://doi.org/10.1234/test.doi"

        result = pipeline.process_item(item, spider)

        assert result.doi == "10.1234/test.doi"

    def test_normalize_already_normalized_doi(self, pipeline, spider, item):
        """Test that already normalized DOIs are unchanged."""
        item.doi = "10.1234/test.doi "

        result = pipeline.process_item(item, spider)

        assert result.doi == "10.1234/test.doi"

    def test_normalize_none_doi(self, pipeline, spider, item):
        """Test that None DOI values are handled correctly."""
        item.doi = None

        result = pipeline.process_item(item, spider)

        assert result.doi is None

    def test_normalize_empty_string_doi(self, pipeline, spider, item):
        """Test that empty string DOI values are normalized to None."""
        item.doi = ""

        result = pipeline.process_item(item, spider)

        assert result.doi is None

    def test_normalize_whitespace_only_doi(self, pipeline, spider, item):
        """Test that whitespace-only DOI values are normalized to None."""
        item.doi = "   "

        result = pipeline.process_item(item, spider)

        assert result.doi is None

    def test_normalize_non_string_doi(self, pipeline, spider, item):
        """Test that non-string DOI values are normalized to None."""
        item.doi = 12345

        result = pipeline.process_item(item, spider)

        assert result.doi is None
