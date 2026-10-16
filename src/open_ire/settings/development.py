from .base import *  # noqa: F403
from .base import ITEM_PIPELINES, OPEN_IRE_LOGGER_LEVELS, OPEN_IRE_SHAREPOINT_BASE_PATH

# LOG_LEVEL gates Scrapy's handler (the sink) and, because Scrapy leaves the
# root logger at NOTSET, is also what makes open_ire.* verbose here.
LOG_LEVEL = "DEBUG"
# Clamp third-party loggers that would otherwise drown out open_ire.* logs.
# See `scrapy.utils.log.DEFAULT_LOGGING` for the levels Scrapy sets itself.
OPEN_IRE_LOGGER_LEVELS = {
    **OPEN_IRE_LOGGER_LEVELS,
    "scrapy": "WARNING",  # Scrapy sets DEBUG
    "alembic": "WARNING",  # unset; inherits root
    "scrapy-playwright": "WARNING",  # unset; inherits root
}
# Dropped items are logged in full, which adds a lot of noise.
OPEN_IRE_LOG_DROPPED_ITEMS = False

# HTTPCACHE_DIR is relative to the Scrapy data dir, which is .scrapy/
HTTPCACHE_DIR = "httpcache"
HTTPCACHE_EXPIRATION_SECS = 86400
HTTPCACHE_ENABLED = True

# Kept even though SharePointPipeline is removed below: if the pipeline is
# re-enabled locally, this keeps dev crawls out of the production folder.
# Suffixed rather than replaced so an OPEN_IRE_SHAREPOINT_BASE_PATH override
# still applies in development.
OPEN_IRE_SHAREPOINT_BASE_PATH += "_dev"

# Copy before removing: `from .base import` binds the same dict object, so
# deleting in place would strip the pipeline from base.ITEM_PIPELINES too.
ITEM_PIPELINES = dict(ITEM_PIPELINES)
ITEM_PIPELINES.pop("open_ire.pipelines.SharePointPipeline", None)
