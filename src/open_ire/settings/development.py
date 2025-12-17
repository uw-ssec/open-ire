from .base import *  # noqa: F403
from .base import ITEM_PIPELINES

LOG_LEVEL = "DEBUG"
SHAREPOINT_BASE_PATH = "open_ire_dev"

if "open_ire.pipelines.SharePointPipeline" in ITEM_PIPELINES:
    del ITEM_PIPELINES["open_ire.pipelines.SharePointPipeline"]
