from .base import *  # noqa: F403
from .base import ITEM_PIPELINES

EXTENSIONS = {"open_ire.logging.OpenIRELogger": 100}
LOG_LEVEL = "WARNING"
OPEN_IRE_LOG_LEVEL = "DEBUG"

SHAREPOINT_BASE_PATH = "open_ire_dev"

if "open_ire.pipelines.SharePointPipeline" in ITEM_PIPELINES:
    del ITEM_PIPELINES["open_ire.pipelines.SharePointPipeline"]
