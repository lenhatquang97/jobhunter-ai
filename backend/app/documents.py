"""Bounded extraction with stable page/paragraph/line locations; no links are followed."""

import asyncio
import base64
import io
import json
import os
import sys
import zipfile
from pathlib import Path

from defusedxml import ElementTree
from pypdf import PdfReader

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 100000
MAX_EXCERPT = 10000
MAX_FRAGMENTS = 200
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def extract_document(document):
    try:
        encoded = document["content_base64"]
        if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES + 2) // 3 * 4:
            raise ValueError("Document is too large")
        content = base64.b64decode(encoded, validate=True)
        if not content or len(content) > MAX_BYTES:
            raise ValueError("Document is empty or too large")
        if (
            document["res_model"] not in ("hr.job", "hr.applicant")
            or type(document["res_id"]) is not int
            or document["res_id"] <= 0
        ):
            raise ValueError("Invalid parent")
        mime = document["mimetype"]
        if mime == "text/plain":
            units = [
                (f"line:{i}", line)
                for i, line in enumerate(content.decode("utf-8-sig").splitlines(), 1)
            ]
        elif mime == "application/pdf":
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted or len(reader.pages) > 500:
                raise ValueError("Encrypted or oversized PDF")
            units = [
                (f"page:{i}", page.extract_text() or "") for i, page in enumerate(reader.pages, 1)
            ]
        elif mime == DOCX:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(info.file_size for info in archive.infolist()) > 25 * 1024 * 1024:
                    raise ValueError("Expanded document is too large")
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

            def paragraph_text(paragraph):
                pieces = []
                for node in paragraph.iter():
                    if node.tag == "{" + ns["w"] + "}t":
                        pieces.append(node.text or "")
                    elif node.tag == "{" + ns["w"] + "}tab":
                        pieces.append("\t")
                    elif node.tag in {"{" + ns["w"] + "}br", "{" + ns["w"] + "}cr"}:
                        pieces.append("\n")
                return "".join(pieces)

            units = [
                (f"paragraph:{i}", paragraph_text(p))
                for i, p in enumerate(root.findall(".//w:p", ns), 1)
            ]
        else:
            raise ValueError("Unsupported document type")
        sources, remaining = [], MAX_TEXT
        for locator, text in units:
            if not text.strip():
                continue
            for offset in range(0, len(text), MAX_EXCERPT):
                excerpt = text[offset : offset + min(MAX_EXCERPT, remaining)]
                if not excerpt:
                    break
                source = {
                    "key": "extracted",
                    "model": document["res_model"],
                    "record_id": document["res_id"],
                    "attachment_id": document["id"],
                    "title": document["name"][:300],
                    "write_date": document["write_date"],
                    "checksum": document["checksum"],
                    "locator": locator
                    if offset == 0 and len(text) <= MAX_EXCERPT
                    else f"{locator}:chars:{offset + 1}-{offset + len(excerpt)}",
                    "excerpt": excerpt,
                }
                sources.append(source)
                remaining -= len(excerpt)
                if remaining == 0 or len(sources) == MAX_FRAGMENTS:
                    # Explicit flag is extraction metadata, stripped before SourceRef storage.
                    source["truncated"] = (
                        offset + len(excerpt) < len(text) or (locator, text) != units[-1]
                    )
                    break
            if not remaining or len(sources) == MAX_FRAGMENTS:
                break
        if not sources:
            raise ValueError("Document has no extractable text")
        return sources
    except Exception as exc:
        # Parser exception text may contain document contents. Never expose it.
        raise ValueError("Document needs readable supported text") from exc


async def extract_document_async(document):
    """Isolate hostile parsers from the API event loop with CPU/memory/time caps.

    The subprocess never inherits service/provider secrets. On timeout/cancel it is killed
    and reaped before the run can proceed. Production image is Linux (resource limits).
    """
    encoded = json.dumps(document).encode()
    if len(encoded) > 14 * 1024 * 1024:
        raise ValueError("Document needs readable supported text")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).resolve()),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL"}},
    )
    try:
        async with asyncio.timeout(8):
            output, _ = await process.communicate(encoded)
        if process.returncode or len(output) > 2 * 1024 * 1024:
            raise ValueError("Document needs readable supported text")
        return json.loads(output)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise ValueError("Document needs readable supported text") from exc
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


def _parser_main():
    import logging
    import resource

    logging.disable(logging.CRITICAL)
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    try:
        document = json.loads(sys.stdin.buffer.read(14 * 1024 * 1024 + 1))
        sys.stdout.write(json.dumps(extract_document(document), ensure_ascii=True))
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    _parser_main()
