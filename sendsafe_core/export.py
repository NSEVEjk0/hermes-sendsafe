"""Rebuild sharing copies from visible content; never scrub the original ZIP in place."""

from __future__ import annotations

import io
import json
import re
import hashlib
import zipfile
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from .archive import NS, Package, DocumentError
from .inspection import enabled, zero, hidden_cell_styles

W = NS["w"]
X = NS["x"]
R = NS["r"]
XML = "http://www.w3.org/XML/1998/namespace"


def node(namespace, name, attrs=None, text=None):
    result = ET.Element(f"{{{namespace}}}{name}", attrs or {})
    result.text = text
    return result


def serialized(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def rels(items):
    root = node(NS["pr"], "Relationships")
    for rid, kind, target in items:
        root.append(node(NS["pr"], "Relationship", {"Id": rid, "Type": R + "/" + kind, "Target": target}))
    return serialized(root)


def types(items):
    root = node(NS["ct"], "Types")
    root.append(node(NS["ct"], "Default", {"Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"}))
    root.append(node(NS["ct"], "Default", {"Extension": "xml", "ContentType": "application/xml"}))
    for path, kind in items:
        root.append(node(NS["ct"], "Override", {"PartName": "/" + path, "ContentType": "application/vnd.openxmlformats-officedocument." + kind}))
    return serialized(root)


def pack(parts):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, content in sorted(parts.items()):
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)
    return result.getvalue()


def _styles(package):
    root = package.trees.get("word/styles.xml")
    mapping = {}
    default = False
    if root is not None:
        for style in root.findall("w:style", NS):
            mapping[style.get(f"{{{W}}}styleId")] = style
        defaults = root.find("w:docDefaults/w:rPrDefault/w:rPr", NS)
        if defaults is not None:
            default = any(enabled(defaults.find(f"w:{tag}", NS)) for tag in ("vanish", "webHidden"))
        for style in root.findall("w:style", NS):
            if style.get(f"{{{W}}}type") == "paragraph" and style.get(f"{{{W}}}default") in ("1", "true"):
                default = _hidden(style.get(f"{{{W}}}styleId"), mapping, default)
    return mapping, default


def _hidden(style_id, styles, base=False, seen=None):
    if style_id not in styles:
        return base
    seen = set() if seen is None else seen
    if style_id in seen:
        raise DocumentError("style_cycle", "The document contains cyclic style inheritance.")
    seen.add(style_id)
    style = styles[style_id]
    parent = style.find("w:basedOn", NS)
    inherited = _hidden(parent.get(f"{{{W}}}val"), styles, base, seen) if parent is not None else base
    return _direct_hidden(style.find("w:rPr", NS), inherited)


def _direct_hidden(properties, base):
    if properties is None:
        return base
    flags = [properties.find(f"w:{tag}", NS) for tag in ("vanish", "webHidden")]
    present = [flag for flag in flags if flag is not None]
    return any(enabled(flag) for flag in present) if present else base


def word_text(root, styles, default=False):
    def walk(element, inherited=False):
        local = element.tag.rsplit("}", 1)[-1]
        if local in ("del", "moveFrom", "delText", "instrText", "rPr", "pPr", "sectPr",
                     "drawing", "pict", "object", "txbxContent", "AlternateContent"):
            return ""
        if local == "p":
            style = element.find("w:pPr/w:pStyle", NS)
            inherited = _hidden(style.get(f"{{{W}}}val"), styles, default) if style is not None else default
            inherited = _direct_hidden(element.find("w:pPr/w:rPr", NS), inherited)
        if local == "r":
            style = element.find("w:rPr/w:rStyle", NS)
            if style is not None:
                inherited = _hidden(style.get(f"{{{W}}}val"), styles, inherited)
            inherited = _direct_hidden(element.find("w:rPr", NS), inherited)
        if inherited:
            return ""
        if local == "t":
            return element.text or ""
        if local == "tab":
            return "\t"
        if local in ("br", "cr"):
            return "\n"
        return "".join(walk(child, inherited) for child in element)
    return walk(root)


