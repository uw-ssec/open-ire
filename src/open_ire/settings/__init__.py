import os
from pathlib import Path

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

if ENVIRONMENT == "production":
    from .production import *  # noqa: F403
elif ENVIRONMENT == "development":
    from .development import *  # noqa: F403

    # Machine-specific overrides, if any. Checking for the file first (rather
    # than catching ImportError) keeps errors raised *inside* it from being
    # swallowed.
    if (Path(__file__).parent / "development_local.py").exists():
        from .development_local import *  # noqa: F403
else:
    msg = f"Invalid environment: {ENVIRONMENT}"
    raise ValueError(msg)

# LOG_FILE may be set by any layer above. Scrapy opens it in configure_logging()
# before anything else runs, and won't create the parent directory itself.
if log_file := globals().get("LOG_FILE"):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
