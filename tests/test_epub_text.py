"""EPUB -> narration plan: Gutenberg trimming, chapterisation, and the text helpers."""
from __future__ import annotations

import os
import re
import sys
import zipfile
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.epub_text import (  # noqa: E402
    Section, chunk_for_tts, clean_for_tts, display_title, extract_book, heading_for_speech, read_epub_metadata,
    smart_title, split_sentences,
)

BOOKS_DIR = Path(os.environ.get("LECTOR_TEST_BOOKS", "/Volumes/mediaDrive/media-server/downloads/media/books"))
BOOKS = sorted(BOOKS_DIR.glob("**/*.epub")) if BOOKS_DIR.is_dir() else []
needs_books = pytest.mark.skipif(not BOOKS, reason=f"sample EPUB folder {BOOKS_DIR} not available")


@lru_cache(maxsize=None)
def book(path: str):
    return extract_book(path)


def find_book(fragment: str) -> str:
    for p in BOOKS:
        if fragment.lower() in p.name.lower():
            return str(p)
    pytest.skip(f"{fragment!r} not in {BOOKS_DIR}")


# ---------------------------------------------------------------------------
# Real Project Gutenberg books
# ---------------------------------------------------------------------------
BOILERPLATE_RE = re.compile(r"PROJECT GUTENBERG|\*\s*\*\s*\*\s*(START|END) OF|START OF THE|END OF THE")


@needs_books
@pytest.mark.parametrize("path", [str(p) for p in BOOKS], ids=[p.stem for p in BOOKS])
def test_gutenberg_books_are_trimmed(path):
    bt = book(path)
    assert bt.is_gutenberg
    assert bt.trimmed_start and bt.trimmed_end
    assert bt.authors and bt.title
    narrated = [s for s in bt.sections if not s.skipped]
    assert narrated and bt.char_count > 5000
    for s in narrated:
        for p in s.spoken_paragraphs():
            # Case-sensitive: the markers are upper case; "at the end of the day" is fine.
            assert not BOILERPLATE_RE.search(p), (s.title, p[:200])
            assert "project gutenberg" not in p.lower(), (s.title, p[:200])
    for s in bt.sections:
        assert "www.gutenberg.org" not in "\n".join(s.paragraphs + s.headings).lower(), s.title


@needs_books
@pytest.mark.parametrize("path", [str(p) for p in BOOKS], ids=[p.stem for p in BOOKS])
def test_paragraphs_are_not_hard_wrapped(path):
    # Gutenberg HTML is wrapped at ~72 columns; those newlines must not survive into the text
    # (chunk_for_tts treats "\n" as a line break, which would split sentences).
    bt = book(path)
    paras = [p for s in bt.sections if not s.skipped for p in s.paragraphs]
    wrapped = [p for p in paras if "\n" in p]
    assert len(wrapped) <= len(paras) * 0.05, wrapped[:3]


@needs_books
@pytest.mark.parametrize("path", [str(p) for p in BOOKS], ids=[p.stem for p in BOOKS])
def test_sections_are_sane(path):
    bt = book(path)
    for s in bt.sections:
        assert s.title and s.title == s.title.strip()
        assert "\n" not in s.title
        assert s.paragraphs
        assert len(s.text_hash()) == 64
        if not s.skipped:
            assert s.char_count <= 48_000 * 1.2, (s.title, s.char_count)


@needs_books
def test_gift_of_the_magi_first_and_last_paragraph():
    bt = book(find_book("Gift of the Magi"))
    narrated = [s for s in bt.sections if not s.skipped]
    spoken = [p for s in narrated for p in s.spoken_paragraphs()]
    assert spoken[0] == "The Gift of the Magi."
    assert spoken[-1].endswith("They are the magi.")
    assert spoken[-1].startswith("The magi, as you know, were wise men")


@needs_books
def test_tale_of_two_cities_chapters():
    bt = book(find_book("Tale of Two Cities"))
    assert len(bt.sections) >= 45
    assert any(s.skipped and s.title == "Contents" for s in bt.sections)
    titles = [s.title for s in bt.sections if not s.skipped]
    assert "CHAPTER I. The Period" in titles
    assert "CHAPTER II. The Mail" in titles
    assert sum(t.startswith("CHAPTER ") for t in titles) >= 44
    period = next(s for s in bt.sections if s.title == "CHAPTER I. The Period")
    joined = " ".join(period.paragraphs)
    assert "It was the best of times" in joined
    mail = next(s for s in bt.sections if s.title == "CHAPTER II. The Mail")
    assert mail.spoken_paragraphs()[0] == "Chapter 2. The Mail."


