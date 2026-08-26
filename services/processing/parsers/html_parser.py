"""HTML parser.

Generic: it makes no assumption about what kind of document it is reading. The
same parser handles articles, technical documentation, reports, web pages, and
regulatory filings. It knows about HTML constructs only - headings, paragraphs,
lists, tables, code, quotes, figures, captions - never about domain concepts.

Two generic behaviours are worth knowing about, both driven by real-world HTML
rather than by any particular source:

* **Typographic heading fallback.** Plenty of generated HTML carries no
  ``<h1>``-``<h6>`` at all and expresses headings purely through styling. When
  a document has no native heading tags, short fully-emphasized blocks are
  treated as headings and distinct font sizes rank into levels. Native heading
  tags, when present, win outright and disable the fallback entirely.
* **Namespaced inline markup.** XML-namespaced elements embedded in HTML
  (``<ix:...>``, ``<xbrli:...>``, and any other prefixed vocabulary) come in
  two kinds. Containers whose local name marks them as metadata - ``hidden``,
  ``header``, ``references``, ``resources``, ``metadata`` - hold machine-
  readable data and are dropped. Everything else is unwrapped so the visible
  text it surrounds survives.

Domain-specific interpretation - mapping headings onto a known document
taxonomy, for instance - is deliberately out of scope here and belongs in a
separate versioned component.

The parser is pure: bytes in, CanonicalDocument out. No filesystem, no
database, no knowledge of chunking, embeddings, retrieval, or models.
"""

from __future__ import annotations

import re
import unicodedata
import warnings

from bs4 import BeautifulSoup, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning

from services.processing.base import DocumentParser, ProcessingError, register_parser
from services.processing.canonical import (
    CanonicalDocument,
    Node,
    NodeType,
    ProcessorInfo,
    SourceInfo,
    Table,
)
from services.processing.detection import DocumentFormat

# Markup that never carries document content.
NOISE_TAGS = frozenset(
    {
        "script", "style", "noscript", "iframe", "object", "embed", "svg",
        "canvas", "form", "input", "button", "select", "textarea",
        "nav", "header", "footer", "aside", "menu",
    }
)

# Local names that mark a namespaced element as a metadata container rather
# than visible content. Applied to any prefix, not to a specific vocabulary.
NAMESPACED_METADATA_NAMES = frozenset(
    {"hidden", "header", "references", "resources", "metadata"}
)

BLOCK_TAGS = frozenset(
    {
        "address", "article", "blockquote", "div", "dl", "fieldset", "figcaption",
        "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "main", "ol",
        "p", "pre", "section", "table", "ul",
    }
)

HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

LIST_TAGS = frozenset({"ul", "ol"})

# A styled heading is short. Anything longer is a paragraph that happens to be
# emphasized.
MAX_HEURISTIC_HEADING_CHARS = 250

_FONT_WEIGHT_RE = re.compile(r"font-weight\s*:\s*([a-z0-9]+)", re.I)
_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*([0-9.]+)\s*(pt|px|em|rem)?", re.I)
_DISPLAY_NONE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_WS_RE = re.compile(r"\s+")
_LANG_CLASS_RE = re.compile(r"(?:language|lang|highlight)[-_]([a-z0-9+#]+)", re.I)


def normalize_text(value: str) -> str:
    """Collapse whitespace and fold unicode compatibility forms."""
    if not value:
        return ""
    # NFKC folds non-breaking spaces, ligatures, and full-width forms.
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("​", "").replace("﻿", "")
    # All whitespace collapses to single spaces: newlines and indentation in
    # HTML source are insignificant, and structure is carried by the node tree
    # rather than by characters inside the text.
    value = _WS_RE.sub(" ", value)
    return value.strip()


def _style(tag: Tag) -> str:
    if not getattr(tag, "attrs", None):
        return ""
    style = tag.get("style")
    return style if isinstance(style, str) else ""


def _is_hidden(tag: Tag) -> bool:
    if getattr(tag, "attrs", None) and tag.has_attr("hidden"):
        return True
    return bool(_DISPLAY_NONE_RE.search(_style(tag)))


def _font_weight(tag: Tag) -> int:
    """Numeric font weight from a style attribute; 400 when unspecified."""
    match = _FONT_WEIGHT_RE.search(_style(tag))
    if not match:
        return 400
    raw = match.group(1).lower()
    if raw in ("bold", "bolder"):
        return 700
    if raw in ("normal", "lighter"):
        return 400
    try:
        return int(raw)
    except ValueError:
        return 400


