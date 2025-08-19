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

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"

# Obey robots.txt rules
ROBOTSTXT_OBEY = True

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
    "open_ire.pipelines.DuplicatesPipeline": 100,
    "open_ire.pipelines.LocalFilePipeline": 200,
    "open_ire.pipelines.FileReferencePipeline": 300,
    "open_ire.pipelines.SharePointPipeline": 400,
    "open_ire.pipelines.SQLModelPipeline": 500,
}
FILES_STORE = "output"
MEDIA_ALLOW_REDIRECTS = True

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

# Open IRE Settings
OPEN_IRE_SEARCH_TERMS = [
    "univ. of washington",
    "university of washington",
    "uw.edu",
    "washington sea grant",
    "washinton.edu",
]
SHAREPOINT_BASE_PATH = "open_ire"
OPEN_IRE_DATABASE_FILE = "dbs/open_ire.db"
OPEN_IRE_DEFAULT_TERMS = ",".join(OPEN_IRE_SEARCH_TERMS)
