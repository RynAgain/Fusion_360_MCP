"""
tests/test_i18n.py
Tests for ai/i18n.py -- internationalization foundation.
"""
import json
import os
import threading
import pytest
from unittest.mock import patch

import ai.i18n as i18n_mod
from ai.i18n import t, set_language, get_language, available_languages, _load_locale


@pytest.fixture(autouse=True)
def reset_i18n_state():
    """Reset module-level state between tests."""
    original_lang = i18n_mod._current_language
    original_translations = dict(i18n_mod._translations)
    original_loaded = set(i18n_mod._loaded_languages)
    yield
    i18n_mod._current_language = original_lang
    i18n_mod._translations.clear()
    i18n_mod._translations.update(original_translations)
    i18n_mod._loaded_languages.clear()
    i18n_mod._loaded_languages.update(original_loaded)


class TestTranslation:
    """Tests for the t() translation function."""

    def test_returns_translation_for_known_key(self, tmp_path):
        """t() returns the translated string for a known key."""
        locale_dir = tmp_path / "locales"
        locale_dir.mkdir()
        (locale_dir / "en.json").write_text(
            json.dumps({"greeting": "Hello"}), encoding="utf-8",
        )

        with patch.object(i18n_mod, "_LOCALES_DIR", str(locale_dir)):
            i18n_mod._translations.clear()
            set_language("en")
            assert t("greeting") == "Hello"

    def test_returns_key_for_unknown_key(self):
        """t() returns the key itself when no translation is found."""
        i18n_mod._translations["en"] = {}
        set_language("en")
        assert t("nonexistent.key") == "nonexistent.key"

    def test_substitutes_kwargs(self, tmp_path):
        """t() replaces {placeholder} tokens with kwargs."""
        locale_dir = tmp_path / "locales"
        locale_dir.mkdir()
        (locale_dir / "en.json").write_text(
            json.dumps({"msg": "Hello, {name}!"}), encoding="utf-8",
        )

        with patch.object(i18n_mod, "_LOCALES_DIR", str(locale_dir)):
            i18n_mod._translations.clear()
            set_language("en")
            assert t("msg", name="World") == "Hello, World!"

    def test_handles_missing_kwargs_gracefully(self, tmp_path):
        """t() returns unformatted text when kwargs are missing."""
        locale_dir = tmp_path / "locales"
        locale_dir.mkdir()
        (locale_dir / "en.json").write_text(
            json.dumps({"msg": "Hello, {name}!"}), encoding="utf-8",
        )

        with patch.object(i18n_mod, "_LOCALES_DIR", str(locale_dir)):
            i18n_mod._translations.clear()
            i18n_mod._loaded_languages.clear()
            set_language("en")
            # Should not raise -- returns the unformatted string
            result = t("msg")
            assert "{name}" in result

    def test_wrong_kwargs_leave_placeholder(self, tmp_path):
        """t() leaves {name} as-is when wrong kwargs are provided."""
        locale_dir = tmp_path / "locales"
        locale_dir.mkdir()
        (locale_dir / "en.json").write_text(
            json.dumps({"msg": "Hello, {name}!"}), encoding="utf-8",
        )

        with patch.object(i18n_mod, "_LOCALES_DIR", str(locale_dir)):
            i18n_mod._translations.clear()
            i18n_mod._loaded_languages.clear()
            set_language("en")
            result = t("msg", wrong="val")
            assert "{name}" in result

    def test_blocks_non_scalar_kwargs(self, tmp_path):
        """t() strips kwargs with non-scalar types (object attribute access blocked)."""
        locale_dir = tmp_path / "locales"
        locale_dir.mkdir()
        (locale_dir / "en.json").write_text(
            json.dumps({"msg": "Value: {val}"}), encoding="utf-8",
        )

        with patch.object(i18n_mod, "_LOCALES_DIR", str(locale_dir)):
            i18n_mod._translations.clear()
            i18n_mod._loaded_languages.clear()
            set_language("en")
            # Pass a list (non-scalar) -- should be stripped, leaving placeholder
            result = t("msg", val=[1, 2, 3])
            assert "{val}" in result

            # Pass a dict (non-scalar) -- should be stripped
            result = t("msg", val={"a": 1})
            assert "{val}" in result

            # Pass an object -- should be stripped
            result = t("msg", val=object())
            assert "{val}" in result

    def test_allows_scalar_types(self, tmp_path):
        """t() allows str, int, float, bool as kwargs."""
        locale_dir = tmp_path / "locales"
        locale_dir.mkdir()
        (locale_dir / "en.json").write_text(
            json.dumps({"msg": "{s} {i} {f} {b}"}), encoding="utf-8",
        )

        with patch.object(i18n_mod, "_LOCALES_DIR", str(locale_dir)):
            i18n_mod._translations.clear()
            i18n_mod._loaded_languages.clear()
            set_language("en")
            result = t("msg", s="hello", i=42, f=3.14, b=True)
            assert result == "hello 42 3.14 True"


