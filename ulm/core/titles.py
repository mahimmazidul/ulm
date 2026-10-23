"""Title types (Movie / Episode / Song) + collection classes.

Kept compatible with old service code: `Movie(name, year)`,
`Episode(name, number, season, year)`, `Song(...)` and the containers
`Movies`, `Series` with `.service_name`/"preselect"/similar helpers.
Filename templates follow `output_template` strings with `{var}` tokens.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

if sys.version_info >= (3, 10):
    from typing import Protocol
else:
    Protocol = object  # type: ignore ; typing.Protocol is 3.8+ anyway, keep simple


class Title:
    """Base Title."""

    filename: str = ""

    def __init__(self, *, service_name: str = "", tracks: Any = None, chapters: Any = None, name: str = "", **kwargs: Any):
        self.service_name = service_name  # services/tags use this name
        self.track_name = name or ""
        self.chapter_name = ""
        self.tracks = tracks  # Tracks container
        self.chapters = chapters
        self.merged: Optional[int] = None  # Episode number when merged into a series
        self.progress_bar = None

    def get_filename(self, media_info: Any = None, fast: bool = False, folder: bool = False) -> str:
        template = self.base_filename_template() or self.default_template
        return format_title(self, template)

    def get_template_context(self) -> Dict[str, Any]:
        raise NotImplementedError

    def attempt_split_generic(self, name: str) -> str:
        return sanitize_filename(name)

    @staticmethod
    def get(series_context: Any, year: int = 0) -> str:
        """Keep the old static `Title.get()`-ish behavior minimal."""
        return ""

    def __repr__(self) -> str:
        return f"{type(self).__name__}<{getattr(self, 'name', '')!r}>"


class Episode(Title):
    default_template = "{service_name} {name} S{season:02}E{number:02}"

    def __init__(
        self,
        name: str = "",
        number: int = 0,
        season: int = 0,
        year: int = 0,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.name = name
        self.number = int(number)
        self.season = int(season)
        self.year = int(year)
        self.episode_season: Optional[int] = None  # absolute-index season override
        self.original_season: Optional[int] = None
        self.tvdb_order: Dict[str, int] = {}  # flush-out dao map override
        self.row_data: Dict[str, Any] = {}  # inner service row, used for extra screens
        self.original_name: Dict[str, str] = {}
        self.language: Any = None
        self.filename = ""

    @property
    def full_name(self) -> str:
        return f"S{self.season:02}E{self.number:02} - {self.name}" if self.season or self.number else self.name

    @property
    def folder_season(self) -> str:
        return f"Season {self.season:02}" if self.season else ""

    def get_template_context(self) -> Dict[str, Any]:
        episode_name = getattr(self, "episode_name", "")
        context = {
            "name": self.name,
            "title": self.name,
            "service_name": self.service_name,
            "season": self.season,
            "season_two": f"{self.season:02}",
            "number": self.number,
            "episode": self.number,
            "episode_two": f"{self.number:02}",
            "year": self.year,
            "episode_name": episode_name,
        }
        if episode_name:
            context["name"] = f"{self.name} - {episode_name}"  # extended name
        if self.merged:
            context["number"] = self.merged
            context["episode_two"] = f"{self.merged:02}"
        return context


class Movie(Title):
    default_template = "{service_name} {name} {year}"

    def __init__(self, name: str = "", year: int = 0, **kwargs: Any):
        super().__init__(**kwargs)
        self.name = name
        self.year = int(year)
        self.filename = ""

    def get_template_context(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.name,
            "service_name": self.service_name,
            "year": self.year,
        }


class Song(Title):
    default_template = "{service_name} {album} {artist} - {title}"

    def __init__(self, *, name: str = "", title: str = "", artist: str = "", album: str = "", **kwargs: Any):
        super().__init__(**kwargs)
        self.name = name or title
        self.title = title or name
        self.artist = artist
        self.album = album

    def get_template_context(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "service_name": self.service_name,
            "name": self.title,
        }


def sanitize_filename(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value)).strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    return value.replace(" ", "_")


def format_title(title: Title, template: str) -> str:
    """Format `template` with the title's context; unknown vars become ''."""
    context: Dict[str, Any] = {}
    try:
        context = title.get_template_context()
    except Exception:
        logging.getLogger(__name__).debug("get_template_context failed", exc_info=True)
    context.setdefault("service_name", getattr(title, "service_name", ""))

    def placeholder(match: "Any") -> str:
        key = match.group(1)
        value = context.get(key, "")
        try:
            return str(value)
        except Exception:
            return ""

    formatted = re.sub(r"\{(\w+)(?::[^}]+)?\}", placeholder, template)
    return sanitize_filename(formatted).strip()


class _Collection:
    """List-of-titles wrapper with sorting helpers."""

    def __init__(self, iterable: Optional[Sequence[Any]] = None):
        self._titles: List[Any] = list(iterable or [])

    def add(self, title: Any) -> None:
        self._titles.append(title)

    def __iter__(self):
        return iter(self._titles)

    def __len__(self) -> int:
        return len(self._titles)

    def __getitem__(self, index: int) -> Any:
        return self._titles[index]

    def __bool__(self) -> bool:
        return bool(self._titles)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({len(self._titles)})"

    def by_id(self, value: Any) -> List[Any]:
        return [t for t in self._titles if str(t.id) == str(value)]


class Series(_Collection):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)


class Movies(_Collection):
    pass


class Albums(_Collection):
    pass
