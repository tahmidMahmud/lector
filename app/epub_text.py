"""EPUB -> narration plan.

Reads an EPUB, flattens it into an ordered list of text blocks, removes Project
Gutenberg boilerplate (everything outside the ``*** START OF ... ***`` /
``*** END OF ... ***`` markers), then cuts the body into chapters using the
book's table of contents (falling back to headings, spine files, or size).

Run ``python -m app.epub_text book.epub`` to print the resulting chapter plan.
"""
from __future__ import annotations

import hashlib
import posixpath
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import unquote

import warnings

from bs4 import (BeautifulSoup, CData, Comment, Declaration, Doctype, NavigableString, ProcessingInstruction,
                 Tag, XMLParsedAsHTMLWarning)

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BLOCK_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "td", "th",
    "dd", "dt", "figcaption", "caption", "section", "article", "header", "footer", "nav",
    "aside", "main", "hr", "table", "tr", "thead", "tbody", "tfoot", "ul", "ol", "dl", "body",
    "center", "address", "figure", "details", "summary", "html",
}
SKIP_TAGS = {"script", "style", "head", "title", "svg", "img", "image", "video", "audio", "math", "sup"}
_NON_TEXT = (Comment, CData, Declaration, Doctype, ProcessingInstruction)
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

GUTENBERG_START_RE = re.compile(
    r"\*\s*\*\s*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG|\*END\*\s*THE SMALL PRINT", re.I
)
GUTENBERG_END_RE = re.compile(r"\*\s*\*\s*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG", re.I)
GUTENBERG_ID_RE = re.compile(r"gutenberg\.org/(?:ebooks/|files/|cache/epub/)?(\d+)", re.I)

SKIP_TITLE_RE = re.compile(
    r"^\s*(table of )?contents?\s*$|^\s*(list of )?illustrations\s*$|^\s*index\s*$|"
    r"^\s*(the )?full project gutenberg(™|\(tm\))? license\s*$",
    re.I,
)
MIN_CHAPTER_CHARS = 250       # segments smaller than this are merged into the next one
MAX_CHAPTER_CHARS = 48_000    # longer segments are split into parts (~50 min of audio)
LINK_RATIO_SKIP = 0.6         # a section that is mostly hyperlinks is a TOC/index


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Block:
    text: str
    file_index: int
    tag: str
    ids: set[str] = field(default_factory=set)
    link_chars: int = 0
    is_heading: bool = False
    pre: bool = False


@dataclass
class Section:
    title: str
    paragraphs: list[str]
    skipped: bool = False
    skip_reason: str | None = None
    headings: list[str] = field(default_factory=list)  # headings merged in, spoken before the text

    @property
    def char_count(self) -> int:
        return sum(len(p) for p in self.paragraphs)

    def spoken_paragraphs(self) -> list[str]:
        """Paragraphs as they should be read aloud, including the chapter heading(s)."""
        out: list[str] = []
        for h in self.headings:
            out.append(heading_for_speech(h))
        norm_first = _norm(self.paragraphs[0]) if self.paragraphs else ""
        for h in self.headings:
            if norm_first and _norm(h) == norm_first:
                # The body already begins with the heading text; don't say it twice.
                return out + self.paragraphs[1:]
        return out + list(self.paragraphs)

    def text_hash(self) -> str:
        h = hashlib.sha256()
        for p in self.spoken_paragraphs():
            h.update(p.encode("utf-8"))
            h.update(b"\n")
        return h.hexdigest()


@dataclass
class BookText:
    title: str
    authors: list[str]
    language: str
    is_gutenberg: bool
    gutenberg_id: str | None
    trimmed_start: bool
    trimmed_end: bool
    sections: list[Section]

    @property
    def char_count(self) -> int:
        return sum(s.char_count for s in self.sections if not s.skipped)


