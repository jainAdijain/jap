"""ATS adapters (I1): one module per source, each with fetch() and board_info().

Adding a source means adding one module and one entry in ADAPTERS.
"""

from schemas.job import ATS
from sources import ashby, greenhouse, lever
from sources.base import Adapter, BoardInfo, BoardNotFound, SourceChanged, SourceUnavailable

ADAPTERS: dict[ATS, Adapter] = {
    ATS.greenhouse: greenhouse.ADAPTER,
    ATS.lever: lever.ADAPTER,
    ATS.ashby: ashby.ADAPTER,
}

__all__ = [
    "ADAPTERS",
    "Adapter",
    "BoardInfo",
    "BoardNotFound",
    "SourceChanged",
    "SourceUnavailable",
]
