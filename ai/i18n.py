"""
ai/i18n.py
Internationalization foundation for Artifex360.

Provides a simple t(key, **kwargs) function for translatable strings.
Loads translations from JSON files in config/locales/.
"""
import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

_LOCALES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "locales",
)

_translations: dict[str, dict[str, str]] = {}
_translations_lock = threading.Lock()
_loaded_languages: set[str] = set()
_current_language: str = "en"


class _SafeFormatDict(dict):
    """Dict subclass that returns the placeholder for missing keys."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _load_locale(lang: str) -> dict[str, str]:
    """Load translations for a language from its JSON file."""
    path = os.path.join(_LOCALES_DIR, f"{lang}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load locale '%s': %s", lang, exc)
    return {}


def _ensure_language_loaded(lang: str) -> None:
    """Thread-safe lazy loading of a language's translations."""
    if lang in _loaded_languages:
        return
    with _translations_lock:
        if lang in _loaded_languages:  # double-check locking
            return
        _translations[lang] = _load_locale(lang)
        _loaded_languages.add(lang)


def set_language(lang: str) -> None:
    """Set the current language."""
    global _current_language
    _current_language = lang
    _ensure_language_loaded(lang)


def get_language() -> str:
    """Get the current language code."""
    return _current_language


def t(key: str, **kwargs) -> str:
    """Translate a key to the current language.

    Falls back to the key itself if no translation is found.
    Supports {placeholder} substitution via kwargs.
    Only simple scalar types (str, int, float, bool) are allowed in kwargs
    to prevent format string injection attacks.

    Args:
        key: Translation key (e.g., "error.not_found")
        **kwargs: Substitution values

    Returns:
        Translated string or the key if not found
    """
    _ensure_language_loaded(_current_language)

    translations = _translations.get(_current_language, {})
    text = translations.get(key, key)

    if kwargs:
        # Only allow simple scalar types to prevent format string injection
        safe_kwargs = {
            k: v for k, v in kwargs.items()
            if isinstance(v, (str, int, float, bool))
        }
        try:
            text = text.format_map(_SafeFormatDict(safe_kwargs))
        except (ValueError, IndexError):
            pass  # Return unformatted text rather than crashing

    return text


def available_languages() -> list[str]:
    """List available language codes based on locale files."""
    if not os.path.exists(_LOCALES_DIR):
        return ["en"]
    langs = []
    for fname in os.listdir(_LOCALES_DIR):
        if fname.endswith(".json"):
            langs.append(fname[:-5])
    return sorted(langs) if langs else ["en"]