# ---------------------------------------------------------------------------
# OPF / container parsing
# ---------------------------------------------------------------------------
def _read_opf(z: zipfile.ZipFile) -> tuple[str, BeautifulSoup]:
    names = set(z.namelist())
    rootfile = None
    if "META-INF/container.xml" in names:
        container = BeautifulSoup(z.read("META-INF/container.xml"), "xml")
        rootfile = container.find("rootfile")
    if rootfile is None or not rootfile.get("full-path") or rootfile["full-path"] not in names:
        # Fallback: first .opf in the archive
        for n in z.namelist():
            if n.lower().endswith(".opf"):
                return n, BeautifulSoup(z.read(n), "xml")
        raise ValueError("EPUB has no OPF package document")
    opf_path = rootfile["full-path"]
    return opf_path, BeautifulSoup(z.read(opf_path), "xml")


def _opf_metadata(opf: BeautifulSoup) -> dict:
    """Title, authors, language, description and Gutenberg id from an OPF document."""
    title = _text_of(opf.find("dc:title")) or _text_of(opf.find("title"))
    creators = opf.find_all("dc:creator") or opf.find_all("creator")
    # EPUB3 puts roles in <meta refines="#id" property="role">aut</meta>; EPUB2 in opf:role.
    refined_roles = {}
    for m in opf.find_all("meta"):
        if (m.get("property") or "") == "role" and (m.get("refines") or "").startswith("#"):
            refined_roles[m["refines"][1:]] = _text_of(m).lower()
    authors: list[str] = []
    others: list[str] = []
    for c in creators:
        name = _text_of(c)
        if not name:
            continue
        role = (c.get("opf:role") or c.get("role") or refined_roles.get(c.get("id") or "", "")).lower()
        (authors if role in ("", "aut") else others).append(name)
    authors = list(dict.fromkeys(authors or others))
    language = _text_of(opf.find("dc:language")) or _text_of(opf.find("language")) or "en"
    desc_el = opf.find("dc:description") or opf.find("description")
    description = ""
    if desc_el is not None:
        raw = desc_el.get_text(" ", strip=True)
        description = _collapse_ws(_html_soup(raw).get_text(" ")) if "<" in raw else _collapse_ws(raw)
    ident_text = " ".join(_text_of(e) for e in opf.find_all(["dc:identifier", "identifier", "dc:source", "source"]))
    gid_match = GUTENBERG_ID_RE.search(ident_text)
    return {
        "title": title, "authors": authors, "language": language, "description": description,
        "gutenberg_id": gid_match.group(1) if gid_match else None,
        "is_gutenberg": bool(gid_match) or "gutenberg" in ident_text.lower(),
    }


def read_epub_metadata(path: str) -> dict:
    """Cheap metadata read: parses only META-INF/container.xml and the OPF package document.

    Returns ``{"title", "authors", "language", "description", "gutenberg_id", "is_gutenberg"}``.
    Raises ``zipfile.BadZipFile`` / ``ValueError`` / ``OSError`` for unreadable files.
    """
    with zipfile.ZipFile(path) as z:
        _opf_path, opf = _read_opf(z)
    meta = _opf_metadata(opf)
    if not meta["title"]:
        meta["title"] = posixpath.splitext(posixpath.basename(str(path)))[0]
    return meta


def _resolve(base_dir: str, href: str) -> str:
    href = unquote(href.split("#", 1)[0])
    return posixpath.normpath(posixpath.join(base_dir, href)) if base_dir else posixpath.normpath(href)


def _text_of(el: Tag | None) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""


# ---------------------------------------------------------------------------
# TOC parsing (EPUB3 nav or NCX), flattened in document order
# ---------------------------------------------------------------------------
def _parse_nav(data: bytes, nav_dir: str) -> list[tuple[str, str, str | None]]:
    soup = _html_soup(data)
    nav = None
    for n in soup.find_all("nav"):
        if (n.get("epub:type") or "").strip() == "toc":
            nav = n
            break
    if nav is None:
        nav = soup.find("nav")
    if nav is None:
        return []
    out: list[tuple[str, str, str | None]] = []
    for a in nav.find_all("a", href=True):
        href = a["href"]
        frag = unquote(href.split("#", 1)[1]) if "#" in href else None
        out.append((_text_of(a), _resolve(nav_dir, href), frag))
    return out


