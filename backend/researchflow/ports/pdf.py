"""Provider-agnostic PDF parsing boundary."""

from typing import Protocol, runtime_checkable

from researchflow.domain.provenance import ParsedPage


class PdfParserError(Exception):
    """Classified parser failure with no source content in diagnostics."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@runtime_checkable
class PdfParser(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    async def parse(self, content: bytes) -> tuple[ParsedPage, ...]: ...