class TestLanguageManagement:
    """Tests for language setting/getting."""

    def test_set_language_changes_active_language(self):
        """set_language() updates the current language."""
        set_language("fr")
        assert get_language() == "fr"

    def test_get_language_returns_current(self):
        """get_language() reflects the last set_language() call."""
        set_language("de")
        assert get_language() == "de"
        set_language("en")
        assert get_language() == "en"


class TestAvailableLanguages:
    """Tests for the available_languages() function."""

    def test_lists_locale_files(self, tmp_path):
        """available_languages() returns codes from JSON files in locales dir."""
        locale_dir = tmp_path / "locales"
        locale_dir.mkdir()
        (locale_dir / "en.json").write_text("{}", encoding="utf-8")
        (locale_dir / "fr.json").write_text("{}", encoding="utf-8")
        (locale_dir / "de.json").write_text("{}", encoding="utf-8")
        (locale_dir / "README.md").write_text("not a locale", encoding="utf-8")

        with patch.object(i18n_mod, "_LOCALES_DIR", str(locale_dir)):
            langs = available_languages()

        assert langs == ["de", "en", "fr"]

    def test_returns_en_when_no_locales_dir(self, tmp_path):
        """available_languages() returns ['en'] when the locales directory is missing."""
        with patch.object(i18n_mod, "_LOCALES_DIR", str(tmp_path / "nonexistent")):
            langs = available_languages()

        assert langs == ["en"]


class TestLoadLocale:
    """Tests for the _load_locale helper."""

    def test_loading_nonexistent_locale_returns_empty(self, tmp_path):
        """_load_locale returns {} for a language with no file."""
        with patch.object(i18n_mod, "_LOCALES_DIR", str(tmp_path)):
            result = _load_locale("zz")
        assert result == {}

    def test_loading_invalid_json_returns_empty(self, tmp_path):
        """_load_locale returns {} for a malformed JSON file."""
        locale_dir = tmp_path
        (locale_dir / "bad.json").write_text("{invalid json", encoding="utf-8")

        with patch.object(i18n_mod, "_LOCALES_DIR", str(locale_dir)):
            result = _load_locale("bad")
        assert result == {}


class TestThreadSafety:
    """Tests for concurrent access to t()."""

    def test_concurrent_t_calls_all_return_valid_results(self, tmp_path):
        """Multiple threads calling t() concurrently all get valid results."""
        locale_dir = tmp_path / "locales"
        locale_dir.mkdir()
        (locale_dir / "en.json").write_text(
            json.dumps({"greeting": "Hello, {name}!"}), encoding="utf-8",
        )

        results = []
        errors = []

        def worker(thread_id):
            try:
                with patch.object(i18n_mod, "_LOCALES_DIR", str(locale_dir)):
                    i18n_mod._translations.clear()
                    i18n_mod._loaded_languages.clear()
                    for _ in range(20):
                        result = t("greeting", name=f"Thread{thread_id}")
                        results.append(result)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"Thread errors: {errors}"
        # All results should be valid translations (not empty or None)
        for r in results:
            assert isinstance(r, str)
            assert len(r) > 0
