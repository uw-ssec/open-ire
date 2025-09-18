from datetime import datetime

from .base import *  # noqa: F403

# Crawl responsibly by identifying yourself (and your website) on the user-agent
USER_AGENT = "open_ire (+https://lib.uw.edu/)"

# Logging
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_LEVEL = "INFO"
LOG_FILE = f"output/open_ire__{timestamp}.log"

# Enable or disable extensions
# See https://docs.scrapy.org/en/latest/topics/extensions.html
EXTENSIONS = {
    "scrapy.extensions.corestats.CoreStats": 500,
    "scrapy.extensions.periodic_log.PeriodicLog": 500,
}

# Playwright settings
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": True,
    "timeout": 20 * 1000,  # 20 seconds
}

# Configure maximum concurrent requests performed by Scrapy (default: 16)
# CONCURRENT_REQUESTS = 32

# Configure a delay for requests for the same website (default: 0)
# See https://docs.scrapy.org/en/latest/topics/settings.html#download-delay
# See also autothrottle settings and docs
# DOWNLOAD_DELAY = 2
# The download delay setting will honor only one of:
# CONCURRENT_REQUESTS_PER_DOMAIN = 16
# CONCURRENT_REQUESTS_PER_IP = 16