def _font_size(tag: Tag) -> float | None:
    """Font size in points, normalizing px/em to a comparable scale."""
    match = _FONT_SIZE_RE.search(_style(tag))
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    unit = (match.group(2) or "pt").lower()
    if unit == "px":
        return value * 0.75
    if unit in ("em", "rem"):
        return value * 12.0
    return value


def _is_live(tag: Tag) -> bool:
    """False once a tag has been removed from the tree.

    find_all() materializes a list up front, so destroying a container also
    invalidates entries later in that list. Touching one raises, so every pass
    has to re-check.
    """
    return not getattr(tag, "decomposed", False) and tag.parent is not None


def _is_namespaced_metadata(name: str) -> bool:
    """True for a namespaced element whose local name marks it as metadata."""
    if ":" not in name:
        return False
    return name.rsplit(":", 1)[-1] in NAMESPACED_METADATA_NAMES


def strip_noise(soup: BeautifulSoup) -> int:
    """Remove markup that carries no document content. Returns count removed.

    Two passes, because the operations differ. Noise containers are destroyed
    along with everything inside them; namespaced wrappers around visible text
    are unwrapped so the text survives. Doing both in one pass would mean
    unwrapping tags a previous decompose already destroyed.
    """
    removed = 0

    # Pass 1: destroy noise containers and their entire subtree.
    for tag in list(soup.find_all(True)):
        if not _is_live(tag):
            continue
        name = (tag.name or "").lower()

        if name in NOISE_TAGS or _is_namespaced_metadata(name) or _is_hidden(tag):
            tag.decompose()
            removed += 1

    # Pass 2: unwrap surviving namespaced wrappers, keeping their text.
    for tag in list(soup.find_all(True)):
        if not _is_live(tag):
            continue
        if ":" in (tag.name or ""):
            tag.unwrap()
            removed += 1

    for comment in list(soup.find_all(string=lambda s: isinstance(s, Comment))):
        comment.extract()
        removed += 1

    return removed


def _direct_text(tag: Tag) -> str:
    return normalize_text(tag.get_text(" ", strip=True))


def _has_block_child(tag: Tag) -> bool:
    return any(
        isinstance(child, Tag) and (child.name or "").lower() in BLOCK_TAGS
        for child in tag.descendants
    )


def _is_emphasized(tag: Tag) -> bool:
    """True when every text-bearing part of this element is emphasized.

    Used only as a fallback when the document has no real heading tags.
    """
    found_text = False

    for node in tag.descendants:
        if not isinstance(node, NavigableString):
            continue
        if not node.strip():
            continue
        found_text = True

        emphasized = False
        for ancestor in node.parents:
            if not isinstance(ancestor, Tag):
                continue
            if (ancestor.name or "").lower() in ("b", "strong", "th"):
                emphasized = True
                break
            if _font_weight(ancestor) >= 600:
                emphasized = True
                break
            if ancestor is tag:
                break
        if not emphasized:
            return False

    return found_text


def _max_font_size(tag: Tag) -> float | None:
    sizes = [
        s
        for s in (_font_size(d) for d in tag.find_all(True) if isinstance(d, Tag))
        if s is not None
    ]
    own = _font_size(tag)
    if own is not None:
        sizes.append(own)
    return max(sizes) if sizes else None


def _code_language(tag: Tag) -> str | None:
    """Language hint from a class such as `language-python` or `highlight-sql`."""
    for candidate in (tag, tag.find("code")):
        if not isinstance(candidate, Tag) or not getattr(candidate, "attrs", None):
            continue
        classes = candidate.get("class") or []
        if isinstance(classes, str):
            classes = [classes]
        for value in classes:
            match = _LANG_CLASS_RE.match(str(value))
            if match:
                return match.group(1).lower()
    return None


def parse_table(tag: Tag) -> Table | None:
    """Extract a table as rows and columns.

    Rows and columns that are empty everywhere are dropped: HTML tables are
    routinely used for layout with spacer cells, and keeping them buries the
    data. `colspan` is expanded so column alignment survives.
    """
    rows: list[list[str]] = []
    header: list[str] = []
    saw_header_row = False

    for tr in tag.find_all("tr"):
        cells: list[str] = []
        is_header_row = True

        for cell in tr.find_all(["td", "th"], recursive=True):
            if cell.find_parent("table") is not tag:
                continue  # belongs to a nested table
            cells.append(_direct_text(cell))

            if (cell.name or "").lower() != "th":
                is_header_row = False

            try:
                colspan = int(cell.get("colspan", 1))
            except (TypeError, ValueError):
                colspan = 1
            cells.extend([""] * max(0, colspan - 1))

        if not cells:
            continue

        if is_header_row and not saw_header_row and not rows:
            header = cells
            saw_header_row = True
        else:
            rows.append(cells)

    if not rows and not header:
        return None

    width = max((len(r) for r in rows + ([header] if header else [])), default=0)
    padded = [r + [""] * (width - len(r)) for r in rows]
    padded_header = header + [""] * (width - len(header)) if header else []

    keep = [
        i
        for i in range(width)
        if any(r[i] for r in padded) or (padded_header and padded_header[i])
    ]
    if keep and len(keep) < width:
        padded = [[r[i] for i in keep] for r in padded]
        if padded_header:
            padded_header = [padded_header[i] for i in keep]

    padded = [r for r in padded if any(c for c in r)]

    if not padded and not padded_header:
        return None

    caption_tag = tag.find("caption")
    caption = _direct_text(caption_tag) if caption_tag else None

    return Table(rows=padded, header=padded_header, caption=caption)