def _parse_ncx(data: bytes, ncx_dir: str) -> list[tuple[str, str, str | None]]:
    soup = BeautifulSoup(data, "xml")
    out: list[tuple[str, str, str | None]] = []
    for np in soup.find_all("navPoint"):
        label = np.find("navLabel")
        content = np.find("content")
        if content is None or not content.get("src"):
            continue
        src = content["src"]
        frag = unquote(src.split("#", 1)[1]) if "#" in src else None
        out.append((_text_of(label), _resolve(ncx_dir, src), frag))
    return out


# ---------------------------------------------------------------------------
# HTML -> blocks
# ---------------------------------------------------------------------------
class _BlockCollector:
    def __init__(self, file_index: int) -> None:
        self.file_index = file_index
        self.blocks: list[Block] = []
        self.pending_ids: list[str] = []

    def _emit(self, text: str, tag: str, ids: Iterable[str], link_chars: int, pre: bool) -> None:
        text = text if pre else _collapse_ws(text)
        text = text.strip()
        all_ids = set(self.pending_ids) | set(ids)
        if not text:
            # Keep anchors around so they attach to the next block with text.
            self.pending_ids = sorted(all_ids)
            return
        self.pending_ids = []
        self.blocks.append(Block(text=text, file_index=self.file_index, tag=tag, ids=all_ids,
                                 link_chars=link_chars, is_heading=tag in HEADING_TAGS, pre=pre))

    @staticmethod
    def _inline_text(nodes: list, pre: bool = False) -> tuple[str, set[str], int]:
        """Render inline content. Outside <pre>, source line breaks are plain whitespace
        (Gutenberg hard-wraps its HTML at ~72 columns); only <br> starts a new line."""
        parts: list[str] = []
        ids: set[str] = set()
        link_chars = 0

        def visit(n, in_link: bool) -> None:
            nonlocal link_chars
            if isinstance(n, NavigableString):
                if isinstance(n, _NON_TEXT) or (n.parent is not None and n.parent.name in SKIP_TAGS):
                    return
                t = str(n)
                if not pre:
                    t = t.replace("\r", " ").replace("\n", " ")
                parts.append(t)
                if in_link:
                    link_chars += len(t.strip())
            elif isinstance(n, Tag):
                if n.name in SKIP_TAGS:
                    return
                if n.get("id"):
                    ids.add(n["id"])
                if n.name == "br":
                    parts.append("\n")
                    return
                for c in n.children:
                    visit(c, in_link or n.name == "a")

        for n in nodes:
            visit(n, False)
        return "".join(parts), ids, link_chars

    def walk(self, el: Tag) -> None:
        if el.name in SKIP_TAGS:
            return
        if el.get("id"):
            self.pending_ids.append(el["id"])
        children = list(el.children)
        has_block_child = any(isinstance(c, Tag) and c.name in BLOCK_TAGS for c in children)
        if not has_block_child:
            # Leaf block: everything inside is inline.
            pre = el.name == "pre"
            text, ids, link_chars = self._inline_text(children, pre)
            if pre:
                # Keep line structure in <pre>; each line becomes part of the block.
                text = "\n".join(l.rstrip() for l in text.splitlines() if l.strip())
            self._emit(text, el.name, ids, link_chars, pre)
            return
        run: list = []
        for c in children:
            if isinstance(c, Tag) and c.name in BLOCK_TAGS:
                if run:
                    text, ids, link_chars = self._inline_text(run)
                    self._emit(text, el.name, ids, link_chars, False)
                    run = []
                self.walk(c)
            else:
                run.append(c)
        if run:
            text, ids, link_chars = self._inline_text(run)
            self._emit(text, el.name, ids, link_chars, False)