@needs_books
def test_poe_volume_titles():
    bt = book(find_book("Poe - Volume 1"))
    titles = [s.title for s in bt.sections]
    assert "Death of Edgar A. Poe" in titles
    assert titles[0] == "Contents" and bt.sections[0].skipped


def test_read_epub_metadata_on_real_books():
    if not BOOKS:
        pytest.skip("no sample books")
    for p in BOOKS:
        m = read_epub_metadata(str(p))
        assert m["title"] and m["authors"] and m["language"].startswith("en")
        assert m["is_gutenberg"] and m["gutenberg_id"]


# ---------------------------------------------------------------------------
# A synthetic, non-Gutenberg EPUB (no START/END markers)
# ---------------------------------------------------------------------------
LOREM = ("The lighthouse keeper climbed the spiral stairs every evening at dusk, counting each of the "
         "one hundred and twelve steps aloud as his father had done before him. ")

SYNTH_CHAPTERS = [
    ("ch1.xhtml", "The First Night",
     [LOREM * 2,
      "She said\nthat the lamp had\nnever once gone out, not in forty winters."]),  # source-wrapped
    ("ch2.xhtml", "The Storm",
     [LOREM * 3, "Once upon a midnight dreary,<br/>while I pondered, weak and weary."]),
    ("ch3.xhtml", "Morning",
     [LOREM * 2, "In the morning the sea was flat and grey, and the gulls had come back to the rocks."]),
]


def make_synthetic_epub(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>')
        items = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
        spine = []
        nav_li = []
        for i, (fname, title, paras) in enumerate(SYNTH_CHAPTERS, 1):
            items.append(f'<item id="c{i}" href="text/{fname}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
            nav_li.append(f'<li><a href="text/{fname}#h{i}">{title}</a></li>')
            body = "".join(f"<p>{p}</p>" for p in paras)
            z.writestr(f"OEBPS/text/{fname}",
                       '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                       f"<head><title>{title}</title></head><body><!-- page {i} -->"
                       f'<h1 id="h{i}">{title}</h1>{body}</body></html>')
        z.writestr("OEBPS/nav.xhtml",
                   '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
                   'xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>'
                   + "".join(nav_li) + "</ol></nav></body></html>")
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0" encoding="utf-8"?>'
                   '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="uid">urn:uuid:1b4e28ba-2fa1-11d2-883f-0016d3cca427</dc:identifier>'
                   '<dc:title>The Keeper</dc:title>'
                   '<dc:creator id="a1">Jane Author</dc:creator>'
                   '<meta refines="#a1" property="role">aut</meta>'
                   '<dc:creator id="t1">Tom Translator</dc:creator>'
                   '<meta refines="#t1" property="role">trl</meta>'
                   '<dc:language>en-GB</dc:language>'
                   '<dc:description>&lt;p&gt;A short &lt;i&gt;novel&lt;/i&gt;.&lt;/p&gt;</dc:description>'
                   '</metadata><manifest>' + "".join(items) + '</manifest><spine>'
                   + "".join(spine) + '</spine></package>')
    return path


@pytest.fixture()
def synthetic(tmp_path):
    return make_synthetic_epub(tmp_path / "keeper.epub")


def test_synthetic_epub_chapters(synthetic):
    bt = extract_book(str(synthetic))
    assert bt.title == "The Keeper"
    assert bt.authors == ["Jane Author"]  # the translator is not an author
    assert bt.language == "en-GB"
    assert bt.is_gutenberg is False and bt.gutenberg_id is None
    assert bt.trimmed_start is False and bt.trimmed_end is False
    assert [s.title for s in bt.sections] == ["The First Night", "The Storm", "Morning"]
    assert not any(s.skipped for s in bt.sections)
    first = bt.sections[0].spoken_paragraphs()
    assert first[0] == "The First Night."
    assert first[-1] == "She said that the lamp had never once gone out, not in forty winters."
    storm = bt.sections[1].paragraphs
    assert storm[-1] == "Once upon a midnight dreary,\nwhile I pondered, weak and weary."
    assert all("page" not in p for s in bt.sections for p in s.paragraphs)  # HTML comments dropped


def test_synthetic_epub_without_toc_falls_back_to_headings(tmp_path):
    path = make_synthetic_epub(tmp_path / "nonav.epub")
    # Rewrite without the nav document: chapterisation must come from <h1> headings.
    stripped = tmp_path / "stripped.epub"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(stripped, "w") as zout:
        for item in zin.infolist():
            if item.filename == "OEBPS/nav.xhtml":
                continue
            data = zin.read(item.filename)
            if item.filename.endswith(".opf"):
                data = data.replace(b' properties="nav"', b"").replace(
                    b'<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>', b"")
            zout.writestr(item, data)
    bt = extract_book(str(stripped))
    assert [s.title for s in bt.sections] == ["The First Night", "The Storm", "Morning"]
    assert bt.is_gutenberg is False


def test_read_epub_metadata_synthetic(synthetic):
    m = read_epub_metadata(str(synthetic))
    assert m["title"] == "The Keeper"
    assert m["authors"] == ["Jane Author"]
    assert m["language"] == "en-GB"
    assert m["gutenberg_id"] is None and m["is_gutenberg"] is False
    assert re.sub(r"\s+", "", m["description"]) == "Ashortnovel."  # HTML stripped


def test_epub_without_container_xml(tmp_path, synthetic):
    broken = tmp_path / "nocontainer.epub"
    with zipfile.ZipFile(synthetic) as zin, zipfile.ZipFile(broken, "w") as zout:
        for item in zin.infolist():
            if item.filename != "META-INF/container.xml":
                zout.writestr(item, zin.read(item.filename))
    assert read_epub_metadata(str(broken))["title"] == "The Keeper"
    assert len(extract_book(str(broken)).sections) == 3


def test_not_an_epub(tmp_path):
    p = tmp_path / "x.epub"
    p.write_bytes(b"not a zip")
    with pytest.raises(zipfile.BadZipFile):
        read_epub_metadata(str(p))


# ---------------------------------------------------------------------------
# heading_for_speech / smart_title / display_title
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, spoken", [
    ("CHAPTER IV. The Preparation", "Chapter 4. The Preparation."),
    ("II", "2."),
    ("XIV.", "14."),
    ("CHAPTER I", "Chapter 1."),
    ("BOOK THE SECOND", "Book the Second."),
    ("PART III. THE TRACK OF A STORM", "Part 3. The Track of a Storm."),
    ("The Gift of the Magi", "The Gift of the Magi."),
    ("Is it you?", "Is it you?"),
    ("Chapter 5[12]", "Chapter 5."),
    ("Letter mix", "Letter mix."),  # lower-case words are not numerals
])
def test_heading_for_speech(raw, spoken):
    assert heading_for_speech(raw) == spoken


