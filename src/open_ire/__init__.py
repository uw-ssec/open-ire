from dotenv import load_dotenv

from .version import version as __version__

load_dotenv()

__all__ = ["__version__"]
