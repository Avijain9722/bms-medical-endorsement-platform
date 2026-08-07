"""Local text extraction.

All processing is local. Nothing is sent to an external service, and the runtime
needs no internet access.

Extraction is a chain of adapters tried in order:

1. embedded PDF text -- free, exact, and covers the many "print to PDF" documents
   that arrive from client HR systems;
2. Tesseract on rasterised pages or images -- for scans and photographs;
3. nothing.

Each adapter reports whether it is available on this host. When none is, the
document is marked `ocr_unavailable` and every field it would have supplied is
raised as a review flag for manual entry. The platform never guesses a value
because an engine was missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import Settings, settings

PDF_MAGIC = b"%PDF"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


@dataclass(frozen=True)
class TextResult:
    text: str
    source: str
    confidence: float

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class TextExtractor(Protocol):
    name: str

    def available(self) -> bool: ...

    def supports(self, filename: str, data: bytes) -> bool: ...

    def extract(self, filename: str, data: bytes) -> TextResult | None: ...


class EmbeddedPdfText:
    """Read the text layer of a PDF without rasterising it.

    Preferred over OCR whenever it yields anything: it is exact rather than
    probabilistic, so extracted values start at high confidence.
    """

    name = "pdf_text"

    def available(self) -> bool:
        # BaseException, not Exception: a PDF backend with a broken native
        # extension can raise a Rust panic (pyo3 PanicException derives from
        # BaseException) at import. An optional reader failing to load must
        # degrade this adapter, never take the application down.
        try:
            import pypdf  # noqa: F401
        except BaseException:
            return False
        return True

    def supports(self, filename: str, data: bytes) -> bool:
        return data[:4] == PDF_MAGIC or filename.lower().endswith(".pdf")

    def extract(self, filename: str, data: bytes) -> TextResult | None:
        try:
            import pypdf
        except BaseException:
            return None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
                handle.write(data)
                handle.flush()
                reader = pypdf.PdfReader(handle.name)
                if reader.is_encrypted:
                    return None
                pages = [page.extract_text() or "" for page in reader.pages]
        except BaseException:
            # A malformed or password-protected PDF is a document problem, not an
            # application problem: fall through so the next adapter can try.
            return None
        text = "\n".join(pages).strip()
        if not text:
            return None
        # An embedded text layer is a transcript, not a reading -- but a scanned
        # PDF can carry a thin, unreliable layer, so this is high rather than
        # absolute.
        return TextResult(text=text, source=self.name, confidence=0.95)


class TesseractOcr:
    """Local Tesseract. Never a network call."""

    name = "tesseract"

    def __init__(self, config: Settings | None = None):
        self.config = config or settings

    def available(self) -> bool:
        if not self.config.ocr_enabled:
            return False
        command = self.config.tesseract_cmd
        # shutil.which only searches PATH entries, so a bundled copy addressed by
        # an absolute path has to be checked directly.
        if os.path.isabs(command):
            return os.path.isfile(command) and os.access(command, os.X_OK)
        return shutil.which(command) is not None

    def _environment(self) -> dict[str, str] | None:
        """TESSDATA_PREFIX for a bundled Tesseract; nothing for a system one."""
        tessdata = self.config.tessdata_dir
        if tessdata is None:
            return None
        return {**os.environ, "TESSDATA_PREFIX": str(tessdata)}

    def _tessdata_arguments(self) -> list[str]:
        """`--tessdata-dir`, which means the same thing in every Tesseract.

        TESSDATA_PREFIX alone is not portable: Tesseract 5 treats it as the
        directory holding the language files, while Tesseract 4 -- still what
        `apt-get install tesseract-ocr` gives on Debian 11 and Ubuntu 22.04 --
        appends "tessdata/" to it and then fails to find
        `<dir>/tessdata/eng.traineddata`. The command-line option is honoured
        verbatim by both and takes precedence, so it is what actually makes a
        bundled copy work across versions.
        """
        tessdata = self.config.tessdata_dir
        return ["--tessdata-dir", str(tessdata)] if tessdata else []

    def supports(self, filename: str, data: bytes) -> bool:
        suffix = Path(filename).suffix.lower()
        return suffix in IMAGE_SUFFIXES or data[:4] == PDF_MAGIC

    def extract(self, filename: str, data: bytes) -> TextResult | None:
        suffix = Path(filename).suffix.lower() or ".png"
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / f"input{suffix}"
            source.write_bytes(data)
            try:
                completed = subprocess.run(
                    [
                        self.config.tesseract_cmd,
                        str(source),
                        "stdout",
                        "-l",
                        self.config.ocr_languages,
                        *self._tessdata_arguments(),
                    ],
                    capture_output=True,
                    timeout=180,
                    check=False,
                    env=self._environment(),
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
        if completed.returncode != 0:
            return None
        text = completed.stdout.decode("utf-8", "replace").strip()
        if not text:
            return None
        # OCR output is a reading, so downstream validation matters more here.
        # Field-level confidence is refined by the extractors in `fields.py`.
        return TextResult(text=text, source=self.name, confidence=0.6)


class PlainTextFile:
    """Client sheets and notes that arrive as plain text."""

    name = "plain_text"

    def available(self) -> bool:
        return True

    def supports(self, filename: str, data: bytes) -> bool:
        return Path(filename).suffix.lower() in {".txt", ".csv"}

    def extract(self, filename: str, data: bytes) -> TextResult | None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("cp1252")
            except UnicodeDecodeError:
                return None
        return TextResult(text=text.strip(), source=self.name, confidence=1.0) if text.strip() else None


class TextPipeline:
    """Tries each adapter in order and returns the first usable result."""

    def __init__(self, extractors: list[TextExtractor] | None = None):
        self.extractors = extractors if extractors is not None else default_extractors()

    def available_engines(self) -> list[str]:
        return [e.name for e in self.extractors if e.available()]

    def extract(self, filename: str, data: bytes) -> TextResult | None:
        for extractor in self.extractors:
            if not extractor.available() or not extractor.supports(filename, data):
                continue
            result = extractor.extract(filename, data)
            if result is not None and not result.is_empty:
                return result
        return None


def default_extractors() -> list[TextExtractor]:
    return [PlainTextFile(), EmbeddedPdfText(), TesseractOcr()]