def word_model(package):
    body = package.trees["word/document.xml"].find("w:body", NS)
    if body is None:
        raise DocumentError("invalid_document", "The Word document has no body.")
    styles, default = _styles(package)
    blocks = []

    def visit(element):
        local = element.tag.rsplit("}", 1)[-1]
        if local in ("del", "moveFrom"):
            return
        if local == "p":
            text = word_text(element, styles, default)
            if text:
                blocks.append({"type": "paragraph", "text": text})
        elif local == "tbl":
            rows = []
            for row in element.findall("w:tr", NS):
                if row.find("w:trPr/w:del", NS) is not None:
                    continue
                cells = ["\n".join(word_text(p, styles, default) for p in cell.findall("w:p", NS))
                         for cell in row.findall("w:tc", NS)
                         if cell.find("w:tcPr/w:cellDel", NS) is None]
                if cells:
                    rows.append(cells)
            if rows:
                blocks.append({"type": "table", "rows": rows})
        else:
            for child in element:
                visit(child)
    visit(body)
    if not blocks:
        raise DocumentError("empty_export", "No supported visible body text or tables remain to export.")
    return blocks


def _paragraph(text):
    paragraph = node(W, "p")
    for index, line in enumerate(text.split("\n")):
        if index:
            run = node(W, "r")
            run.append(node(W, "br"))
            paragraph.append(run)
        pieces = line.split("\t")
        for i, piece in enumerate(pieces):
            run = node(W, "r")
            if i:
                run.append(node(W, "tab"))
            run.append(node(W, "t", {f"{{{XML}}}space": "preserve"}, piece))
            paragraph.append(run)
    return paragraph


def word_export(package):
    model = word_model(package)
    document = node(W, "document")
    body = node(W, "body")
    document.append(body)
    for block in model:
        if block["type"] == "paragraph":
            body.append(_paragraph(block["text"]))
        else:
            table = node(W, "tbl")
            for cells in block["rows"]:
                row = node(W, "tr")
                for text in cells:
                    cell = node(W, "tc")
                    cell.append(_paragraph(text))
                    row.append(cell)
                table.append(row)
            body.append(table)
    body.append(node(W, "sectPr"))
    parts = {
        "[Content_Types].xml": types([("word/document.xml", "wordprocessingml.document.main+xml")]),
        "_rels/.rels": rels([("rId1", "officeDocument", "word/document.xml")]),
        "word/document.xml": serialized(document),
    }
    return pack(parts), model, {"mode": "visible_text_snapshot", "blocks": len(model),
                               "omitted": "Comments, revisions, hidden text, metadata, images, headers, footers, notes, embedded objects and original formatting."}


def _column(ref):
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", ref or "")
    if not match:
        raise DocumentError("invalid_cell", "A worksheet has an invalid cell reference.")
    col = 0
    for letter in match[1]:
        col = col * 26 + ord(letter) - 64
    if col > 16384 or int(match[2]) > 1048576:
        raise DocumentError("invalid_cell", "A cell exceeds Excel's worksheet limits.")
    return col, int(match[2])


def _shared_strings(package):
    root = package.trees.get("xl/sharedStrings.xml")
    return ["".join(n.text or "" for n in si.findall(".//x:t", NS)) for si in root.findall("x:si", NS)] if root is not None else []


def _cell_value(cell, strings):
    value = cell.find("x:v", NS)
    formula = cell.find("x:f", NS)
    if formula is not None and (value is None or value.text is None):
        raise DocumentError("uncached_formula", "A visible formula has no cached result. Open and save the workbook in Excel or LibreOffice, then scan again.")
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        return "text", "".join(n.text or "" for n in cell.findall("x:is//x:t", NS))
    if value is None or value.text is None:
        return None
    text = value.text
    if kind == "s":
        try:
            index = int(text)
            if index < 0:
                raise ValueError
            return "text", strings[index]
        except (ValueError, IndexError):
            raise DocumentError("invalid_string", "A worksheet has an invalid shared-string reference.") from None
    if kind in ("str", "d"):
        return "text", text
    if kind == "b":
        if text not in ("0", "1"):
            raise DocumentError("invalid_cell", "A worksheet contains an invalid boolean.")
        return "boolean", text
    if kind == "e":
        if text not in ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA", "#SPILL!", "#CALC!"):
            raise DocumentError("invalid_cell", "A worksheet contains an unsupported Excel error value.")
        return "error", text
    if kind != "n":
        raise DocumentError("invalid_cell", "A worksheet contains an unsupported cell type.")
    try:
        number = Decimal(text)
        if not number.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        raise DocumentError("invalid_cell", "A worksheet contains an invalid numeric value.") from None
    return "number", text


