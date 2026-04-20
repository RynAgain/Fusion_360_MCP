"""
ai/folded_context.py
Generate folded (signature-only) file summaries for context condensation.

When conversations are condensed, full file contents can be replaced with
signature-only summaries showing class/function declarations without bodies.
This preserves structural awareness at ~10% of the token cost.
"""
import ast
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Maximum total characters for folded content
DEFAULT_MAX_CHARACTERS = 50_000


@dataclass
class FoldedFileResult:
    """Result of folding a single file."""
    path: str
    content: str
    lines_original: int
    lines_folded: int
    success: bool
    error: str | None = None


@dataclass
class FoldedContextResult:
    """Result of generating folded context for multiple files."""
    content: str
    files_processed: int
    files_skipped: int
    character_count: int
    sections: list[str]


def _format_function_sig(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_name: str | None = None,
    indent: str = "",
) -> str:
    """Format a function/method node into a signature string.

    Args:
        node: The AST function node.
        class_name: If provided, qualifies the name as class_name.method_name.
        indent: Whitespace prefix for indentation.
    """
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    args = ast.unparse(node.args) if node.args else ""
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    qualified_name = f"{class_name}.{node.name}" if class_name else node.name
    docstring = ""
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        first_line = node.body[0].value.value.split("\n")[0].strip()
        if first_line:
            docstring = f'{indent}    """{first_line}..."""\n'
    return f"{indent}{prefix}def {qualified_name}({args}){returns}:\n{docstring}{indent}    ...\n"


def fold_python_file(source: str, file_path: str = "<unknown>") -> FoldedFileResult:
    """Extract signatures from a Python source file using AST.

    Returns class declarations, function signatures, and top-level assignments
    without implementation bodies.
    """
    original_lines = source.count("\n") + 1
    signatures = []

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return FoldedFileResult(
            path=file_path, content=f"# Parse error: {exc}",
            lines_original=original_lines, lines_folded=1,
            success=False, error=str(exc),
        )

    for node in tree.body:  # top-level only to avoid duplicates
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            signatures.append(_format_function_sig(node))
        elif isinstance(node, ast.ClassDef):
            decorators = "".join(
                f"@{ast.dump(d) if not hasattr(d, 'id') else d.id}\n"
                for d in node.decorator_list
            ) if node.decorator_list else ""
            bases = ", ".join(ast.unparse(b) for b in node.bases) if node.bases else ""
            base_str = f"({bases})" if bases else ""
            # Get docstring if present
            docstring = ""
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                first_line = node.body[0].value.value.split("\n")[0].strip()
                if first_line:
                    docstring = f'    """{first_line}..."""\n'
            signatures.append(f"{decorators}class {node.name}{base_str}:\n{docstring}    ...\n")
            # Walk class body to collect methods with qualified names
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    signatures.append(_format_function_sig(item, class_name=node.name, indent="  "))

    if not signatures:
        return FoldedFileResult(
            path=file_path, content="# No classes or functions found",
            lines_original=original_lines, lines_folded=1,
            success=True, error=None,
        )

    folded = "\n".join(signatures)
    folded_lines = folded.count("\n") + 1

    return FoldedFileResult(
        path=file_path, content=folded,
        lines_original=original_lines, lines_folded=folded_lines,
        success=True, error=None,
    )


def generate_folded_context(
    file_paths: list[str],
    max_characters: int = DEFAULT_MAX_CHARACTERS,
) -> FoldedContextResult:
    """Generate folded context for a list of files.

    Only Python files are supported via AST parsing. Other files are skipped.

    Args:
        file_paths: List of file paths to process
        max_characters: Maximum total characters for output

    Returns:
        FoldedContextResult with combined content and statistics
    """
    total_files = len(file_paths)
    sections = []
    total_chars = 0
    processed = 0

    for fpath in file_paths:
        if total_chars >= max_characters:
            break

        # Only process Python files
        if not fpath.endswith(".py"):
            continue

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                source = f.read()
        except OSError:
            continue

        result = fold_python_file(source, fpath)
        if not result.success:
            continue

        section = f"## {fpath}\n{result.content}"

        if total_chars + len(section) > max_characters:
            break

        sections.append(section)
        total_chars += len(section)
        processed += 1

    skipped = total_files - processed
    content = "\n---\n".join(sections)

    return FoldedContextResult(
        content=content,
        files_processed=processed,
        files_skipped=skipped,
        character_count=len(content),
        sections=sections,
    )
