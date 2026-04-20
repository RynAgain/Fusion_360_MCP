"""
tests/test_folded_context.py
Tests for the folded context generation module.
"""
import os

import pytest

from ai.folded_context import (
    fold_python_file,
    generate_folded_context,
    FoldedFileResult,
    FoldedContextResult,
)


class TestFoldPythonFile:
    """Tests for fold_python_file."""

    def test_extracts_function_signatures(self):
        """fold_python_file extracts function signatures without bodies."""
        source = '''\
def greet(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

def add(a: int, b: int) -> int:
    return a + b
'''
        result = fold_python_file(source, "test.py")
        assert result.success is True
        assert "def greet(name: str) -> str:" in result.content
        assert "def add(a: int, b: int) -> int:" in result.content
        assert "return" not in result.content
        assert result.lines_folded <= result.lines_original

    def test_extracts_class_definitions(self):
        """fold_python_file extracts class signatures with base classes."""
        source = '''\
class Animal:
    """A base animal."""
    def speak(self):
        return "..."

class Dog(Animal):
    """A dog."""
    def speak(self):
        return "Woof!"
'''
        result = fold_python_file(source, "models.py")
        assert result.success is True
        assert "class Animal:" in result.content
        assert "class Dog(Animal):" in result.content
        assert '"Woof!"' not in result.content

    def test_handles_async_functions(self):
        """fold_python_file handles async function definitions."""
        source = '''\
async def fetch_data(url: str) -> dict:
    """Fetch data from URL."""
    async with aiohttp.get(url) as resp:
        return await resp.json()
'''
        result = fold_python_file(source, "async_mod.py")
        assert result.success is True
        assert "async def fetch_data(url: str) -> dict:" in result.content

    def test_handles_return_type_annotations(self):
        """fold_python_file preserves return type annotations."""
        source = '''\
def process(data: list[int]) -> dict[str, float]:
    return {}
'''
        result = fold_python_file(source, "typed.py")
        assert result.success is True
        assert "-> dict[str, float]" in result.content

    def test_handles_syntax_errors(self):
        """fold_python_file handles syntax errors gracefully."""
        source = "def broken(:\n    pass"
        result = fold_python_file(source, "broken.py")
        assert result.success is False
        assert result.error is not None
        assert "Parse error" in result.content

    def test_handles_empty_files(self):
        """fold_python_file handles files with no classes or functions."""
        source = "# Just a comment\nX = 42\n"
        result = fold_python_file(source, "constants.py")
        assert result.success is True
        assert "No classes or functions" in result.content

    def test_extracts_docstring_first_lines(self):
        """fold_python_file extracts the first line of docstrings."""
        source = '''\
def complex_func(x: int) -> int:
    """Compute something complex.
    
    This is a longer description that should not appear.
    """
    return x * 2
'''
        result = fold_python_file(source, "documented.py")
        assert result.success is True
        assert "Compute something complex" in result.content
        assert "longer description" not in result.content

    def test_extracts_class_docstring(self):
        """fold_python_file extracts class docstrings."""
        source = '''\
class MyService:
    """Service for managing things.
    
    Extended description here.
    """
    def run(self):
        pass
'''
        result = fold_python_file(source, "service.py")
        assert result.success is True
        assert "Service for managing things" in result.content
        assert "Extended description" not in result.content

    def test_empty_source_string(self):
        """fold_python_file handles completely empty source."""
        result = fold_python_file("", "empty.py")
        assert result.success is True
        assert result.lines_original == 1

    def test_no_duplicate_signatures_for_class_methods(self):
        """TASK-188: Methods inside classes must not appear as standalone entries."""
        source = '''\
class Foo:
    def bar(self):
        pass

    def baz(self, x: int) -> int:
        return x
'''
        result = fold_python_file(source, "dup_test.py")
        assert result.success is True
        content = result.content
        # Methods should appear only as qualified names (Foo.bar, Foo.baz)
        assert "Foo.bar" in content
        assert "Foo.baz" in content
        # Count occurrences: "def" + "bar" should appear exactly once
        assert content.count("bar") == 1
        assert content.count("baz") == 1

    def test_class_methods_have_qualified_names(self):
        """TASK-188: Class methods should be prefixed with class name."""
        source = '''\
class MyService:
    def start(self):
        pass

    async def stop(self):
        pass

def standalone():
    pass
'''
        result = fold_python_file(source, "qualified.py")
        assert result.success is True
        assert "MyService.start" in result.content
        assert "MyService.stop" in result.content
        # standalone should NOT be qualified
        assert "def standalone()" in result.content
        assert "MyService.standalone" not in result.content

    def test_docstrings_with_ast_constant(self):
        """TASK-207: Docstrings (ast.Constant nodes) are extracted correctly."""
        source = '''\
def documented(x: int) -> int:
    """Process the input value."""
    return x * 2

class Documented:
    """A documented class."""
    def method(self):
        """Method docstring."""
        pass
'''
        result = fold_python_file(source, "docstrings.py")
        assert result.success is True
        assert "Process the input value" in result.content
        assert "A documented class" in result.content
        assert "Method docstring" in result.content


