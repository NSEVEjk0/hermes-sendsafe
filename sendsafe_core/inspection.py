"""Risk summaries deliberately contain no hidden text, values, or author names."""

from __future__ import annotations

import hashlib
from .archive import NS, Package

LABELS = {
    "personal_metadata": ("Author and document metadata", "medium"),
    "custom_properties": ("Custom document properties", "high"),
    "comments": ("Comments and reviewer information", "high"),
    "tracked_deletions": ("Deleted or moved text retained in the file", "high"),
    "tracked_insertions": ("Tracked changes and revision authors", "medium"),
    "hidden_text": ("Hidden Word text or styles", "high"),
    "hidden_sheets": ("Hidden Excel worksheets", "high"),
    "hidden_rows": ("Hidden Excel rows", "high"),
    "hidden_columns": ("Hidden Excel columns", "high"),
    "hidden_cells": ("Excel cells hidden by number formats", "high"),
    "external_links": ("External links or data connections", "high"),
    "embedded_content": ("Embedded objects, macros, or custom XML", "high"),
    "cached_content": ("Thumbnails or cached data", "medium"),
    "extra_document_content": ("Headers, footers, notes, images, or drawings", "medium"),
}


def enabled(node) -> bool:
    return node is not None and node.get(f"{{{NS['w']}}}val", "true").lower() not in ("0", "false", "off")


def zero(value):
    try:
        return value is not None and float(value) == 0
    except (ValueError, TypeError):
        return False


def hidden_cell_styles(package):
    root = package.trees.get("xl/styles.xml")
    if root is None:
        return set()
    formats = {n.get("numFmtId") for n in root.findall("x:numFmts/x:numFmt", NS)
               if (n.get("formatCode") or "").strip() == ";;;"}
    return {str(i) for i, xf in enumerate(root.findall("x:cellXfs/x:xf", NS)) if xf.get("numFmtId") in formats}


def inspect_package(package: Package, data: bytes) -> dict:
    counts: dict[str, int] = {}

    def add(code, count=1):
        if count:
            counts[code] = counts.get(code, 0) + count

    for name, root in package.trees.items():
        lower = name.lower()
        if lower == "docprops/core.xml":
            add("personal_metadata", sum(bool((n.text or "").strip()) for n in root
                                         if n.tag.rsplit("}", 1)[-1] not in ("created", "modified", "revision")))
        if lower == "docprops/custom.xml":
            add("custom_properties", len(root))
        if lower == "docprops/app.xml":
            add("personal_metadata", sum(bool((n.text or "").strip()) for n in root
                                         if n.tag.rsplit("}", 1)[-1] in ("Company", "Manager", "Template")))
        if "comment" in lower:
            add("comments", max(1, len(root)))
        if lower.endswith(".rels"):
            add("external_links", sum(r.get("TargetMode") == "External" for r in root))
        if lower.startswith("word/") and lower.endswith(".xml"):
            add("tracked_deletions", len(root.findall(".//w:del", NS)) + len(root.findall(".//w:moveFrom", NS)))
            add("tracked_insertions", len(root.findall(".//w:ins", NS)) + len(root.findall(".//w:moveTo", NS)))
            add("hidden_text", sum(enabled(n) for tag in ("vanish", "webHidden") for n in root.findall(f".//w:{tag}", NS)))
    for name in package.parts:
        lower = name.lower()
        if any(s in lower for s in ("vbaproject", "embeddings/", "customxml/", "activex/", "connections.xml", "externallinks/")):
            add("embedded_content")
        if "thumbnail" in lower or "pivotcache" in lower:
            add("cached_content")
        if (lower.startswith(("word/header", "word/footer", "word/footnote", "word/endnote"))
                or "/media/" in lower or "/drawings/" in lower):
            add("extra_document_content")
    visible_sheets = 0
    if package.kind == "xlsx":
        hidden_styles = hidden_cell_styles(package)
        for sheet, _, root in package.workbook_sheets():
            if sheet.get("state", "visible") != "visible":
                add("hidden_sheets")
                continue
            visible_sheets += 1
            add("hidden_rows", sum(r.get("hidden") in ("1", "true") or zero(r.get("ht"))
                                   for r in root.findall("x:sheetData/x:row", NS)))
            add("hidden_columns", sum(max(1, int(c.get("max", "1")) - int(c.get("min", "1")) + 1)
                                      for c in root.findall("x:cols/x:col", NS)
                                      if c.get("hidden") in ("1", "true") or zero(c.get("width"))))
            add("hidden_cells", sum(c.get("s") in hidden_styles for c in root.findall("x:sheetData/x:row/x:c", NS)))
    findings = [{"code": c, "title": LABELS[c][0], "severity": LABELS[c][1], "count": n}
                for c, n in sorted(counts.items())]
    return {
        "format": package.kind,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "status": "review_required" if findings else "no_detected_hidden_content",
        "finding_count": sum(counts.values()),
        "findings": findings,
        "visible_sheets": visible_sheets if package.kind == "xlsx" else None,
        "scope": "Structural privacy inspection. Visible text and cell values are not PII-redacted.",
        "sharing_copy": ("Visible worksheet values only; formulas become cached values."
                         if package.kind == "xlsx" else "Visible body text and tables; accepted insertions, omitted deletions."),
    }
