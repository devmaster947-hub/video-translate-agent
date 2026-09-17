"""One registry for Whisper, TTS provider and subtitle language codes."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    whisper: str
    minimax: str
    boost: str
    subtitle: str


_ROWS = [
    ("zh-Hans", "zh", "zh", "Chinese", "zho"),
    ("zh-Hant", "zh", "zh", "Chinese", "zho"),
    ("en", "en", "en", "English", "eng"),
    ("ja", "ja", "ja", "Japanese", "jpn"),
    ("ko", "ko", "ko", "Korean", "kor"),
    ("fr", "fr", "fr", "French", "fra"),
    ("de", "de", "de", "German", "deu"),
    ("es", "es", "es", "Spanish", "spa"),
    ("pt", "pt", "pt", "Portuguese", "por"),
    ("it", "it", "it", "Italian", "ita"),
    ("ru", "ru", "ru", "Russian", "rus"),
    ("ar", "ar", "ar", "Arabic", "ara"),
    ("hi", "hi", "hi", "Hindi", "hin"),
    ("th", "th", "th", "Thai", "tha"),
    ("vi", "vi", "vi", "Vietnamese", "vie"),
    ("id", "id", "id", "Indonesian", "ind"),
    ("tr", "tr", "tr", "Turkish", "tur"),
    ("uk", "uk", "uk", "Ukrainian", "ukr"),
    ("nl", "nl", "nl", "Dutch", "nld"),
    ("pl", "pl", "pl", "Polish", "pol"),
]
LANGUAGES = {row[0].lower(): Language(*row) for row in _ROWS}
ALIASES = {"zh": "zh-hans", "zh-cn": "zh-hans", "zh-sg": "zh-hans",
           "zh-tw": "zh-hant", "zh-hk": "zh-hant"}


def language(value: str) -> Language:
    key = value.strip().lower().replace("_", "-")
    key = ALIASES.get(key, key)
    if key not in LANGUAGES:
        key = key.split("-")[0]
    if key not in LANGUAGES:
        raise ValueError("Unsupported language; see languages.py")
    return LANGUAGES[key]


def source_code(value: str) -> str:
    return "auto" if value.lower() == "auto" else language(value).whisper