def _blocks_from_xhtml(data: bytes, file_index: int) -> list[Block]:
    soup = _html_soup(data)
    body = soup.find("body") or soup
    col = _BlockCollector(file_index)
    col.walk(body)
    return col.blocks


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
_WS_RE = re.compile(r"[ \t\r\f\v ]+")
_NL_RE = re.compile(r"\s*\n\s*")


def _html_soup(data: bytes | str) -> BeautifulSoup:
    """Parse (X)HTML leniently with lxml, without the XML-as-HTML warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        return BeautifulSoup(data, "lxml")


def _collapse_ws(s: str) -> str:
    s = _NL_RE.sub("\n", s)
    s = _WS_RE.sub(" ", s)
    return "\n".join(line.strip() for line in s.split("\n") if line.strip())


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


_ROMAN_RE = re.compile(r"\b(CHAPTER|BOOK|PART|VOLUME|CANTO|LETTER|SECTION|ACT|SCENE|STAVE)\s+([IVXLCDM]+)\b(\.?)", re.I)
_ROMAN_ONLY_RE = re.compile(r"^\s*([IVXLCDM]+)\s*\.?\s*$")


def roman_to_int(s: str) -> int | None:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    s = s.upper()
    if not s or any(ch not in vals for ch in s):
        return None
    total = 0
    for i, ch in enumerate(s):
        v = vals[ch]
        if i + 1 < len(s) and vals[s[i + 1]] > v:
            total -= v
        else:
            total += v
    return total


def int_to_roman(n: int) -> str:
    out = []
    for v, sym in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
                   (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


def is_roman(s: str) -> bool:
    """True only for canonical numerals ("XIV"), so words like "DID" or "CIVIL" don't count."""
    n = roman_to_int(s)
    return bool(n) and 0 < n < 4000 and int_to_roman(n) == s.upper()


_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "nor",
                "from", "with", "into", "upon", "via", "vs"}
_EDGE_PUNCT = ".,;:!?()[]{}\"'“”‘’«»—–-"


def smart_title(s: str) -> str:
    """Turn ALL-CAPS headings into Title Case (keeps roman numerals and initials)."""
    letters = [c for c in s if c.isalpha()]
    if not letters or not all(c.isupper() for c in letters):
        return s

    def fix(word: str, first: bool) -> str:
        bare = word.strip(_EDGE_PUNCT)
        if len(bare) > 1 and is_roman(bare):
            return word
        core = word.lower()
        if re.fullmatch(r"(?:[^\w]*)(?:[a-z]\.)+(?:[^\w]*)", core):
            return core.upper()  # initials, e.g. "A." in "Edgar A. Poe", "U.S."
        if not first and bare.lower() in _SMALL_WORDS:
            return core
        core = re.sub(r"[^\W\d_]", lambda m: m.group(0).upper(), core, count=1)       # first letter
        core = re.sub(r"([-—–/])([^\W\d_])", lambda m: m.group(1) + m.group(2).upper(), core)  # Wine-Shop
        core = re.sub(r"^([^\w]*[OD]['’])([a-z])", lambda m: m.group(1) + m.group(2).upper(), core)  # O'Brien
        return core

    words = s.split(" ")
    out = []
    for i, w in enumerate(words):
        prev = out[-1] if out else ""
        # A word after "III." / a colon / a dash starts a new phrase, so it is capitalised too.
        out.append(fix(w, i == 0 or prev.endswith((":", "—", "–", ".", "!", "?")) or prev in ("—", "–", "-")))
    return " ".join(out)


_FOOTNOTE_RE = re.compile(r"\s*(\[\s*\d+\s*\]|\(\*\d+\)|\{\d+\})")
_BYLINE_RE = re.compile(r"^\s*by\b", re.I)


def _roman_heading_sub(m: re.Match) -> str:
    num = m.group(2)
    if not num.isupper() or not is_roman(num):
        return m.group(0)
    return f"{m.group(1).capitalize()} {roman_to_int(num)}{m.group(3)}"


