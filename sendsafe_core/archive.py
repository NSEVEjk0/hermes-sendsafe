"""Bounded OOXML reading. Nothing is extracted, fetched, or executed."""

from __future__ import annotations

import io
import os
import posixpath
import stat
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

MAX_FILE = 20 * 1024 * 1024
MAX_PART = 16 * 1024 * 1024
MAX_EXPANDED = 80 * 1024 * 1024
MAX_PARTS = 1500
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}


class DocumentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def xml(data: bytes) -> ET.Element:
    # Reject declarations before invoking the parser, including UTF-16 documents.
    encoding = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data[:64] else "utf-8-sig"
    try:
        text = data.decode(encoding)
    except UnicodeError:
        raise DocumentError("xml_encoding", "An XML part has an unsupported encoding.") from None
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise DocumentError("xml_entities", "DTD and entity declarations are refused.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise DocumentError("malformed_xml", "An XML part is malformed.") from None
    stack = [(root, 1)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > 400_000 or depth > 80:
            raise DocumentError("xml_limit", "An XML part exceeds the structural limit.")
        stack.extend((child, depth + 1) for child in node)
    return root


def bounded_read(path: str | Path, roots: list[str | Path] | None = None) -> tuple[Path, bytes]:
    raw = Path(path).expanduser().absolute()
    if raw.suffix.lower() not in (".docx", ".xlsx"):
        raise DocumentError("unsupported_format", "Only .docx and .xlsx documents are supported.")
    try:
        resolved = raw.resolve(strict=True)
        if raw.is_symlink():
            raise DocumentError("symlink", "Document symlinks are refused; select the actual file.")
        if roots is not None and not any(resolved.is_relative_to(Path(r).expanduser().resolve()) for r in roots):
            raise DocumentError("outside_roots", "This document is outside the folders you allowed SendSafe to inspect.")
        fd = os.open(raw, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise DocumentError("not_regular", "The document must be a regular file.")
            if info.st_size > MAX_FILE:
                raise DocumentError("file_limit", "The document exceeds the 20 MiB input limit.")
            data = file.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise DocumentError("file_limit", "The document exceeds the 20 MiB input limit.")
            after = os.fstat(file.fileno())
            if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise DocumentError("changed_during_read", "The document changed during inspection. Scan it again.")
        return resolved, data
    except DocumentError:
        raise
    except (OSError, RuntimeError):
        raise DocumentError("file_unavailable", "The document could not be read. Check the file and permissions.") from None


class Package:
    def __init__(self, data: bytes, kind: str):
        self.kind = kind
        self.parts: dict[str, bytes] = {}
        self.trees: dict[str, ET.Element] = {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_PARTS:
                    raise DocumentError("archive_limit", "The document has too many archive parts.")
                total = 0
                seen = set()
                for item in infos:
                    name = item.filename
                    if item.is_dir():
                        continue
                    canonical = unquote(name)
                    if ("\\" in name or canonical.startswith("/") or ":" in canonical
                            or any(p in ("", ".", "..") for p in canonical.split("/"))):
                        raise DocumentError("archive_path", "The document contains an unsafe archive path.")
                    if name.casefold() in seen:
                        raise DocumentError("duplicate_part", "The document contains duplicate archive parts.")
                    seen.add(name.casefold())
                    if stat.S_ISLNK(item.external_attr >> 16):
                        raise DocumentError("archive_symlink", "Archive symlinks are refused.")
                    if item.flag_bits & 1:
                        raise DocumentError("encrypted", "Encrypted documents are not supported.")
                    if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                        raise DocumentError("compression", "This archive compression method is not supported.")
                    if item.file_size > MAX_PART or total + item.file_size > MAX_EXPANDED:
                        raise DocumentError("archive_limit", "The document exceeds the expanded archive limit.")
                    if item.file_size > 1024 * 1024 and item.file_size / max(1, item.compress_size) > 250:
                        raise DocumentError("compression_ratio", "An archive part has an excessive compression ratio.")
                    with archive.open(item) as part:
                        content = part.read(MAX_PART + 1)
                    if len(content) != item.file_size or len(content) > MAX_PART:
                        raise DocumentError("archive_limit", "An archive part exceeds its declared size.")
                    total += len(content)
                    self.parts[name] = content
                    if name.endswith((".xml", ".rels")) or name == "[Content_Types].xml":
                        self.trees[name] = xml(content)
        except DocumentError:
            raise
        except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError):
            raise DocumentError("invalid_archive", "The document is not a valid, readable OOXML archive.") from None
        main = "word/document.xml" if kind == "docx" else "xl/workbook.xml"
        expected = f"{{{NS['w']}}}document" if kind == "docx" else f"{{{NS['x']}}}workbook"
        if main not in self.trees or self.trees[main].tag != expected or "[Content_Types].xml" not in self.trees:
            raise DocumentError("invalid_document", "The file is not a supported Word or Excel document.")
        types = self.trees["[Content_Types].xml"]
        if types.tag != f"{{{NS['ct']}}}Types":
            raise DocumentError("invalid_document", "The document content types are invalid.")
        self._validate_relationships()

    def _validate_relationships(self):
        for name, root in self.trees.items():
            if not name.endswith(".rels"):
                continue
            if root.tag != f"{{{NS['pr']}}}Relationships":
                raise DocumentError("invalid_relationship", "A relationship part is malformed.")
            ids = set()
            for relationship in root:
                rid = relationship.get("Id")
                if not rid or rid in ids:
                    raise DocumentError("invalid_relationship", "A relationship ID is missing or duplicated.")
                ids.add(rid)
                if relationship.get("TargetMode") == "External":
                    continue
                target = self.target(name, relationship.get("Target", ""))
                if target not in self.parts:
                    raise DocumentError("missing_relationship", "The document references a missing archive part.")

    @staticmethod
    def target(rels_name: str, target: str) -> str:
        target = unquote(target).split("#", 1)[0]
        if "\\" in target or ":" in target:
            raise DocumentError("invalid_relationship", "An internal relationship has an unsafe target.")
        base = "" if rels_name == "_rels/.rels" else posixpath.dirname(posixpath.dirname(rels_name))
        resolved = posixpath.normpath(posixpath.join(base, target.lstrip("/"))) if not target.startswith("/") else posixpath.normpath(target.lstrip("/"))
        if resolved.startswith("../") or resolved in ("", ".", ".."):
            raise DocumentError("invalid_relationship", "A relationship escapes the document package.")
        return resolved

    def workbook_sheets(self):
        rels_name = "xl/_rels/workbook.xml.rels"
        rels = self.trees.get(rels_name)
        if rels is None:
            raise DocumentError("missing_relationship", "The workbook sheet relationships are missing.")
        mapping = {r.get("Id"): self.target(rels_name, r.get("Target", ""))
                   for r in rels if r.get("TargetMode") != "External"}
        sheets = []
        seen = set()
        for sheet in self.trees["xl/workbook.xml"].findall("x:sheets/x:sheet", NS):
            target = mapping.get(sheet.get(f"{{{NS['r']}}}id"))
            if not target or target not in self.trees or target in seen:
                raise DocumentError("invalid_sheet", "A workbook sheet cannot be resolved uniquely.")
            if self.trees[target].tag != f"{{{NS['x']}}}worksheet":
                raise DocumentError("unsupported_sheet", "Only standard Excel worksheets can be exported.")
            seen.add(target)
            sheets.append((sheet, target, self.trees[target]))
        if not sheets or len(sheets) > 100:
            raise DocumentError("sheet_limit", "The workbook must contain between 1 and 100 worksheets.")
        return sheets

