"""Tree-sitter wrapper: parses one file's source into CodeUnit-shaped records
(module/class/function, with file_path, line range, signature, docstring) plus
its raw import list. LAYER A — purely structural, no LLM involved (ADR-006).

Import *resolution* (turning "./bar" or "foo.bar" into a graph edge to another
CodeUnit) happens in dependency_graph.py, which has visibility across the whole
repo; this module only extracts what's literally written in each file.
"""

from dataclasses import dataclass
from pathlib import Path

import tree_sitter_javascript as tsjs
import tree_sitter_python as tsp
import tree_sitter_typescript as tsts
from tree_sitter import Language as TSLanguage
from tree_sitter import Node, Parser

from app.ingestion.language_detect import Language

_PARSERS: dict[Language, Parser] = {
    Language.python: Parser(TSLanguage(tsp.language())),
    Language.javascript: Parser(TSLanguage(tsjs.language())),
    Language.typescript: Parser(TSLanguage(tsts.language_typescript())),
}
_TSX_PARSER = Parser(TSLanguage(tsts.language_tsx()))


@dataclass(frozen=True)
class ParsedImport:
    raw: str  # module path/text as written in source, e.g. "..pkg", "./hooks", "react"
    kind: str  # "python_absolute" | "python_relative" | "js_relative" | "js_bare"
    level: int = 0  # leading-dot count, python relative imports only
    # names bound by a `from X import a, b as c` statement (source names, not
    # local aliases) — dependency_graph.py uses these to detect the common
    # `from package import submodule` idiom, which `raw` alone can't capture
    # since `submodule` isn't part of the module spec grammatically.
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedUnit:
    unit_type: str  # "module" | "class" | "function"
    name: str
    line_start: int  # 1-indexed, inclusive
    line_end: int  # 1-indexed, inclusive
    signature: str | None
    docstring: str | None


@dataclass(frozen=True)
class ParsedFile:
    relative_path: str
    units: list[ParsedUnit]
    imports: list[ParsedImport]


def parse_file(path: Path, relative_path: str, language: Language) -> ParsedFile | None:
    try:
        source = path.read_bytes()
    except OSError:
        return None
    return parse_source(source, relative_path, language)


def parse_source(source: bytes, relative_path: str, language: Language) -> ParsedFile | None:
    if not source.strip():
        return None

    parser = _TSX_PARSER if relative_path.endswith(".tsx") else _PARSERS[language]
    tree = parser.parse(source)
    root = tree.root_node

    if language is Language.python:
        units, imports = _parse_python(root, source)
        module_doc = _block_docstring(root, source)
    else:
        units, imports = _parse_javascript(root, source)
        module_doc = _leading_jsdoc(root, source)

    module_unit = ParsedUnit(
        unit_type="module",
        name=relative_path,
        line_start=1,
        line_end=source.count(b"\n") + 1,
        signature=None,
        docstring=module_doc,
    )
    return ParsedFile(relative_path=relative_path, units=[module_unit, *units], imports=imports)


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


# --- Python ---


def _parse_python(root: Node, source: bytes) -> tuple[list[ParsedUnit], list[ParsedImport]]:
    units: list[ParsedUnit] = []
    imports: list[ParsedImport] = []

    for child in root.named_children:
        if child.type == "import_statement":
            imports.extend(_python_plain_imports(child, source))
        elif child.type == "import_from_statement":
            imp = _python_from_import(child, source)
            if imp is not None:
                imports.append(imp)
        elif child.type == "class_definition":
            units.append(_python_class_unit(child, source, child))
            units.extend(_python_class_methods(child, source))
        elif child.type == "function_definition":
            units.append(_python_function_unit(child, source, qualifier=None, span_node=child))
        elif child.type == "decorated_definition":
            inner = child.child_by_field_name("definition")
            if inner is None:
                continue
            if inner.type == "class_definition":
                units.append(_python_class_unit(inner, source, child))
                units.extend(_python_class_methods(inner, source))
            elif inner.type == "function_definition":
                units.append(_python_function_unit(inner, source, qualifier=None, span_node=child))

    return units, imports


def _python_class_methods(class_node: Node, source: bytes) -> list[ParsedUnit]:
    body = class_node.child_by_field_name("body")
    if body is None:
        return []
    class_name = _text(class_node.child_by_field_name("name"), source)

    methods: list[ParsedUnit] = []
    for child in body.named_children:
        if child.type == "function_definition":
            methods.append(_python_function_unit(child, source, qualifier=class_name, span_node=child))
        elif child.type == "decorated_definition":
            inner = child.child_by_field_name("definition")
            if inner is not None and inner.type == "function_definition":
                methods.append(_python_function_unit(inner, source, qualifier=class_name, span_node=child))
    return methods