def heading_for_speech(title: str) -> str:
    """"CHAPTER IV. The Preparation" -> "Chapter 4. The Preparation." ; "II" -> "2."."""
    t = _collapse_ws(_FOOTNOTE_RE.sub("", title)).replace("\n", " ").strip()
    t = smart_title(t)
    t = _ROMAN_RE.sub(_roman_heading_sub, t)
    m = _ROMAN_ONLY_RE.match(t)
    if m and m.group(1).isupper() and is_roman(m.group(1)):
        t = f"{roman_to_int(m.group(1))}."
    t = t.rstrip()
    if t and t[-1] not in ".!?:":
        t += "."
    return t


_TRAILING_ABBREV_RE = re.compile(r"(?:\b[A-Za-z]|\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|Co|No|Vol|Esq|Bros|etc))\.$")


def display_title(title: str) -> str:
    """Title for menus/chapter lists: footnote markers removed, ALL CAPS -> Title Case,
    a lone trailing period dropped ("The Black Cat." -> "The Black Cat")."""
    t = _collapse_ws(_FOOTNOTE_RE.sub("", title)).replace("\n", " ").strip()
    t = smart_title(t)
    if t.endswith(".") and not t.endswith("..") and not _TRAILING_ABBREV_RE.search(t):
        t = t[:-1].rstrip()
    return t or "Untitled"


_ATOMIC = {
    "Đ": "D", "đ": "d", "Ð": "D", "ð": "d", "Þ": "Th", "þ": "th", "Æ": "AE", "æ": "ae",
    "Œ": "OE", "œ": "oe", "ß": "ss", "Ø": "O", "ø": "o", "Ł": "L", "ł": "l",
}


def clean_for_tts(text: str, language: str = "en") -> str:
    """Normalise a paragraph for the TTS engine."""
    t = text.replace(" ", " ").replace("­", "").replace("​", "").replace("﻿", "")
    t = re.sub(r"\[\s*\d+\s*\]|\(\*\d+\)|\{\d+\}", "", t)          # footnote markers
    t = re.sub(r"(?<![A-Za-z0-9])_([^_\n]{1,200}?)_(?![A-Za-z0-9])", r"\1", t)  # _italics_
    t = t.replace("…", "...")
    t = t.replace("“", '"').replace("”", '"').replace("„", '"').replace("‘", "'").replace("’", "'")
    t = t.replace("«", '"').replace("»", '"')
    t = re.sub(r"(?<=\d)\s*[—–]\s*(?=\d)", " to ", t)              # 1840–1850
    t = re.sub(r"(?<=\w)[—–―]+(?=\w)", ", ", t)                   # word—word
    t = re.sub(r"[—–―]+", " ", t)
    t = re.sub(r"\*+", " ", t)                                     # "* * *" breaks, *emphasis*
    t = re.sub(r"&c\.", "etc.", t)
    t = re.sub(r"\s*&\s*", " and ", t)
    t = re.sub(r"\.{4,}", "...", t)
    t = re.sub(r",\s*,", ",", t)
    if language.lower().startswith("en"):
        t = "".join(_ATOMIC.get(ch, ch) for ch in t)
        t = unicodedata.normalize("NFKD", t)
        t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = _WS_RE.sub(" ", t)
    t = re.sub(r" +([,;:!?])", r"\1", t)
    t = re.sub(r"(^|\n)\s*[,;:]\s*", r"\1", t)                     # no leading comma after a removed dash
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    return t


_SENT_RE = re.compile(r"([.!?…][\"'”’\)\]]*)\s+(?=[\"'“‘(\[A-Z0-9])")
_ABBREV_END_RE = re.compile(
    r"(?:\b(?:Mr|Mrs|Ms|Messrs|Mme|Mlle|Mons|Dr|St|Rev|Col|Gen|Capt|Lt|Sgt|Prof|Hon|Jr|Sr|Bros|Gov|Sen|"
    r"Rep|Fr|vs|viz|cf|No|Nos|Vol|Ch|pp|Esq)|(?<![A-Za-z])[A-Z])\.$"
)


