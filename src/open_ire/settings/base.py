import os

from requests import utils as requests_utils

# Scrapy settings for open_ire project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

BOT_NAME = "open_ire"
SPIDER_MODULES = ["open_ire.spiders"]
NEWSPIDER_MODULE = "open_ire.spiders"
USER_AGENT = requests_utils.default_user_agent()
ROBOTSTXT_USER_AGENT = USER_AGENT

# Obey robots.txt rules
ROBOTSTXT_OBEY = True

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
    # Filtering pipelines:
    "open_ire.pipelines.DuplicatesPipeline": 1,
    "open_ire.pipelines.SkipExistingPipeline": 2,
    # Author identifier storage (early, before article processing):
    "open_ire.pipelines.AuthorIdentifierPipeline": 5,
    # Data normalization pipelines:
    "open_ire.pipelines.DOINormalizationPipeline": 10,
    "open_ire.pipelines.DOIDuplicatesPipeline": 20,
    # Processing pipelines:
    "open_ire.pipelines.LocalFilePipeline": 100,
    "open_ire.pipelines.FileReferencePipeline": 200,
    "open_ire.pipelines.SharePointPipeline": 300,
    "open_ire.pipelines.SQLModelPipeline": 400,
    "open_ire.pipelines.AuthorshipPipeline": 410,
}
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
FILES_STORE = "output"
MEDIA_ALLOW_REDIRECTS = True
DOWNLOAD_DELAY = 3

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
AUTOTHROTTLE_ENABLED = True
# The initial download delay
AUTOTHROTTLE_START_DELAY = 5
# The maximum download delay to be set in case of high latencies
AUTOTHROTTLE_MAX_DELAY = 60
# The average number of requests Scrapy should be sending in parallel to
# each remote server
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
# AUTOTHROTTLE_DEBUG = False

# Set settings whose default value is deprecated to a future-proof value
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"

# Playwright settings
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": False,
    "timeout": 20 * 1000,  # 20 seconds
}

# Open IRE Settings
OPEN_IRE_SEARCH_TERMS = [
    "friday harbor laboratories",
    "harborview injury prevention and research center",
    "univ. of washington",
    "university of washington",
    "uw.edu",
    "washington sea grant",
    "washington.edu",
]
OPEN_IRE_SHAREPOINT_BASE_PATH = os.getenv("OPEN_IRE_SHAREPOINT_BASE_PATH", "open_ire")
OPEN_IRE_DATABASE_FILE = "dbs/open_ire.db"
OPEN_IRE_DEFAULT_TERMS = ",".join(OPEN_IRE_SEARCH_TERMS)
OPEN_IRE_SKIP_EXISTING = False
OPEN_IRE_SKIP_EXISTING_WITH_FILES = False
OPEN_IRE_CONTACT_EMAIL = "uwtextmine@uw.edu"

# Names that identify our institution in a repository's affiliation metadata.
# Matched as case-insensitive substrings of a single institution string, so a
# prefix such as "friday harbor lab" also covers "Friday Harbor Laboratories",
# and "washington univ" covers both "Washington Univ." and the spelled-out
# "Washington University" that older records use for UW.
OPEN_IRE_INSTITUTION_NAMES = [
    "friday harbor lab",
    "harborview",
    "u. of washington",
    "univ of washington",
    "univ. of washington",
    "university of washington",
    "uw.edu",
    "washington sea grant",
    "washington univ",
    "washington.edu",
]

# Other institutions whose names contain one of the above. Checked against the
# same institution string: a name match that also matches one of these belongs
# to a different institution, not to ours.
OPEN_IRE_EXCLUDED_INSTITUTIONS = [
    "central washington univ",
    "eastern washington univ",
    # Misspelled in some OSTI records ("Easthern Washington University").
    "easthern washington univ",
    "george washington",
    "saint louis",
    "st louis",
    "st. louis",
    "washington state univ",
    "washington, d.c.",
    "washington, dc",
    "western washington univ",
]

# Default log level for `open_ire` logger
OPEN_IRE_LOG_LEVEL = "INFO"
OPEN_IRE_LOG_DROPPED_ITEMS = True
LOG_FORMATTER = "open_ire.logging.OpenIRELogFormatter"
# Override log levels for specific modules. For example:
# OPEN_IRE_LOG_LEVELS = {
#   "open_ire.pipelines.sharepoint_pipeline": "WARNING"
# }
OPEN_IRE_LOG_LEVELS = {}

OPEN_IRE_OPENALEX_INSTITUTION_ID = "i201448701"
OPEN_IRE_OPENALEX_AMBIGUOUS_AUTHORS_FILE = f"{FILES_STORE}/openalex_ambiguous_authors.csv"
OPEN_IRE_WOS_ORGANIZATION = "University of Washington"