def _python_class_unit(class_node: Node, source: bytes, span_node: Node) -> ParsedUnit:
    body = class_node.child_by_field_name("body")
    header_end = body.start_byte if body is not None else class_node.end_byte
    signature = _text_range(span_node.start_byte, header_end, source).rstrip().rstrip(":").rstrip()
    return ParsedUnit(
        unit_type="class",
        name=_text(class_node.child_by_field_name("name"), source),
        line_start=span_node.start_point.row + 1,
        line_end=class_node.end_point.row + 1,
        signature=signature,
        docstring=_block_docstring(body, source),
    )


def _python_function_unit(fn_node: Node, source: bytes, qualifier: str | None, span_node: Node) -> ParsedUnit:
    name = _text(fn_node.child_by_field_name("name"), source)
    if qualifier:
        name = f"{qualifier}.{name}"
    body = fn_node.child_by_field_name("body")
    header_end = body.start_byte if body is not None else fn_node.end_byte
    signature = _text_range(span_node.start_byte, header_end, source).rstrip().rstrip(":").rstrip()
    return ParsedUnit(
        unit_type="function",
        name=name,
        line_start=span_node.start_point.row + 1,
        line_end=fn_node.end_point.row + 1,
        signature=signature,
        docstring=_block_docstring(body, source),
    )


def _text_range(start: int, end: int, source: bytes) -> str:
    return source[start:end].decode("utf-8", errors="replace")


def _block_docstring(block_node: Node | None, source: bytes) -> str | None:
    if block_node is None or not block_node.named_children:
        return None
    first = block_node.named_children[0]
    if first.type != "expression_statement" or not first.named_children:
        return None
    maybe_string = first.named_children[0]
    if maybe_string.type != "string":
        return None
    return _python_string_content(maybe_string, source)


def _python_string_content(string_node: Node, source: bytes) -> str | None:
    parts = [_text(c, source) for c in string_node.named_children if c.type == "string_content"]
    text = "".join(parts).strip()
    return text or None


def _python_plain_imports(node: Node, source: bytes) -> list[ParsedImport]:
    imports: list[ParsedImport] = []
    for item in node.named_children:
        if item.type == "dotted_name":
            imports.append(ParsedImport(raw=_text(item, source), kind="python_absolute"))
        elif item.type == "aliased_import" and item.named_children:
            dotted = item.named_children[0]
            imports.append(ParsedImport(raw=_text(dotted, source), kind="python_absolute"))
    return imports


def _python_from_import(node: Node, source: bytes) -> ParsedImport | None:
    named = node.named_children
    if not named:
        return None
    spec, name_nodes = named[0], named[1:]
    names = _python_imported_names(name_nodes, source)

    if spec.type == "relative_import":
        text = _text(spec, source)
        level = len(text) - len(text.lstrip("."))
        module = text.lstrip(".") or None
        return ParsedImport(raw=module or ("." * level), kind="python_relative", level=level, names=names)
    if spec.type == "dotted_name":
        return ParsedImport(raw=_text(spec, source), kind="python_absolute", names=names)
    return None


def _python_imported_names(name_nodes: list[Node], source: bytes) -> tuple[str, ...]:
    names: list[str] = []
    for item in name_nodes:
        if item.type == "dotted_name":
            names.append(_text(item, source))
        elif item.type == "aliased_import" and item.named_children:
            names.append(_text(item.named_children[0], source))  # source name, not the local alias
    return tuple(names)


# --- JavaScript / TypeScript ---


def _parse_javascript(root: Node, source: bytes) -> tuple[list[ParsedUnit], list[ParsedImport]]:
    units: list[ParsedUnit] = []
    imports: list[ParsedImport] = []

    for child in root.named_children:
        _js_top_level(child, source, units, imports, top_level_node=child)

    # import_statement is top-level-only by spec, but require(...) can appear
    # anywhere (inside functions, conditionally, etc.) — scan the whole tree.
    imports.extend(_find_js_requires(root, source))

    return units, imports


