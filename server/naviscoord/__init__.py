"""NavisCoord — coordination intelligence for Autodesk Navisworks.

The engine turns a raw clash export into a small, ranked, explained set of
coordination problems. It runs standalone; the Navisworks addin and the MCP
server are transport around it.
"""

from .model import SCHEMA, Clash, ClashExport, ElementRef, Issue, ModelSource
from .profile import Profile

__version__ = "0.3.0"

__all__ = [
    "SCHEMA",
    "Clash",
    "ClashExport",
    "ElementRef",
    "Issue",
    "ModelSource",
    "Profile",
    "__version__",
]