def split_sentences(line: str) -> list[str]:
    """Split a line into sentences, not breaking after "Mr." or initials like "A."."""
    out: list[str] = []
    start = 0
    for m in _SENT_RE.finditer(line):
        head = line[start:m.end(1)]
        if _ABBREV_END_RE.search(head):
            continue
        if head.strip():
            out.append(head.strip())
        start = m.end()
    tail = line[start:].strip()
    if tail:
        out.append(tail)
    return out


def _hard_split(s: str, max_chars: int) -> list[str]:
    """Split one overlong sentence at clause breaks, else at word boundaries; never > max_chars."""
    pieces: list[str] = []
    while len(s) > max_chars:
        window = s[:max_chars + 1]          # a space right at max_chars is a fine cut point
        cut = -1
        for sep in (", ", "; ", ": ", " - ", ") "):
            i = window.rfind(sep)
            if i >= max_chars // 2:
                cut = max(cut, i + 1)       # keep the punctuation with the left piece
        if cut <= 0:
            i = window.rfind(" ")
            cut = i if i > 0 else max_chars  # no space at all: a hard cut is unavoidable
        piece = s[:cut].strip()
        if piece:
            pieces.append(piece)
        s = s[cut:].strip()
    if s:
        pieces.append(s)
    return pieces


