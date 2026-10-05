"""Section-aware, page-bounded deterministic provenance chunker."""

import re
from dataclasses import dataclass
from typing import Final

from researchflow.domain.provenance import ChunkDraft, NormalizedPage, chunk_hash

CHUNKER_NAME: Final = "section-aware-codepoint"
CHUNKER_VERSION: Final = "rf-section-chunker-v1"
MAX_CHUNK_CODEPOINTS: Final = 1_600
CHUNK_OVERLAP_CODEPOINTS: Final = 200
_MIN_PREFERRED_BOUNDARY: Final = 320

_NUMBERED_HEADING = re.compile(r"^(?P<number>\d+(?:\.\d+)*\.?)\s+(?P<title>\S.{0,158})$")
_SENTENCE_END = re.compile(r"[.!?](?:[\"')\]]*)\s+")
_UNNUMBERED_SECTIONS = frozenset(
    {
        "abstract",
        "acknowledgments",
        "acknowledgements",
        "conclusion",
        "conclusions",
        "discussion",
        "introduction",
        "methods",
        "references",
        "results",
        "supplementary information",
    }
)


@dataclass(frozen=True, slots=True)
class _Heading:
    offset: int
    label: str
    is_section: bool


def _headings(text: str) -> tuple[_Heading, ...]:
    headings: list[_Heading] = []
    offset = 0
    for line_with_end in text.splitlines(keepends=True):
        line = line_with_end.rstrip("\n")
        match = _NUMBERED_HEADING.fullmatch(line)
        if match is not None and not match.group("title").endswith((".", ";")):
            number = match.group("number").rstrip(".")
            headings.append(_Heading(offset, line, number.count(".") == 0))
        elif line.casefold() in _UNNUMBERED_SECTIONS:
            headings.append(_Heading(offset, line, True))
        offset += len(line_with_end)
    return tuple(headings)


def _trimmed_end(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1].isspace():
        end -= 1
    return end


def _next_nonspace(text: str, offset: int) -> int:
    while offset < len(text) and text[offset].isspace():
        offset += 1
    return offset


def _preferred_end(
    text: str, start: int, hard_end: int, headings: tuple[_Heading, ...]
) -> tuple[int, bool]:
    next_heading = next(
        (heading.offset for heading in headings if start < heading.offset <= hard_end), None
    )
    if next_heading is not None:
        return _trimmed_end(text, start, next_heading), True

    minimum = min(start + _MIN_PREFERRED_BOUNDARY, hard_end)
    paragraph = text.rfind("\n\n", minimum, hard_end + 1)
    if paragraph >= minimum:
        return _trimmed_end(text, start, paragraph), False

    sentence_ends = [match.end() for match in _SENTENCE_END.finditer(text, minimum, hard_end)]
    if sentence_ends:
        return _trimmed_end(text, start, sentence_ends[-1]), False
    return _trimmed_end(text, start, hard_end), False


class SectionAwareChunker:
    name = CHUNKER_NAME
    version = CHUNKER_VERSION
    maximum_chunk_codepoints = MAX_CHUNK_CODEPOINTS
    overlap_codepoints = CHUNK_OVERLAP_CODEPOINTS

    def chunk(self, pages: tuple[NormalizedPage, ...]) -> tuple[ChunkDraft, ...]:
        chunks: list[ChunkDraft] = []
        current_section: str | None = None
        current_subsection: str | None = None

        for page in pages:
            text = page.text
            headings = _headings(text)
            cursor = _next_nonspace(text, 0)
            while cursor < len(text):
                for heading in headings:
                    if heading.offset <= cursor:
                        if heading.is_section:
                            current_section = heading.label
                            current_subsection = None
                        else:
                            current_subsection = heading.label

                hard_end = min(cursor + self.maximum_chunk_codepoints, len(text))
                end, ended_at_section = _preferred_end(text, cursor, hard_end, headings)
                if end <= cursor:
                    end = hard_end
                chunk_text = text[cursor:end]
                chunks.append(
                    ChunkDraft(
                        page_number=page.page_number,
                        section=current_section,
                        subsection=current_subsection,
                        chunk_index=len(chunks),
                        start_offset=cursor,
                        end_offset=end,
                        chunk_text=chunk_text,
                        content_hash=chunk_hash(chunk_text),
                    )
                )
                if end >= len(text):
                    break
                if ended_at_section:
                    cursor = _next_nonspace(text, end)
                else:
                    overlap_start = max(cursor + 1, end - self.overlap_codepoints)
                    cursor = _next_nonspace(text, overlap_start)
        return tuple(chunks)