@pytest.mark.parametrize("raw, title", [
    ("DEATH OF EDGAR A. POE", "Death of Edgar A. Poe"),
    ("THE FALL OF THE HOUSE OF USHER", "The Fall of the House of Usher"),
    ("CHAPTER XIV", "Chapter XIV"),
    ("LANDOR'S COTTAGE", "Landor's Cottage"),
    ("O'BRIEN AND THE WINE-SHOP", "O'Brien and the Wine-Shop"),
    ("A MILD DAY", "A Mild Day"),          # MILD / DID are not roman numerals
    ("WHAT DID HE DO", "What Did He Do"),
    ("Already Mixed Case", "Already Mixed Case"),
    ("THE U.S. MAIL", "The U.S. Mail"),
])
def test_smart_title(raw, title):
    assert smart_title(raw) == title


@pytest.mark.parametrize("raw, title", [
    ("DEATH OF EDGAR A. POE", "Death of Edgar A. Poe"),
    ("The Black Cat.", "The Black Cat"),
    ("Edgar A.", "Edgar A."),
    ("CHAPTER I. The Period", "CHAPTER I. The Period"),
    ("Notes[1]", "Notes"),
    ("", "Untitled"),
    ("  The\nTitle  ", "The Title"),
])
def test_display_title(raw, title):
    assert display_title(raw) == title