class TestGenerateFoldedContext:
    """Tests for generate_folded_context."""

    def test_processes_multiple_files(self, tmp_path):
        """generate_folded_context processes multiple Python files."""
        f1 = tmp_path / "a.py"
        f1.write_text("def foo(): pass\n", encoding="utf-8")
        f2 = tmp_path / "b.py"
        f2.write_text("class Bar:\n    pass\n", encoding="utf-8")

        result = generate_folded_context([str(f1), str(f2)])
        assert result.files_processed == 2
        assert result.files_skipped == 0
        assert len(result.sections) == 2
        assert "def foo" in result.content
        assert "class Bar" in result.content

    def test_respects_max_characters(self, tmp_path):
        """generate_folded_context stops processing when max_characters reached."""
        files = []
        for i in range(20):
            f = tmp_path / f"mod_{i}.py"
            f.write_text(f"def func_{i}(): pass\n" * 10, encoding="utf-8")
            files.append(str(f))

        result = generate_folded_context(files, max_characters=200)
        assert result.files_processed < 20
        assert result.character_count <= 200

    def test_skipped_count_accurate_at_char_limit(self, tmp_path):
        """TASK-200: Skipped count reflects all unprocessed files when char limit hit."""
        files = []
        for i in range(10):
            f = tmp_path / f"mod_{i}.py"
            f.write_text(f"def func_{i}(): pass\n" * 5, encoding="utf-8")
            files.append(str(f))

        # Use a very low character limit that only allows ~1-2 files
        result = generate_folded_context(files, max_characters=100)
        # skipped = total - processed
        assert result.files_skipped == 10 - result.files_processed
        assert result.files_processed + result.files_skipped == 10

    def test_skips_non_python_files(self, tmp_path):
        """generate_folded_context skips non-Python files."""
        py_file = tmp_path / "code.py"
        py_file.write_text("def hello(): pass\n", encoding="utf-8")
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("Just notes", encoding="utf-8")
        json_file = tmp_path / "data.json"
        json_file.write_text("{}", encoding="utf-8")

        result = generate_folded_context([str(py_file), str(txt_file), str(json_file)])
        assert result.files_processed == 1
        assert result.files_skipped == 2

    def test_handles_missing_files(self, tmp_path):
        """generate_folded_context skips files that don't exist."""
        existing = tmp_path / "exists.py"
        existing.write_text("def real(): pass\n", encoding="utf-8")

        result = generate_folded_context([
            str(existing),
            str(tmp_path / "missing.py"),
        ])
        assert result.files_processed == 1
        assert result.files_skipped == 1

    def test_empty_file_list(self):
        """generate_folded_context handles empty input list."""
        result = generate_folded_context([])
        assert result.files_processed == 0
        assert result.files_skipped == 0
        assert result.content == ""
        assert result.sections == []

    def test_sections_contain_file_headers(self, tmp_path):
        """Each section starts with a file path header."""
        f = tmp_path / "module.py"
        f.write_text("def func(): pass\n", encoding="utf-8")

        result = generate_folded_context([str(f)])
        assert result.sections[0].startswith(f"## {f}")

    def test_result_character_count_matches_content(self, tmp_path):
        """character_count matches the actual content length."""
        f = tmp_path / "x.py"
        f.write_text("def x(): pass\n", encoding="utf-8")

        result = generate_folded_context([str(f)])
        assert result.character_count == len(result.content)
