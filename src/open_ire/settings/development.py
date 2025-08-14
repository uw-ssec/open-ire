from .base import *  # noqa: F403

LOG_LEVEL = "DEBUG"
SHAREPOINT_BASE_PATH = "open_ire_dev"
ITEM_PIPELINES = {
    "open_ire.pipelines.DuplicatesPipeline": 100,
    "open_ire.pipelines.LocalFilePipeline": 200,
    "open_ire.pipelines.FileReferencePipeline": 300,
    "open_ire.pipelines.SQLModelPipeline": 500,
}
