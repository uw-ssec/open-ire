from dotenv import dotenv_values, find_dotenv, load_dotenv

from .version import version as __version__

load_dotenv()

# Migration shim, added 2026-08-07: ENVIRONMENT was renamed to
# OPEN_IRE_ENVIRONMENT. Drop it once everyone has moved over.
_dotenv = dotenv_values(find_dotenv(usecwd=True))
if "ENVIRONMENT" in _dotenv and "OPEN_IRE_ENVIRONMENT" not in _dotenv:
    msg = "ENVIRONMENT is no longer read. Rename it to OPEN_IRE_ENVIRONMENT in your .env."
    raise ValueError(msg)

__all__ = ["__version__"]
