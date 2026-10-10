import re
from dataclasses import dataclass, field

from .config import CHUNK_CHARS, CHUNK_OVERLAP, MAX_PAGES


@dataclass
class Page:
    number: int
    text: str


@dataclass
class Chunk:
    index: int
    start_page: int
    end_page: int
    text: str
    pages: dict[int, str] = field(default_factory=dict)


def normalize(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\ufb00", "ff").replace("\ufb01", "fi")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_pages(path: str, max_pages: int | None = None) -> list[Page]:
    from pypdf import PdfReader

    limit = max_pages if max_pages is not None else MAX_PAGES
    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError("PDF is password protected") from exc

    total = len(reader.pages)
    if total > limit:
        raise ValueError(
            f"Document has {total} pages. Your plan allows {limit}. Upgrade to analyse longer tenders."
        )
    pages: list[Page] = []
    empty_pages = 0
    for i in range(min(total, limit)):
        try:
            raw = reader.pages[i].extract_text() or ""
        except Exception:
            raw = ""
        text = normalize(raw)
        if not text:
            empty_pages += 1
        pages.append(Page(number=i + 1, text=text))

    if not any(p.text for p in pages):
        raise ValueError(
            f"No text layer found in {total} pages. This is a scanned PDF - OCR is required."
        )
    if empty_pages > len(pages) * 0.6:
        raise ValueError(
            f"Only {len(pages) - empty_pages} of {len(pages)} pages contain text. Scanned PDF, OCR required."
        )
    return pages


def _page_text_block(pages: list[Page]) -> str:
    parts = []
    for p in pages:
        parts.append(f"[[PAGE {p.number}]]\n{p.text}")
    return "\n\n".join(parts)


def chunk_pages(pages: list[Page], size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[Chunk]:
    chunks: list[Chunk] = []
    buf: list[Page] = []
    length = 0
    index = 0

    def flush() -> None:
        nonlocal buf, length, index
        if not buf:
            return
        text = _page_text_block(buf)
        page_map = {p.number: p.text for p in buf}
        chunks.append(
            Chunk(
                index=index,
                start_page=buf[0].number,
                end_page=buf[-1].number,
                text=text,
                pages=page_map,
            )
        )
        index += 1
        buf = []
        length = 0

    for page in pages:
        add = len(page.text) + 20
        if length and length + add > size:
            tail = [page]
            flush()
            carry = min(overlap, size // 2)
            while tail and carry > 0:
                candidate = _page_text_block(tail)
                if len(candidate) > carry:
                    break
                carry -= len(tail[-1].text) + 20
                tail.insert(0, tail.pop())
            buf = tail
            length = len(_page_text_block(tail))
        buf.append(page)
        length += add

    flush()
    return chunks