def chunk_for_tts(paragraphs: list[str], max_chars: int = 380) -> list[str]:
    """Pack sentences into chunks of at most ``max_chars``.

    A chunk never spans two paragraphs (or two ``\n``-separated lines, e.g. verse),
    and sentences are only split - at a clause or word boundary - when they are
    longer than ``max_chars`` on their own.
    """
    chunks: list[str] = []
    for para in paragraphs:
        for line in para.split("\n"):
            line = line.strip()
            if not line:
                continue
            cur = ""
            for sent in split_sentences(line):
                for s in _hard_split(sent.strip(), max_chars):
                    if cur and len(cur) + 1 + len(s) > max_chars:
                        chunks.append(cur)
                        cur = s
                    else:
                        cur = f"{cur} {s}".strip()
            if cur:
                chunks.append(cur)
    return chunks


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------
def extract_book(path: str) -> BookText:
    with zipfile.ZipFile(path) as z:
        opf_path, opf = _read_opf(z)
        opf_dir = posixpath.dirname(opf_path)
        names = set(z.namelist())

        # Metadata
        meta = _opf_metadata(opf)
        title = meta["title"] or posixpath.basename(str(path))
        authors = meta["authors"]
        language = meta["language"]
        gutenberg_id = meta["gutenberg_id"]
        is_gutenberg = meta["is_gutenberg"]

        # Manifest + spine
        manifest: dict[str, tuple[str, str, str]] = {}
        nav_href = None
        ncx_href = None
        for item in opf.find_all("item"):
            iid, href, mt = item.get("id"), item.get("href"), item.get("media-type", "")
            if not iid or not href:
                continue
            full = _resolve(opf_dir, href)
            manifest[iid] = (full, mt, item.get("properties", "") or "")
            if "nav" in (item.get("properties") or "").split():
                nav_href = full
            if mt == "application/x-dtbncx+xml":
                ncx_href = full
        spine_el = opf.find("spine")
        spine: list[str] = []
        if spine_el is not None:
            if spine_el.get("toc") and spine_el["toc"] in manifest and ncx_href is None:
                ncx_href = manifest[spine_el["toc"]][0]
            for ref in spine_el.find_all("itemref"):
                iid = ref.get("idref")
                if iid in manifest and (ref.get("linear", "yes") or "yes").lower() != "no":
                    full, mt, _props = manifest[iid]
                    if ("html" in mt or full.lower().endswith((".xhtml", ".html", ".htm"))) and full in names:
                        spine.append(full)
        if not spine:
            spine = sorted(n for n in names if n.lower().endswith((".xhtml", ".html", ".htm")))

        # TOC
        toc: list[tuple[str, str, str | None]] = []
        if nav_href and nav_href in names:
            toc = _parse_nav(z.read(nav_href), posixpath.dirname(nav_href))
        if not toc and ncx_href and ncx_href in names:
            toc = _parse_ncx(z.read(ncx_href), posixpath.dirname(ncx_href))

        # Blocks
        blocks: list[Block] = []
        file_first_block: dict[int, int] = {}
        for fi, href in enumerate(spine):
            if nav_href and href == nav_href and len(spine) > 1:
                continue  # the nav document itself is not narration
            fb = _blocks_from_xhtml(z.read(href), fi)
            if fb:
                file_first_block[fi] = len(blocks)
                blocks.extend(fb)

    # Gutenberg trimming -----------------------------------------------------
    trimmed_start = trimmed_end = False
    start_idx = None
    end_idx = None
    for i, b in enumerate(blocks):
        if start_idx is None and GUTENBERG_START_RE.search(b.text):
            start_idx = i
        elif GUTENBERG_END_RE.search(b.text):
            end_idx = i
            break
    if start_idx is not None or end_idx is not None:
        is_gutenberg = True
    lo = (start_idx + 1) if start_idx is not None else 0
    hi = end_idx if end_idx is not None else len(blocks)
    if start_idx is not None:
        trimmed_start = True
    if end_idx is not None:
        trimmed_end = True
    if lo >= hi:  # degenerate markers; keep everything
        lo, hi = 0, len(blocks)
        trimmed_start = trimmed_end = False
    keep = blocks[lo:hi]
    # Re-index block positions for TOC lookup
    offset = lo
    file_first_block = {fi: max(0, idx - offset) for fi, idx in file_first_block.items()
                        if idx < hi}
    for fi in list(file_first_block):
        if file_first_block[fi] >= len(keep):
            del file_first_block[fi]

    # Chapter starts from TOC ------------------------------------------------
    spine_index = {href: i for i, href in enumerate(spine)}
    starts: list[tuple[int, str]] = []
    id_lookup: dict[tuple[int, str], int] = {}
    for bi, b in enumerate(keep):
        for an in b.ids:
            id_lookup.setdefault((b.file_index, an), bi)
    for label, href, frag in toc:
        fi = spine_index.get(href)
        if fi is None:
            continue
        bi = None
        if frag is not None:
            bi = id_lookup.get((fi, frag))
        if bi is None:
            bi = file_first_block.get(fi)
            if bi is None:
                # The target file is entirely in the trimmed region, or has no text.
                continue
            if frag is not None and bi is not None and (start_idx is not None) and fi <= blocks[start_idx].file_index \
                    and bi == 0:
                # Anchor was in the removed header; chapter begins where the body does.
                pass
        starts.append((bi, label or ""))

    if len(starts) < 2:
        # Fallback 1: headings
        starts = [(bi, b.text) for bi, b in enumerate(keep) if b.is_heading and b.tag in ("h1", "h2", "h3")]
        if len(starts) < 2:
            # Fallback 2: one chapter per spine file
            starts = [(idx, "") for _fi, idx in sorted(file_first_block.items())]

    # Dedupe / sort
    starts.sort(key=lambda s: s[0])
    merged_starts: list[tuple[int, list[str]]] = []
    for bi, label in starts:
        if merged_starts and merged_starts[-1][0] == bi:
            if label and label not in merged_starts[-1][1]:
                merged_starts[-1][1].append(label)
        else:
            merged_starts.append((bi, [label] if label else []))
    if not merged_starts or merged_starts[0][0] > 0:
        merged_starts.insert(0, (0, ["Front matter" if merged_starts else title]))

    # Build raw segments
    raw: list[Section] = []
    for n, (bi, labels) in enumerate(merged_starts):
        end = merged_starts[n + 1][0] if n + 1 < len(merged_starts) else len(keep)
        seg = keep[bi:end]
        if not seg:
            continue
        paragraphs = [b.text for b in seg if b.text]
        total = sum(len(p) for p in paragraphs)
        link_chars = sum(b.link_chars for b in seg)
        label = labels[-1] if labels else (seg[0].text if seg[0].is_heading else f"Section {n + 1}")
        sec = Section(title=label, paragraphs=paragraphs, headings=list(labels))
        if SKIP_TITLE_RE.match(label) or (total > 0 and link_chars / total > LINK_RATIO_SKIP and len(seg) > 3):
            sec.skipped = True
            sec.skip_reason = "table of contents / index"
        raw.append(sec)

    # Merge tiny heading-only segments into the following kept segment -------
    sections: list[Section] = []
    carry: Section | None = None
    for sec in raw:
        if carry is not None:
            if sec.skipped:
                # Keep the carried headings waiting for the next narrated section.
                sections.append(sec)
                continue
            sec.headings = carry.headings + [h for h in carry.paragraphs if _norm(h) not in
                                             {_norm(x) for x in carry.headings}] + sec.headings
            if _BYLINE_RE.match(sec.title) or sec.title.startswith("Section "):
                better = [h for h in carry.headings + carry.paragraphs if not _BYLINE_RE.match(h)]
                if better:
                    sec.title = better[0]
            carry = None
        if not sec.skipped and sec.char_count < MIN_CHAPTER_CHARS:
            carry = sec
            continue
        sections.append(sec)
    if carry is not None:
        # Nothing followed; keep it as its own (tiny) section.
        sections.append(carry)

    # Split overlong sections into parts -------------------------------------
    final: list[Section] = []
    for sec in sections:
        if sec.skipped or sec.char_count <= MAX_CHAPTER_CHARS:
            final.append(sec)
            continue
        n_parts = -(-sec.char_count // MAX_CHAPTER_CHARS)
        target = sec.char_count / n_parts
        part: list[str] = []
        size = 0
        parts: list[list[str]] = []
        for p in sec.paragraphs:
            part.append(p)
            size += len(p)
            if size >= target and len(parts) < n_parts - 1:
                parts.append(part)
                part, size = [], 0
        if part:
            parts.append(part)
        for i, ps in enumerate(parts, 1):
            final.append(Section(title=f"{sec.title} ({i}/{len(parts)})", paragraphs=ps,
                                 headings=sec.headings if i == 1 else []))

    narrated = [s for s in final if not s.skipped]
    for sec in final:
        if (sec.title.startswith("Section ") or _BYLINE_RE.match(sec.title)) and len(narrated) <= 2:
            m = re.search(r"\((\d+)/(\d+)\)\s*$", sec.title)
            sec.title = f"{title} {m.group(0)}" if m else title
        sec.title = display_title(sec.title)
        # Headings that are literally the first paragraph are handled in spoken_paragraphs().
        sec.headings = [h for h in sec.headings if h.strip()]

    return BookText(
        title=title, authors=authors, language=language, is_gutenberg=is_gutenberg,
        gutenberg_id=gutenberg_id, trimmed_start=trimmed_start, trimmed_end=trimmed_end,
        sections=final,
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    bt = extract_book(argv[1])
    print(f"{bt.title} — {', '.join(bt.authors)} [{bt.language}]")
    print(f"gutenberg={bt.is_gutenberg} id={bt.gutenberg_id} trimmed start={bt.trimmed_start} end={bt.trimmed_end}")
    print(f"{len(bt.sections)} sections, {bt.char_count:,} narrated chars (~{bt.char_count / 900 / 60:.1f} h)")
    for i, s in enumerate(bt.sections):
        flag = "SKIP " if s.skipped else "     "
        first = s.spoken_paragraphs()[0][:70].replace("\n", " ") if s.paragraphs else ""
        print(f"{flag}{i:3d} {s.char_count:7,d}  {s.title[:50]:50s} | {first}")
    if "--dump" in argv:
        for s in bt.sections:
            if s.skipped:
                continue
            print("\n" + "=" * 80 + "\n" + s.title + "\n" + "=" * 80)
            for p in s.spoken_paragraphs():
                print(clean_for_tts(p, bt.language))
                print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