class _Element:
    """Intermediate flat element produced by the walk.

    A staging type: heading levels cannot be assigned until the whole document
    has been seen, so nodes are only built afterwards.
    """

    __slots__ = ("kind", "text", "table", "children", "font_size", "level", "language")

    def __init__(
        self,
        kind: str,
        text: str = "",
        table: Table | None = None,
        children: list[str] | None = None,
        font_size: float | None = None,
        level: int | None = None,
        language: str | None = None,
    ) -> None:
        self.kind = kind
        self.text = text
        self.table = table
        self.children = children or []
        self.font_size = font_size
        self.level = level
        self.language = language


class HtmlParser(DocumentParser):
    name = "html"
    #: 2.0.0 emits generic canonical nodes (schema 2.0) instead of the
    #: section-only tree of 1.x, and additionally extracts code, quotes,
    #: figures, and captions.
    version = "2.0.0"
    supported_formats = frozenset({DocumentFormat.HTML})

    def parse(self, source: SourceInfo, raw: bytes) -> CanonicalDocument:
        if not raw:
            raise ProcessingError(f"Empty file: {source.path}")

        try:
            with warnings.catch_warnings():
                # XHTML opens with an XML declaration but is HTML. The HTML
                # tree builder is deliberate: it is lenient about malformed
                # markup where the XML parser refuses the document outright.
                warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
                soup = BeautifulSoup(raw, "lxml")
        except Exception as exc:  # pragma: no cover - lxml rarely fails outright
            raise ProcessingError(f"HTML parse failed for {source.path}: {exc}") from exc

        doc_metadata = self._document_metadata(soup)
        strip_noise(soup)

        elements = self._walk(soup.body or soup)

        if not elements:
            raise ProcessingError(
                f"No extractable content in {source.path} "
                "(document parsed but produced no text, tables, or lists)"
            )

        self._assign_heading_levels(elements)
        content = self._build_tree(elements)

        return CanonicalDocument(
            source=source,
            processor=ProcessorInfo(name=self.name, version=self.version),
            metadata=doc_metadata,
            content=content,
        )

    # -- metadata ----------------------------------------------------------

    def _document_metadata(self, soup: BeautifulSoup) -> dict:
        """Source metadata retained from the document itself."""
        metadata: dict = {}

        if soup.title and soup.title.string:
            metadata["html_title"] = normalize_text(str(soup.title.string))

        html_tag = soup.find("html")
        if isinstance(html_tag, Tag) and html_tag.get("lang"):
            metadata["language"] = html_tag.get("lang")

        meta: dict[str, str] = {}
        for tag in soup.find_all("meta"):
            key = tag.get("name") or tag.get("property")
            content = tag.get("content")
            if key and content:
                meta[str(key)] = normalize_text(str(content))
        if meta:
            metadata["html_meta"] = meta

        return metadata

    # -- walk --------------------------------------------------------------

    def _walk(self, root: Tag) -> list[_Element]:
        elements: list[_Element] = []
        self._walk_into(root, elements)
        return elements

    def _walk_into(self, tag: Tag, out: list[_Element]) -> None:
        for child in tag.children:
            if isinstance(child, NavigableString):
                text = normalize_text(str(child))
                if len(text) > 1:
                    out.append(_Element("paragraph", text=text))
                continue

            if not isinstance(child, Tag):
                continue

            name = (child.name or "").lower()

            if name in HEADING_TAGS:
                text = _direct_text(child)
                if text:
                    out.append(_Element("heading", text=text, level=HEADING_TAGS[name]))
                continue

            if name == "table":
                table = parse_table(child)
                if table is not None:
                    out.append(_Element("table", table=table))
                continue

            if name in LIST_TAGS:
                items = [
                    _direct_text(li)
                    for li in child.find_all("li", recursive=False)
                    if _direct_text(li)
                ]
                if items:
                    out.append(_Element("list", children=items))
                continue

            if name == "pre":
                # Preformatted text keeps its line structure; normalizing it
                # away would destroy the thing that makes it code.
                text = child.get_text("", strip=False).strip("\n")
                if text.strip():
                    out.append(
                        _Element("code", text=text, language=_code_language(child))
                    )
                continue

            if name == "blockquote":
                text = _direct_text(child)
                if text:
                    out.append(_Element("quote", text=text))
                continue

            if name == "figcaption":
                text = _direct_text(child)
                if text:
                    out.append(_Element("caption", text=text))
                continue

            if name in ("hr", "br"):
                continue

            if _has_block_child(child):
                self._walk_into(child, out)
                continue

            text = _direct_text(child)
            if not text:
                continue

            if _is_emphasized(child) and len(text) <= MAX_HEURISTIC_HEADING_CHARS:
                out.append(
                    _Element("styled-heading", text=text, font_size=_max_font_size(child))
                )
            else:
                out.append(_Element("paragraph", text=text))

    # -- heading levels ----------------------------------------------------

    def _assign_heading_levels(self, elements: list[_Element]) -> None:
        """Promote styled-heading candidates to headings, with levels.

        Native <h*> tags win outright. The typography fallback engages only
        when the document has none.
        """
        if any(e.kind == "heading" for e in elements):
            for element in elements:
                if element.kind == "styled-heading":
                    element.kind = "paragraph"
            return

        candidates = [e for e in elements if e.kind == "styled-heading"]
        if not candidates:
            return

        # Rank distinct font sizes: larger type means a higher-level heading.
        sizes = sorted({e.font_size for e in candidates if e.font_size}, reverse=True)
        size_to_level = {size: min(i + 1, 6) for i, size in enumerate(sizes)}

        for element in candidates:
            element.kind = "heading"
            element.level = size_to_level.get(element.font_size or 0.0, 1)

    # -- node tree ---------------------------------------------------------

    def _build_tree(self, elements: list[_Element]) -> list[Node]:
        """Fold the flat element stream into canonical nodes.

        When the document has headings, they group the content that follows
        into nested SECTION nodes. When it has none, content nodes are emitted
        flat at the document root - a technical page that is simply a run of
        paragraphs, code, and tables should not be forced into a synthetic
        section that carries no meaning.
        """
        counter = 0

        def next_id() -> str:
            nonlocal counter
            counter += 1
            return f"n-{counter:04d}"

        def content_node(element: _Element, ordinal: int) -> Node:
            if element.kind == "table":
                return Node(
                    id=next_id(), type=NodeType.TABLE, table=element.table, ordinal=ordinal
                )
            if element.kind == "list":
                node = Node(id=next_id(), type=NodeType.LIST, ordinal=ordinal)
                node.children = [
                    Node(id=next_id(), type=NodeType.LIST_ITEM, text=item, ordinal=i)
                    for i, item in enumerate(element.children)
                ]
                return node
            if element.kind == "code":
                return Node(
                    id=next_id(),
                    type=NodeType.CODE,
                    text=element.text,
                    ordinal=ordinal,
                    attributes=({"language": element.language} if element.language else {}),
                )
            if element.kind == "quote":
                return Node(
                    id=next_id(), type=NodeType.QUOTE, text=element.text, ordinal=ordinal
                )
            if element.kind == "caption":
                return Node(
                    id=next_id(), type=NodeType.CAPTION, text=element.text, ordinal=ordinal
                )
            return Node(
                id=next_id(), type=NodeType.PARAGRAPH, text=element.text, ordinal=ordinal
            )

        has_headings = any(e.kind == "heading" for e in elements)

        if not has_headings:
            return [content_node(e, i) for i, e in enumerate(elements)]

        roots: list[Node] = []
        # (node, level) stack; the document root is level 0 and is not a node.
        stack: list[tuple[Node, int]] = []
        ordinal = 0

        def append_to_current(node: Node) -> None:
            if stack:
                stack[-1][0].children.append(node)
            else:
                roots.append(node)

        for element in elements:
            if element.kind == "heading":
                level = element.level or 1
                while stack and stack[-1][1] >= level:
                    stack.pop()

                path = [n.text for n, _ in stack if n.text]
                section = Node(
                    id=next_id(),
                    type=NodeType.SECTION,
                    text=element.text,
                    ordinal=len(stack[-1][0].children) if stack else len(roots),
                    attributes={"level": level, "path": path},
                )
                append_to_current(section)
                stack.append((section, level))
                ordinal = 0
                continue

            append_to_current(content_node(element, ordinal))
            ordinal += 1

        return roots


register_parser(HtmlParser())
