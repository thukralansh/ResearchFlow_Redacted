"""Non-executing Python AST extraction with exact immutable line provenance."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtractedRepositoryComponent:
    component_type: str
    symbol: str | None
    line_start: int
    line_end: int
    content_excerpt: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class PythonExtractionResult:
    components: tuple[ExtractedRepositoryComponent, ...]
    warning: str | None = None


def extract_python_components(content: bytes) -> PythonExtractionResult:
    text = content.decode("utf-8")
    lines = text.splitlines(keepends=True)
    file_component = _component("file", None, 1, max(len(lines), 1), text)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return PythonExtractionResult((file_component,), "python_ast_syntax_error")
    found: list[ExtractedRepositoryComponent] = [file_component]
    parents: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _visit_symbol(
            self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ) -> None:
            parents.append(node.name)
            start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            end = node.end_lineno or node.lineno
            excerpt = "".join(lines[start - 1 : end])
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            found.append(_component(kind, ".".join(parents), start, end, excerpt))
            self.generic_visit(node)
            parents.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._visit_symbol(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_symbol(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_symbol(node)

    Visitor().visit(tree)
    return PythonExtractionResult(tuple(found))


def extract_file_component(content: bytes) -> PythonExtractionResult:
    text = content.decode("utf-8")
    return PythonExtractionResult(
        (_component("file", None, 1, max(text.count("\n") + 1, 1), text),)
    )


def _component(
    component_type: str,
    symbol: str | None,
    line_start: int,
    line_end: int,
    excerpt: str,
) -> ExtractedRepositoryComponent:
    return ExtractedRepositoryComponent(
        component_type=component_type,
        symbol=symbol,
        line_start=line_start,
        line_end=line_end,
        content_excerpt=excerpt,
        content_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
    )
