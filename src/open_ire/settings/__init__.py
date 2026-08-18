import os

OPEN_IRE_ENVIRONMENT = os.getenv("OPEN_IRE_ENVIRONMENT", "development")

if OPEN_IRE_ENVIRONMENT == "production":
    from .production import *  # noqa: F403
elif OPEN_IRE_ENVIRONMENT == "development":
    from .development import *  # noqa: F403
else:
    msg = f"Invalid environment: {OPEN_IRE_ENVIRONMENT}"
    raise ValueError(msg)
