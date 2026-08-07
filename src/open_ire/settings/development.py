from pathlib import Path

from .base import *  # noqa: F403
from .base import ITEM_PIPELINES

EXTENSIONS = {"open_ire.logging.OpenIRELogger": 100}
LOG_LEVEL = "WARNING"
OPEN_IRE_LOG_LEVEL = "DEBUG"
OPEN_IRE_LOG_DROPPED_ITEMS = False

SHAREPOINT_BASE_PATH = "open_ire_dev"

# HTTPCACHE_DIR is relative to the Scrapy data dir, which is .scrapy/
HTTPCACHE_DIR = "httpcache"
HTTPCACHE_EXPIRATION_SECS = 86400
HTTPCACHE_ENABLED = True

if "open_ire.pipelines.SharePointPipeline" in ITEM_PIPELINES:
    del ITEM_PIPELINES["open_ire.pipelines.SharePointPipeline"]

# Machine-specific overrides, if any. Checking for the file first (rather than
# catching ImportError) keeps errors raised *inside* it from being swallowed.
if (Path(__file__).parent / "development_local.py").exists():
    from .development_local import *  # noqa: F403
