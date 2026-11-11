"""Fictional, reviewable demonstration documents; never uses user documents."""

from pathlib import Path
from xml.etree import ElementTree as ET
from .archive import NS
from .export import node, pack, serialized, rels, types


def create_fixtures(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    w, x, r = NS["w"], NS["x"], NS["r"]
    workbook = node(x, "workbook")
    sheets = node(x, "sheets")
    workbook.append(sheets)
    for index, (name, state) in enumerate((("Client summary", "visible"), ("Internal payroll", "veryHidden")), 1):
        sheets.append(node(x, "sheet", {"name": name, "sheetId": str(index), "state": state, f"{{{r}}}id": f"rId{index}"}))
    public = node(x, "worksheet")
    columns = node(x, "cols")
    columns.append(node(x, "col", {"min": "4", "max": "4", "hidden": "1"}))
    public.append(columns)
    data = node(x, "sheetData")
    public.append(data)

    def row(index, items, hidden=False):
        row_node = node(x, "row", {"r": str(index), **({"hidden": "1"} if hidden else {})})
        for ref, kind, value, formula in items:
            cell = node(x, "c", {"r": ref, "t": kind})
            if formula:
                cell.append(node(x, "f", text=formula))
            if kind == "inlineStr":
                string = node(x, "is"); string.append(node(x, "t", text=value)); cell.append(string)
            else:
                cell.append(node(x, "v", text=value))
            row_node.append(cell)
        data.append(row_node)
    row(1, [("A1", "inlineStr", "NORTHSTAR STUDIO · CLIENT SUMMARY", None)])
    row(2, [("A2", "inlineStr", "Quarter", None), ("B2", "inlineStr", "Q4 2026", None)])
    row(3, [("A3", "inlineStr", "Revenue (USD)", None), ("B3", "n", "48000", None)])
    row(4, [("A4", "inlineStr", "Expenses (USD)", None), ("B4", "n", "31200", None), ("D4", "s", "0", None)])
    row(5, [("A5", "inlineStr", "Profit (USD)", None), ("B5", "n", "16800", "B3-B4")])
    row(9, [("A9", "inlineStr", "PRIVATE_DISCOUNT_CANARY_7391", None)], True)
    private = node(x, "worksheet")
    private_data = node(x, "sheetData"); private.append(private_data)
    secret_row = node(x, "row", {"r": "1"}); private_data.append(secret_row)
    secret_cell = node(x, "c", {"r": "A1", "t": "s"}); secret_cell.append(node(x, "v", text="0")); secret_row.append(secret_cell)
    strings = node(x, "sst", {"count": "2", "uniqueCount": "2"})
    for value in ("PRIVATE_SALARY_CANARY_7391", "UNUSED_PRIVATE_STRING_CANARY_7391"):
        si = node(x, "si"); si.append(node(x, "t", text=value)); strings.append(si)
    comments = node(x, "comments")
    authors = node(x, "authors"); authors.append(node(x, "author", text="Private finance reviewer")); comments.append(authors)
    comment_list = node(x, "commentList"); comments.append(comment_list)
    comment = node(x, "comment", {"ref": "A1", "authorId": "0"}); text_node = node(x, "text"); text_node.append(node(x, "t", text="PRIVATE_COMMENT_CANARY_7391")); comment.append(text_node); comment_list.append(comment)
    core = ET.fromstring('<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Private founder</dc:creator><cp:lastModifiedBy>Private finance lead</cp:lastModifiedBy></cp:coreProperties>')
    parts = {
        "[Content_Types].xml": types([("xl/workbook.xml", "spreadsheetml.sheet.main+xml"), ("xl/worksheets/sheet1.xml", "spreadsheetml.worksheet+xml"), ("xl/worksheets/sheet2.xml", "spreadsheetml.worksheet+xml"), ("xl/sharedStrings.xml", "spreadsheetml.sharedStrings+xml"), ("xl/comments/comment1.xml", "spreadsheetml.comments+xml")]),
        "_rels/.rels": rels([("rId1", "officeDocument", "xl/workbook.xml")]),
        "xl/workbook.xml": serialized(workbook),
        "xl/_rels/workbook.xml.rels": rels([("rId1", "worksheet", "worksheets/sheet1.xml"), ("rId2", "worksheet", "worksheets/sheet2.xml"), ("rId3", "sharedStrings", "sharedStrings.xml")]),
        "xl/worksheets/sheet1.xml": serialized(public), "xl/worksheets/sheet2.xml": serialized(private),
        "xl/sharedStrings.xml": serialized(strings), "xl/comments/comment1.xml": serialized(comments),
        "docProps/core.xml": serialized(core),
    }
    spreadsheet = folder / "northstar-client-summary.xlsx"
    spreadsheet.write_bytes(pack(parts))
    document = node(w, "document"); body = node(w, "body"); document.append(body)
    def paragraph(text):
        p = node(w, "p"); run = node(w, "r"); run.append(node(w, "t", text=text)); p.append(run); body.append(p); return p
    paragraph("NORTHSTAR STUDIO — CLIENT PROPOSAL")
    paragraph("We propose a four-week project for USD 12,000.")
    p = paragraph("The agreed delivery date is ")
    deleted = node(w, "del", {f"{{{w}}}author": "Private reviewer"}); run = node(w, "r"); run.append(node(w, "delText", text="PRIVATE_OLD_PRICE_CANARY_7391")); deleted.append(run); p.append(deleted)
    inserted = node(w, "ins", {f"{{{w}}}author": "Private reviewer"}); run = node(w, "r"); run.append(node(w, "t", text="December 9, 2026.")); inserted.append(run); p.append(inserted)
    p = node(w, "p"); run = node(w, "r"); properties = node(w, "rPr"); properties.append(node(w, "vanish")); run.append(properties); run.append(node(w, "t", text="PRIVATE_BOARD_NOTE_CANARY_7391")); p.append(run); body.append(p)
    body.append(node(w, "sectPr"))
    word_comments = node(w, "comments"); comment = node(w, "comment", {f"{{{w}}}id": "0", f"{{{w}}}author": "Private reviewer"}); cp = node(w, "p"); cr = node(w, "r"); cr.append(node(w, "t", text="PRIVATE_NEGOTIATION_CANARY_7391")); cp.append(cr); comment.append(cp); word_comments.append(comment)
    doc = folder / "northstar-client-proposal.docx"
    doc.write_bytes(pack({"[Content_Types].xml": types([("word/document.xml", "wordprocessingml.document.main+xml")]),
                          "_rels/.rels": rels([("rId1", "officeDocument", "word/document.xml")]),
                          "word/document.xml": serialized(document), "word/comments.xml": serialized(word_comments),
                          "docProps/core.xml": serialized(core)}))
    return {"xlsx": str(spreadsheet), "docx": str(doc)}

