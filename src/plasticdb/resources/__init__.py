from ._base import Resource
from .catalogue import Microorganisms, Papers, Plastics
from .downloads import Downloads
from .evidence import Evidence, Pathways, Search, StatsResource
from .proteins import Proteins, Variants
from .releases import Releases

__all__ = [
    "Downloads",
    "Evidence",
    "Microorganisms",
    "Papers",
    "Pathways",
    "Plastics",
    "Proteins",
    "Releases",
    "Resource",
    "Search",
    "StatsResource",
    "Variants",
]