def _js_top_level(
    node: Node, source: bytes, units: list[ParsedUnit], imports: list[ParsedImport], top_level_node: Node
) -> None:
    if node.type == "import_statement":
        imp = _js_import(node, source)
        if imp is not None:
            imports.append(imp)
    elif node.type == "export_statement":
        for child in node.named_children:
            _js_top_level(child, source, units, imports, top_level_node=node)
    elif node.type == "function_declaration":
        units.append(_js_function_unit(node, source, qualifier=None, span_node=top_level_node))
    elif node.type == "class_declaration":
        units.append(_js_class_unit(node, source, span_node=top_level_node))
        units.extend(_js_class_methods(node, source))
    elif node.type in ("lexical_declaration", "variable_declaration"):
        units.extend(_js_declared_functions(node, source, span_node=top_level_node))


def _js_declared_functions(decl_node: Node, source: bytes, span_node: Node) -> list[ParsedUnit]:
    units: list[ParsedUnit] = []
    for declarator in decl_node.named_children:
        if declarator.type != "variable_declarator":
            continue
        name_node = declarator.child_by_field_name("name")
        value_node = declarator.child_by_field_name("value")
        if name_node is None or value_node is None:
            continue
        if value_node.type in ("arrow_function", "function_expression"):
            units.append(
                _js_function_unit(
                    value_node, source, qualifier=None, span_node=span_node, name_override=_text(name_node, source)
                )
            )
    return units


def _js_class_methods(class_node: Node, source: bytes) -> list[ParsedUnit]:
    body = class_node.child_by_field_name("body")
    if body is None:
        return []
    class_name = _text(class_node.child_by_field_name("name"), source)

    methods: list[ParsedUnit] = []
    for child in body.named_children:
        if child.type == "method_definition":
            methods.append(_js_function_unit(child, source, qualifier=class_name, span_node=child))
    return methods


def _js_class_unit(class_node: Node, source: bytes, span_node: Node) -> ParsedUnit:
    body = class_node.child_by_field_name("body")
    header_end = body.start_byte if body is not None else class_node.end_byte
    signature = _text_range(span_node.start_byte, header_end, source).rstrip()
    name_node = class_node.child_by_field_name("name")
    return ParsedUnit(
        unit_type="class",
        name=_text(name_node, source) if name_node is not None else "<anonymous>",
        line_start=span_node.start_point.row + 1,
        line_end=class_node.end_point.row + 1,
        signature=signature,
        docstring=_leading_jsdoc(span_node, source),
    )


def _js_function_unit(
    fn_node: Node, source: bytes, qualifier: str | None, span_node: Node, name_override: str | None = None
) -> ParsedUnit:
    if name_override is not None:
        name = name_override
    else:
        name_node = fn_node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else "<anonymous>"
    if qualifier:
        name = f"{qualifier}.{name}"

    body = fn_node.child_by_field_name("body")
    header_end = body.start_byte if body is not None else fn_node.end_byte
    signature = _text_range(span_node.start_byte, header_end, source).rstrip()

    return ParsedUnit(
        unit_type="function",
        name=name,
        line_start=span_node.start_point.row + 1,
        line_end=fn_node.end_point.row + 1,
        signature=signature,
        docstring=_leading_jsdoc(span_node, source),
    )


def _leading_jsdoc(node: Node, source: bytes) -> str | None:
    prev = node.prev_sibling
    if prev is None or prev.type != "comment":
        return None
    raw = _text(prev, source).strip()
    if not raw.startswith("/**"):
        return None
    inner = raw[3:]
    inner = inner.removesuffix("*/")
    lines = [line.strip().lstrip("*").strip() for line in inner.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned or None


def _js_import(node: Node, source: bytes) -> ParsedImport | None:
    string_node = next((c for c in node.children if c.type == "string"), None)
    if string_node is None:
        return None
    raw = _js_string_value(string_node, source)
    kind = "js_relative" if raw.startswith(".") else "js_bare"
    return ParsedImport(raw=raw, kind=kind)


def _js_string_value(string_node: Node, source: bytes) -> str:
    frag = next((c for c in string_node.named_children if c.type == "string_fragment"), None)
    if frag is not None:
        return _text(frag, source)
    return _text(string_node, source).strip("\"'`")


def _find_js_requires(node: Node, source: bytes) -> list[ParsedImport]:
    results: list[ParsedImport] = []
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "identifier" and _text(fn, source) == "require":
            args = node.child_by_field_name("arguments")
            str_args = [c for c in args.named_children if c.type == "string"] if args is not None else []
            if len(str_args) == 1:
                raw = _js_string_value(str_args[0], source)
                kind = "js_relative" if raw.startswith(".") else "js_bare"
                results.append(ParsedImport(raw=raw, kind=kind))
    for child in node.children:
        results.extend(_find_js_requires(child, source))
    return results
