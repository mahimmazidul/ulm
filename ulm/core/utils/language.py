"""Minimal language tag handling — zero heavy deps.

The old core pulled `langcodes` + `language-data` + `pycountry` (tens of
MB of import-time RAM). Here we keep a small built-in table of common
languages and accept anything service code passes (`"en"`, `"en-US"`,
`None`, a name or 639-2/3 code). Comparison and filenames use tolerant
`Language` semantics so old `lang == "en"` checks keep working.
"""
from __future__ import annotations

from typing import Optional, Union

_NAME_TO_CODE = {
    "english": "en", "eng": "en", "japanese": "ja", "jpn": "ja", "korean": "ko", "kor": "ko",
    "chinese": "zh", "zho": "zh", "spanish": "es", "spa": "es", "french": "fr", "fra": "fr",
    "german": "de", "deu": "de", "italian": "it", "ita": "it", "portuguese": "pt", "por": "pt",
    "russian": "ru", "rus": "ru", "hindi": "hi", "hin": "hi", "arabic": "ar", "ara": "ar",
    "turkish": "tr", "tur": "tr", "indonesian": "id", "ind": "id", "thai": "th", "tha": "th",
    "vietnamese": "vi", "vie": "vi", "dutch": "nl", "nld": "nl", "polish": "pl", "pol": "pl",
    "swedish": "sv", "swe": "sv", "norwegian": "nb", "nor": "nb", "danish": "da", "dan": "da",
    "finnish": "fi", "fin": "fi", "czech": "cs", "ces": "cs", "greek": "el", "ell": "el",
    "hebrew": "he", "heb": "he", "hungarian": "hu", "hun": "hu", "romanian": "ro", "ron": "ro",
    "malay": "ms", "msa": "ms", "filipino": "tl", "fil": "tl", "ukrainian": "uk", "ukr": "uk",
    "slovak": "sk", "slo": "sk", "bulgarian": "bg", "bul": "bg", "croatian": "hr", "hrv": "hr",
    "serbian": "sr", "srp": "sr", "bengali": "bn", "ben": "bn", "tamil": "ta", "tam": "ta",
    "telugu": "te", "tel": "te", "kannada": "kn", "kan": "kn", "malayalam": "ml", "mal": "ml",
    "marathi": "mr", "mar": "mr", "punjabi": "pa", "pan": "pa", "gujarati": "gu", "guj": "gu",
    "persian": "fa", "per": "fa", "swahili": "sw", "swa": "sw", "afrikaans": "af", "afr": "af",
    "catalan": "ca", "cat": "ca", "nepali": "ne", "nep": "ne", "sinhala": "si", "sin": "si",
    "burmese": "my", "bur": "my", "khmer": "km", "khm": "km", "georgian": "ka", "geo": "ka",
    "amharic": "am", "amh": "am", "estonian": "et", "est": "et", "lithuanian": "lt", "lit": "lt",
    "latvian": "lv", "lav": "lv", "icelandic": "is", "isl": "is", "albanian": "sq", "sqi": "sq",
    "macedonian": "mk", "mkd": "mk", "armenian": "hy", "hye": "hy", "azerbaijani": "az", "aze": "az",
    "kazakh": "kk", "kaz": "kk", "uzbek": "uz", "uzb": "uz", "mongolian": "mn", "mon": "mn",
    "urdu": "ur", "urd": "ur", "kurdish": "ku", "kur": "ku", "chinese (simplified)": "zh-Hans",
    "chinese (traditional)": "zh-Hant",
}


class Language:
    """Immutable language tag with tolerant comparisons."""

    def __init__(self, value: Optional[str]) -> None:
        self._full = str(value).strip() if value is not None else ""
        text = self._full
        lowered = text.lower().split(";")[0].strip().replace("_", "-")
        parts = [p.strip() for p in lowered.split(",") if p.strip()]
        primary = parts[0] if parts else ""
        self.code: str = primary.split("-")[0].lower() if primary else ""
        if self.code in _NAME_TO_CODE:
            self.code = _NAME_TO_CODE[self.code]
        self.name = self._full or self.code

    @property
    def display_name(self) -> str:
        return self._full or self.code or "none"

    def get_display_name(self, section: str = "") -> str:
        return self.display_name

    def to_639_1(self) -> str:
        return self.code if len(self.code) == 2 else "und"

    def __str__(self) -> str:
        return self.display_name or "none"

    def __repr__(self) -> str:
        return f"<Language {self.display_name!r}>"

    def __eq__(self, other: object) -> bool:
        """Match on code, 639-2/3 name, or full tag; case-insensitive."""
        if isinstance(other, Language):
            return other.code.lower() == self.code.lower()
        if isinstance(other, str):
            other_code = str(_NAME_TO_CODE.get(other.lower(), other)).strip().lower()
            return other_code in {self.code.lower(), self._full.lower()}
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.code.lower())

    def __bool__(self) -> bool:
        return bool(self.code)


def make_language(value: Union[None, str, Language]) -> Language:
    if isinstance(value, Language):
        return value
    return Language(value)
