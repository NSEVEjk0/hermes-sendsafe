"""Privacy and failure contracts against real OOXML packages."""

import hashlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from sendsafe_core.archive import NS, Package, DocumentError, xml, MAX_FILE
from sendsafe_core.export import pack, node, serialized, excel_model, word_model
from sendsafe_core.fixtures import create_fixtures
from sendsafe_core.service import SendSafe


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fixtures = create_fixtures(self.root / "input")
        self.service = SendSafe(self.root / "exports", [self.root / "input"])

    def tearDown(self):
        self.tmp.cleanup()

    def mutate(self, kind, callback):
        path = Path(self.fixtures[kind])
        package = Package(path.read_bytes(), kind)
        callback(package.parts)
        path.write_bytes(pack(package.parts))
        return path

    def assert_code(self, code, call):
        with self.assertRaises(DocumentError) as caught:
            call()
        self.assertEqual(code, caught.exception.code)

    def export(self, kind):
        path = self.fixtures[kind]
        report = self.service.scan(path)
        return self.service.prepare(path, report["source_sha256"])

    def test_scan_never_returns_hidden_contents_or_authors(self):
        for kind in ("docx", "xlsx"):
            report = self.service.scan(self.fixtures[kind])
            rendered = json.dumps(report)
            self.assertNotIn("CANARY", rendered)
            self.assertNotIn("Private founder", rendered)
            self.assertNotIn("Private finance", rendered)
            self.assertEqual("review_required", report["status"])

    def test_xlsx_rebuild_removes_all_private_parts_and_unused_strings(self):
        result = self.export("xlsx")
        package = Package(Path(result["output_path"]).read_bytes(), "xlsx")
        self.assertNotIn(b"CANARY", b"".join(package.parts.values()))
        model = excel_model(package)
        self.assertEqual(["Client summary"], [s["name"] for s in model])
        values = {c["ref"]: c["value"] for c in model[0]["cells"]}
        self.assertEqual("16800", values["B5"])
        self.assertEqual("48000", values["B3"])
        self.assertNotIn("A9", values)
        self.assertNotIn("D4", values)
        self.assertNotIn("xl/sharedStrings.xml", package.parts)
        self.assertTrue(self.service.verify(result["export_id"])["verified"])

    def test_word_rebuild_preserves_insertions_and_removes_deleted_hidden_text(self):
        result = self.export("docx")
        package = Package(Path(result["output_path"]).read_bytes(), "docx")
        self.assertNotIn(b"CANARY", b"".join(package.parts.values()))
        text = "\n".join(b["text"] for b in word_model(package))
        self.assertIn("USD 12,000", text)
        self.assertIn("December 9, 2026", text)
        self.assertNotIn("word/comments.xml", package.parts)

    def test_sources_are_byte_identical_after_export(self):
        for kind in ("docx", "xlsx"):
            before = Path(self.fixtures[kind]).read_bytes()
            result = self.export(kind)
            self.assertEqual(before, Path(self.fixtures[kind]).read_bytes())
            self.assertNotEqual(Path(self.fixtures[kind]), Path(result["output_path"]))

    def test_changed_source_requires_rescan(self):
        path = Path(self.fixtures["xlsx"])
        report = self.service.scan(str(path))
        self.mutate("xlsx", lambda p: p.update({"docProps/private.txt": b"change"}))
        self.assert_code("stale_scan", lambda: self.service.prepare(str(path), report["source_sha256"]))
        self.assertFalse((self.root / "exports").exists())

    def test_missing_fingerprint_refused(self):
        self.assert_code("scan_required", lambda: self.service.prepare(self.fixtures["docx"], ""))

    def test_uncached_formula_refused_instead_of_inventing_value(self):
        def change(parts):
            root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
            cell = root.find('.//x:c[@r="B5"]', NS)
            cell.remove(cell.find("x:v", NS))
            parts["xl/worksheets/sheet1.xml"] = serialized(root)
        path = self.mutate("xlsx", change)
        report = self.service.scan(str(path))
        self.assert_code("uncached_formula", lambda: self.service.prepare(str(path), report["source_sha256"]))

    def test_tampered_export_refused(self):
        result = self.export("docx")
        path = Path(result["output_path"])
        package = Package(path.read_bytes(), "docx")
        package.parts["word/document.xml"] = package.parts["word/document.xml"].replace(b"12,000", b"99,000")
        path.write_bytes(pack(package.parts))
        self.assert_code("tampered_export", lambda: self.service.verify(result["export_id"]))

    def test_identifiers_cannot_traverse(self):
        self.assert_code("invalid_export", lambda: self.service.verify("../input"))

    def test_output_permissions_are_private(self):
        result = self.export("docx")
        if os.name == "posix":
            self.assertEqual(0o600, stat.S_IMODE(Path(result["output_path"]).stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(Path(result["output_path"]).parent.stat().st_mode))

    def test_each_export_is_a_new_copy(self):
        first, second = self.export("xlsx"), self.export("xlsx")
        self.assertNotEqual(first["output_path"], second["output_path"])
        self.assertEqual(first["output_sha256"], second["output_sha256"])

    def test_root_allowlist_refuses_other_files(self):
        outside = self.root / "outside.docx"
        outside.write_bytes(Path(self.fixtures["docx"]).read_bytes())
        self.assert_code("outside_roots", lambda: self.service.scan(str(outside)))

    def test_empty_allowlist_refuses_scanning(self):
        service = SendSafe(self.root / "exports", [])
        self.assert_code("outside_roots", lambda: service.scan(self.fixtures["docx"]))

    def test_symlink_input_refused(self):
        link = self.root / "input" / "link.docx"
        link.symlink_to(self.fixtures["docx"])
        self.assert_code("symlink", lambda: self.service.scan(str(link)))

    def test_oversized_input_refused_before_parsing(self):
        path = self.root / "input" / "large.docx"
        with path.open("wb") as file:
            file.truncate(MAX_FILE + 1)
        self.assert_code("file_limit", lambda: self.service.scan(str(path)))

    def test_wrong_file_type_refused(self):
        self.assert_code("unsupported_format", lambda: self.service.scan(str(self.root / "file.docm")))

    def test_nonzip_office_file_refused(self):
        path = self.root / "input" / "bad.docx"; path.write_text("not a zip")
        self.assert_code("invalid_archive", lambda: self.service.scan(str(path)))

    def test_missing_main_part_refused(self):
        path = self.mutate("docx", lambda p: p.pop("word/document.xml"))
        self.assert_code("invalid_document", lambda: self.service.scan(str(path)))

    def test_archive_traversal_refused(self):
        path = self.mutate("docx", lambda p: p.update({"../private.xml": b"<x/>"}))
        self.assert_code("archive_path", lambda: self.service.scan(str(path)))

    def test_encoded_archive_traversal_refused(self):
        path = self.mutate("docx", lambda p: p.update({"%2e%2e/private.xml": b"<x/>"}))
        self.assert_code("archive_path", lambda: self.service.scan(str(path)))

    def test_case_alias_archive_parts_refused(self):
        path = self.mutate("docx", lambda p: p.update({"WORD/DOCUMENT.XML": b"<x/>"}))
        self.assert_code("duplicate_part", lambda: self.service.scan(str(path)))

    def test_xml_entities_utf8_refused(self):
        self.assert_code("xml_entities", lambda: xml(b'<!DOCTYPE x [<!ENTITY hidden "secret">]><x>&hidden;</x>'))

    def test_xml_entities_utf16_refused(self):
        self.assert_code("xml_entities", lambda: xml('<!DOCTYPE x [<!ENTITY hidden "secret">]><x>&hidden;</x>'.encode("utf-16")))

    def test_malformed_xml_refused(self):
        self.assert_code("malformed_xml", lambda: xml(b"<x>"))

    def test_xml_depth_limit(self):
        self.assert_code("xml_limit", lambda: xml(("<x>" * 100 + "</x>" * 100).encode()))

    def test_missing_relationship_target_refused(self):
        path = self.mutate("xlsx", lambda p: p.pop("xl/worksheets/sheet2.xml"))
        self.assert_code("missing_relationship", lambda: self.service.scan(str(path)))

    def test_hidden_default_word_style_is_removed(self):
        def change(parts):
            w = NS["w"]
            styles = node(w, "styles"); style = node(w, "style", {f"{{{w}}}type": "paragraph", f"{{{w}}}default": "1", f"{{{w}}}styleId": "HiddenDefault"})
            props = node(w, "rPr"); props.append(node(w, "vanish")); style.append(props); styles.append(style)
            parts["word/styles.xml"] = serialized(styles)
        path = self.mutate("docx", change)
        report = self.service.scan(str(path))
        self.assert_code("empty_export", lambda: self.service.prepare(str(path), report["source_sha256"]))

    def test_number_format_hidden_values_are_omitted(self):
        def change(parts):
            x = NS["x"]
            root = node(x, "styleSheet"); formats = node(x, "numFmts"); formats.append(node(x, "numFmt", {"numFmtId": "164", "formatCode": ";;;"})); root.append(formats)
            xfs = node(x, "cellXfs"); xfs.append(node(x, "xf", {"numFmtId": "0"})); xfs.append(node(x, "xf", {"numFmtId": "164"})); root.append(xfs)
            parts["xl/styles.xml"] = serialized(root)
            sheet = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
            sheet.find('.//x:c[@r="B3"]', NS).set("s", "1")
            parts["xl/worksheets/sheet1.xml"] = serialized(sheet)
        path = self.mutate("xlsx", change)
        report = self.service.scan(str(path))
        self.assertIn("hidden_cells", [f["code"] for f in report["findings"]])
        result = self.service.prepare(str(path), report["source_sha256"])
        output = Package(Path(result["output_path"]).read_bytes(), "xlsx")
        self.assertNotIn("B3", [c["ref"] for c in excel_model(output)[0]["cells"]])

    def test_zero_height_rows_are_omitted(self):
        def change(parts):
            root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
            row = root.find('.//x:row[@r="9"]', NS); row.attrib.pop("hidden"); row.set("ht", "0.0")
            parts["xl/worksheets/sheet1.xml"] = serialized(root)
        self.mutate("xlsx", change)
        self.test_xlsx_rebuild_removes_all_private_parts_and_unused_strings()

    def test_receipt_filename_traversal_refused(self):
        result = self.export("docx"); path = Path(result["receipt_path"])
        receipt = json.loads(path.read_text()); receipt["output_name"] = "../input.docx"; path.write_text(json.dumps(receipt))
        self.assert_code("invalid_receipt", lambda: self.service.verify(result["export_id"]))


if __name__ == "__main__":
    unittest.main()
