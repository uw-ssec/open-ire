from .base import *  # noqa: F403
from .base import ITEM_PIPELINES

EXTENSIONS = {"open_ire.logging.OpenIRELogger": 100}
LOG_LEVEL = "WARNING"
OPEN_IRE_LOG_LEVEL = "DEBUG"
OPEN_IRE_LOG_DROPPED_ITEMS = False

# HTTPCACHE_DIR is relative to the Scrapy data dir, which is .scrapy/
HTTPCACHE_DIR = "httpcache"
HTTPCACHE_EXPIRATION_SECS = 86400
HTTPCACHE_ENABLED = True

# Kept even though SharePointPipeline is removed below: if the pipeline is
# re-enabled locally, this keeps dev crawls out of the production folder.
SHAREPOINT_BASE_PATH = "open_ire_dev"

# Copy before removing: `from .base import` binds the same dict object, so
# deleting in place would strip the pipeline from base.ITEM_PIPELINES too.
ITEM_PIPELINES = dict(ITEM_PIPELINES)
ITEM_PIPELINES.pop("open_ire.pipelines.SharePointPipeline", None)
