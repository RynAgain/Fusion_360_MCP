"""Tests for ai/document_extractor.py"""
import pytest
from pathlib import Path
from ai.document_extractor import extract_text, get_supported_extensions, SUPPORTED_ALL


class TestExtractText:
    def test_text_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Line 1\nLine 2\nLine 3")
        result = extract_text(str(f))
        assert result["content"] == "Line 1\nLine 2\nLine 3"
        assert result["total_lines"] == 3
        assert result["was_truncated"] is False

    def test_markdown_file(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Hello\n\nWorld")
        result = extract_text(str(f))
        assert "Hello" in result["content"]

    def test_csv_file(self, tmp_path):
        f = tmp_path / "test.csv"
        f.write_text("a,b,c\n1,2,3")
        result = extract_text(str(f))
        assert "a,b,c" in result["content"]

    def test_truncation(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"Line {i}" for i in range(5000)))
        result = extract_text(str(f), max_lines=100)
        assert result["was_truncated"] is True
        assert result["returned_lines"] == 100
        assert result["total_lines"] == 5000

    def test_file_not_found(self):
        result = extract_text("/nonexistent/file.txt")
        assert "error" in result

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "test.xyz"
        f.write_text("data")
        result = extract_text(str(f))
        assert "error" in result
        assert "unsupported" in result["error"].lower()

    def test_image_file(self, tmp_path):
        # Create a minimal PNG (1x1 pixel)
        import base64
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        f = tmp_path / "test.png"
        f.write_bytes(png_data)
        result = extract_text(str(f))
        assert result.get("is_image") is True
        assert result.get("base64_data")
        assert result["media_type"] == "image/png"

    def test_file_too_large(self, tmp_path):
        f = tmp_path / "huge.txt"
        # Test the check logic with a mock
        from unittest.mock import patch
        with patch("ai.document_extractor.MAX_FILE_SIZE_MB", 0.001):
            f.write_text("x" * 2000)
            result = extract_text(str(f))
            assert "error" in result
            assert "too large" in result["error"].lower()

    def test_json_file(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')
        result = extract_text(str(f))
        assert result["content"] == '{"key": "value"}'
        assert result["file_type"] == ".json"

    def test_yaml_file(self, tmp_path):
        f = tmp_path / "config.yaml"
        f.write_text("key: value\nlist:\n  - item1")
        result = extract_text(str(f))
        assert "key: value" in result["content"]
        assert result["file_type"] == ".yaml"


class TestSupportedExtensions:
    def test_returns_categories(self):
        exts = get_supported_extensions()
        assert "text" in exts
        assert "documents" in exts
        assert "images" in exts
        assert ".pdf" in exts["documents"]
        assert ".png" in exts["images"]
        assert ".txt" in exts["text"]

    def test_pdf_in_supported(self):
        assert ".pdf" in SUPPORTED_ALL
        assert ".docx" in SUPPORTED_ALL
