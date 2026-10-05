"""PyMuPDF adapter preserving parser extraction order and page identity."""

import asyncio

import pymupdf

from researchflow.domain.provenance import ParsedPage
from researchflow.ports.pdf import PdfParserError


class PyMuPdfParser:
    def __init__(self, *, version: str, maximum_pages: int) -> None:
        if not version.strip() or maximum_pages <= 0:
            raise ValueError("parser version and maximum_pages must be valid")
        self._version = version.strip()
        self._maximum_pages = maximum_pages

    @property
    def name(self) -> str:
        return "pymupdf"

    @property
    def version(self) -> str:
        return self._version

    async def parse(self, content: bytes) -> tuple[ParsedPage, ...]:
        return await asyncio.to_thread(self._parse_sync, bytes(content))

    def _parse_sync(self, content: bytes) -> tuple[ParsedPage, ...]:
        try:
            document: pymupdf.Document = pymupdf.open(  # type: ignore[no-untyped-call]
                stream=content, filetype="pdf"
            )
        except (pymupdf.FileDataError, RuntimeError, ValueError) as error:
            raise PdfParserError("pdf_malformed") from error

        try:
            if document.needs_pass:
                raise PdfParserError("pdf_encrypted")
            if document.page_count == 0:
                raise PdfParserError("pdf_empty")
            if document.page_count > self._maximum_pages:
                raise PdfParserError("pdf_too_many_pages")

            pages: list[ParsedPage] = []
            for page_index in range(document.page_count):
                page: pymupdf.Page = document.load_page(  # type: ignore[no-untyped-call]
                    page_index
                )
                try:
                    text = page.get_text("text", sort=False)  # type: ignore[no-untyped-call]
                    image_count = len(
                        page.get_images(full=True)  # type: ignore[no-untyped-call]
                    )
                except (RuntimeError, ValueError) as error:
                    raise PdfParserError("pdf_page_parse_failed") from error
                pages.append(ParsedPage(page_index + 1, text, image_count))
            return tuple(pages)
        finally:
            document.close()  # type: ignore[no-untyped-call]
