"""The interface every source kind implements."""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..errors import NotSupportedError
from ..models import (
    Account,
    Capabilities,
    Category,
    Channel,
    Episode,
    Movie,
    Programme,
    Series,
    StreamRef,
)


class BaseProvider:
    """Default implementations raise, so a partial source fails loudly and typed.

    ``capabilities`` lets the UI hide what a source cannot do instead of offering a
    menu entry that errors when opened.
    """

    kind = "base"
    capabilities = Capabilities()

    def account(self) -> Account:
        raise NotSupportedError("This source has no account information.")

    def categories(self, kind: str) -> List[Category]:
        raise NotSupportedError("This source has no categories.")

    def channels(self, category_id: Optional[str] = None) -> List[Channel]:
        raise NotSupportedError("This source has no live channels.")

    def movies(self, category_id: Optional[str] = None) -> List[Movie]:
        raise NotSupportedError("This source has no movies.")

    def movie_info(self, movie_id: str) -> dict:
        raise NotSupportedError("This source has no movie details.")

    def series(self, category_id: Optional[str] = None) -> List[Series]:
        raise NotSupportedError("This source has no series.")

    def series_info(self, series_id: str) -> Tuple[Series, List[Episode]]:
        raise NotSupportedError("This source has no series details.")

    def short_epg(self, channel: Channel, limit: int = 2) -> List[Programme]:
        raise NotSupportedError("This source has no EPG endpoint.")

    def live_stream(self, channel: Channel) -> StreamRef:
        raise NotSupportedError("This source cannot play live channels.")

    def movie_stream(self, movie: Movie) -> StreamRef:
        raise NotSupportedError("This source cannot play movies.")

    def episode_stream(self, episode: Episode) -> StreamRef:
        raise NotSupportedError("This source cannot play episodes.")
