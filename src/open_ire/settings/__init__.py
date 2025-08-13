import os

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

if ENVIRONMENT == "production":
    from .production import *  # noqa: F403
elif ENVIRONMENT == "development":
    from .development import *  # noqa: F403
else:
    msg = f"Invalid environment: {ENVIRONMENT}"
    raise ValueError(msg)
