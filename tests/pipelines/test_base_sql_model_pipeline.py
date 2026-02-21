"""Contract tests for BaseSQLModelPipeline subclasses."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from open_ire.errors import ConfigurationError
from open_ire.pipelines import BaseSQLModelPipeline, SQLModelPipeline


def _base_sql_model_pipeline_subclasses() -> list[type[BaseSQLModelPipeline]]:
    return sorted(BaseSQLModelPipeline.__subclasses__(), key=lambda cls: cls.__name__)


BASE_PIPELINE_SUBCLASSES = _base_sql_model_pipeline_subclasses()


class TestBaseSQLModelPipeline:
    """Contract tests for BaseSQLModelPipeline."""

    @pytest.mark.parametrize("pipeline_cls", BASE_PIPELINE_SUBCLASSES)
    def test_base_subclasses_do_not_override_from_crawler(
        self,
        pipeline_cls: type[BaseSQLModelPipeline],
    ) -> None:
        """Ensure subclasses inherit the shared from_crawler() setup."""
        assert "from_crawler" not in pipeline_cls.__dict__, (
            f"{pipeline_cls.__name__} overrides from_crawler(); this can bypass "
            "BaseSQLModelPipeline setup and validations."
        )

    @pytest.mark.parametrize("pipeline_cls", BASE_PIPELINE_SUBCLASSES)
    def test_base_subclasses_inherit_from_crawler_setup(
        self, pipeline_cls: type[BaseSQLModelPipeline], tmp_path: Path
    ) -> None:
        missing_db = str(tmp_path / "missing_parent" / "open_ire.db")
        crawler = SimpleNamespace(
            settings={"OPEN_IRE_DATABASE_FILE": missing_db, "FILES_STORE": str(tmp_path)}
        )

        pipeline = pipeline_cls.from_crawler(crawler)  # type: ignore[arg-type]

        assert isinstance(pipeline, pipeline_cls)
        assert pipeline.crawler is crawler
        assert Path(missing_db).parent.exists()

    def test_from_crawler_raises_if_open_ire_database_file_missing(self, tmp_path: Path) -> None:
        crawler = SimpleNamespace(settings={"FILES_STORE": str(tmp_path)})

        with pytest.raises(ConfigurationError, match="OPEN_IRE_DATABASE_FILE"):
            SQLModelPipeline.from_crawler(crawler)  # type: ignore[arg-type]

    def test_from_crawler_raises_if_files_store_missing(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "dbs" / "open_ire.db")
        crawler = SimpleNamespace(settings={"OPEN_IRE_DATABASE_FILE": db_path})

        with pytest.raises(ConfigurationError, match="FILES_STORE"):
            SQLModelPipeline.from_crawler(crawler)  # type: ignore[arg-type]