def excel_model(package):
    strings = _shared_strings(package)
    hidden_styles = hidden_cell_styles(package)
    result = []
    for sheet, _, root in package.workbook_sheets():
        if sheet.get("state", "visible") != "visible":
            continue
        name = sheet.get("name", "")
        if not name or len(name) > 31 or re.search(r"[\\/*?:\[\]]", name):
            raise DocumentError("invalid_sheet", "A visible worksheet has an invalid name.")
        hidden = []
        for col in root.findall("x:cols/x:col", NS):
            if col.get("hidden") in ("1", "true") or zero(col.get("width")):
                try:
                    start, end = int(col.get("min", "0")), int(col.get("max", "0"))
                except ValueError:
                    raise DocumentError("invalid_column", "A worksheet column range is invalid.") from None
                if not 1 <= start <= end <= 16384:
                    raise DocumentError("invalid_column", "A worksheet column range is invalid.")
                hidden.append((start, end))
        cells = []
        seen = set()
        for row in root.findall("x:sheetData/x:row", NS):
            if row.get("hidden") in ("1", "true") or zero(row.get("ht")):
                continue
            for cell in row.findall("x:c", NS):
                if cell.get("s") in hidden_styles:
                    continue
                ref = cell.get("r", "")
                col, row_index = _column(ref)
                if ref in seen:
                    raise DocumentError("duplicate_cell", "A worksheet contains duplicate cell references.")
                seen.add(ref)
                if any(start <= col <= end for start, end in hidden):
                    continue
                value = _cell_value(cell, strings)
                if value is not None:
                    cells.append({"ref": ref, "kind": value[0], "value": value[1]})
        result.append({"name": name, "cells": cells})
    if not result or not any(sheet["cells"] for sheet in result):
        raise DocumentError("empty_export", "No supported visible worksheet values remain to export.")
    if len({s["name"].casefold() for s in result}) != len(result):
        raise DocumentError("invalid_sheet", "Worksheet names must be unique.")
    return result


def excel_export(package):
    model = excel_model(package)
    workbook = node(X, "workbook")
    sheets = node(X, "sheets")
    workbook.append(sheets)
    entries = []
    content_types = [("xl/workbook.xml", "spreadsheetml.sheet.main+xml")]
    parts = {"_rels/.rels": rels([("rId1", "officeDocument", "xl/workbook.xml")])}
    for index, sheet in enumerate(model, 1):
        rid = f"rId{index}"
        sheets.append(node(X, "sheet", {"name": sheet["name"], "sheetId": str(index), f"{{{R}}}id": rid}))
        path = f"xl/worksheets/sheet{index}.xml"
        entries.append((rid, "worksheet", f"worksheets/sheet{index}.xml"))
        content_types.append((path, "spreadsheetml.worksheet+xml"))
        worksheet = node(X, "worksheet")
        data = node(X, "sheetData")
        worksheet.append(data)
        rows = {}
        for item in sheet["cells"]:
            _, row_index = _column(item["ref"])
            if row_index not in rows:
                rows[row_index] = node(X, "row", {"r": str(row_index)})
                data.append(rows[row_index])
            kind = item["kind"]
            attrs = {"r": item["ref"]}
            if kind == "text":
                attrs["t"] = "inlineStr"
                cell = node(X, "c", attrs)
                string = node(X, "is")
                string.append(node(X, "t", {f"{{{XML}}}space": "preserve"}, item["value"]))
                cell.append(string)
            else:
                attrs["t"] = {"boolean": "b", "error": "e", "number": "n"}[kind]
                cell = node(X, "c", attrs)
                cell.append(node(X, "v", text=item["value"]))
            rows[row_index].append(cell)
        parts[path] = serialized(worksheet)
    parts["xl/workbook.xml"] = serialized(workbook)
    parts["xl/_rels/workbook.xml.rels"] = rels(entries)
    parts["[Content_Types].xml"] = types(content_types)
    return pack(parts), model, {"mode": "visible_values_snapshot", "sheets": len(model),
                               "cells": sum(len(s["cells"]) for s in model),
                               "omitted": "Hidden sheets/rows/columns, formulas, formatting, comments, shared-string leftovers, links, cached data, images and metadata. Dates stored as serial numbers retain their numeric value."}


def content_hash(model):
    return hashlib.sha256(json.dumps(model, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sharing_copy(package):
    output, expected, preservation = word_export(package) if package.kind == "docx" else excel_export(package)
    rebuilt = Package(output, package.kind)
    actual = word_model(rebuilt) if package.kind == "docx" else excel_model(rebuilt)
    if expected != actual:
        raise DocumentError("verification_failed", "The sharing copy did not preserve the selected visible content.")
    return output, preservation, content_hash(actual)