# ---------------------------------------------------------------------------
# clean_for_tts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, clean", [
    ("wise men—wonderfully wise men—who brought gifts", "wise men, wonderfully wise men, who brought gifts"),
    ("He stopped— and left.", "He stopped and left."),
    ("“But—” she said.", '"But " she said.'),
    ("in the years 1840–1850", "in the years 1840 to 1850"),
    ("The end.[12] Next[3] line{4}", "The end. Next line"),
    ("A note.(*3)", "A note."),
    ("It was _very_ cold and _quite_ dark.", "It was very cold and quite dark."),
    ("snake_case_name stays", "snake_case_name stays"),
    ("Château d’If, café, naïve, Æsop, Œuvre", "Chateau d'If, cafe, naive, AEsop, OEuvre"),
    ("Wait…", "Wait..."),
    ("salt & pepper, &c.", "salt and pepper, etc."),
    ("* * *", ""),
    ("a b", "a b"),
])
def test_clean_for_tts(raw, clean):
    assert clean_for_tts(raw) == clean


def test_clean_for_tts_keeps_accents_for_other_languages():
    assert clean_for_tts("Château", "fr") == "Château"


def test_clean_for_tts_keeps_line_breaks():
    assert clean_for_tts("Line one,\n  line two.") == "Line one,\nline two."


# ---------------------------------------------------------------------------
# split_sentences / chunk_for_tts
# ---------------------------------------------------------------------------
def test_split_sentences_respects_abbreviations():
    assert split_sentences('Mr. Lorry met Edgar A. Poe. Then he left! "Why?" she asked. No. 4 was empty.') == [
        "Mr. Lorry met Edgar A. Poe.", "Then he left!", '"Why?" she asked.', "No. 4 was empty."]


PARAS = [
    "It was the best of times, it was the worst of times, it was the age of wisdom, it was the age of "
    "foolishness, it was the epoch of belief, it was the epoch of incredulity, it was the season of Light, "
    "it was the season of Darkness, it was the spring of hope, it was the winter of despair, we had "
    "everything before us, we had nothing before us, we were all going direct to Heaven, we were all going "
    "direct the other way. In short, the period was so far like the present period. Short one.",
    "Second paragraph. It has two sentences.",
    "Line one of a verse\nLine two of a verse",
    "x" * 500,
]


@pytest.mark.parametrize("max_chars", [40, 80, 120, 200, 380])
def test_chunks_respect_max_length(max_chars):
    chunks = chunk_for_tts(PARAS, max_chars)
    assert chunks
    assert all(0 < len(c) <= max_chars for c in chunks), [len(c) for c in chunks]


@pytest.mark.parametrize("max_chars", [40, 80, 120, 380])
def test_chunks_do_not_split_words(max_chars):
    paras = PARAS[:3]
    words = [w for p in paras for w in p.split()]
    chunk_words = [w for c in chunk_for_tts(paras, max_chars) for w in c.split()]
    assert chunk_words == words  # same words, same order, none broken in two


def test_chunks_keep_paragraph_boundaries():
    paras = ["Alpha one. Alpha two.", "Beta one. Beta two.", "Gamma."]
    chunks = chunk_for_tts(paras, 380)
    assert chunks == ["Alpha one. Alpha two.", "Beta one. Beta two.", "Gamma."]


def test_chunks_pack_whole_sentences():
    para = "One sentence here. Two sentence here. Three sentence here. Four sentence here."
    chunks = chunk_for_tts([para], 40)
    assert chunks == ["One sentence here. Two sentence here.", "Three sentence here. Four sentence here."]
    for c in chunks:
        assert c.endswith(".")


def test_overlong_sentence_splits_at_clause_break():
    chunks = chunk_for_tts([PARAS[0]], 120)
    assert all(c.endswith((",", ".")) for c in chunks[:-1]), chunks


def test_unbreakable_token_is_hard_cut():
    chunks = chunk_for_tts(["y" * 1000], 380)
    assert [len(c) for c in chunks] == [380, 380, 240]


def test_empty_input():
    assert chunk_for_tts([]) == []
    assert chunk_for_tts(["", "  \n "]) == []


# ---------------------------------------------------------------------------
# Section
# ---------------------------------------------------------------------------
def test_section_heading_not_spoken_twice():
    s = Section(title="Chapter I", paragraphs=["CHAPTER I.", "Text."], headings=["CHAPTER I."])
    assert s.spoken_paragraphs() == ["Chapter 1.", "Text."]


def test_section_text_hash_changes_with_text():
    a = Section(title="A", paragraphs=["One."], headings=["A"])
    b = Section(title="A", paragraphs=["Two."], headings=["A"])
    assert a.text_hash() != b.text_hash()
    assert a.text_hash() == Section(title="A", paragraphs=["One."], headings=["A"]).text_hash()
