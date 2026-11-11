"""Content-bound exports and receipts, with private, non-overwriting storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from .archive import Package, DocumentError, bounded_read
from .inspection import inspect_package
from .export import sharing_copy


class SendSafe:
    def __init__(self, storage: str | Path, roots: list[str | Path] | None = None):
        self.storage = Path(storage)
        self.roots = roots

    def scan(self, source: str) -> dict:
        path, data = bounded_read(source, self.roots)
        package = Package(data, path.suffix.lower()[1:])
        return {"ok": True, "source_name": path.name, "source_path": str(path), **inspect_package(package, data)}

    def prepare(self, source: str, expected_sha256: str) -> dict:
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise DocumentError("scan_required", "Scan this document first and supply the exact source_sha256 from that scan.")
        path, data = bounded_read(source, self.roots)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise DocumentError("stale_scan", "The document changed after your scan. Scan it again before making a sharing copy.")
        package = Package(data, path.suffix.lower()[1:])
        before = inspect_package(package, data)
        output, preservation, content_sha256 = sharing_copy(package)
        after = inspect_package(Package(output, package.kind), output)
        if after["findings"]:
            raise DocumentError("verification_failed", "The rebuilt sharing copy still contains a detected structural risk.")
        _, current = bounded_read(path, self.roots)
        if hashlib.sha256(current).hexdigest() != expected_sha256:
            raise DocumentError("stale_scan", "The source changed during export. Scan it again.")
        self.storage.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.storage.is_symlink():
            raise DocumentError("storage_symlink", "The export directory cannot be a symlink.")
        export_id = secrets.token_hex(16)
        stem = re.sub(r"[^A-Za-z0-9._-]", "-", path.stem)[:70].strip(".-") or "document"
        name = f"{stem}.sendsafe-{export_id[:8]}{path.suffix.lower()}"
        folder = self.storage / export_id
        folder.mkdir(mode=0o700)
        destination = folder / name
        receipt = {
            "schema": "sendsafe.receipt.v1", "export_id": export_id,
            "source_name": path.name, "source_sha256": expected_sha256,
            "output_name": name, "output_sha256": after["source_sha256"],
            "output_bytes": len(output), "visible_content_sha256": content_sha256,
            "verified": True, "content_verified": True,
            "removed_findings": before["findings"], "remaining_findings": [],
            "preservation": preservation,
            "scope": "Structural privacy cleanup. Visible content is preserved as a snapshot, not PII-redacted. Inspect the copy before sharing.",
        }
        self._exclusive_write(destination, output)
        self._exclusive_write(folder / "receipt.json", json.dumps(receipt, indent=2, ensure_ascii=False).encode())
        return {"ok": True, **receipt, "output_path": str(destination), "receipt_path": str(folder / "receipt.json")}

    @staticmethod
    def _exclusive_write(path, data):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())

    def verify(self, export_id: str) -> dict:
        if not isinstance(export_id, str) or not re.fullmatch(r"[0-9a-f]{32}", export_id):
            raise DocumentError("invalid_export", "The export identifier is invalid.")
        folder = self.storage / export_id
        if folder.is_symlink() or (folder / "receipt.json").is_symlink():
            raise DocumentError("invalid_export", "Export symlinks are refused.")
        try:
            receipt_file = folder / "receipt.json"
            if receipt_file.stat().st_size > 100_000:
                raise DocumentError("invalid_receipt", "The export receipt is too large.")
            receipt = json.loads(receipt_file.read_text())
            name = receipt["output_name"]
            if not isinstance(name, str) or Path(name).name != name:
                raise DocumentError("invalid_receipt", "The export receipt has an invalid file name.")
            path, data = bounded_read(folder / name, [folder])
        except DocumentError:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            raise DocumentError("invalid_receipt", "The export receipt is missing or invalid.") from None
        if receipt.get("export_id") != export_id or receipt.get("output_sha256") != hashlib.sha256(data).hexdigest():
            raise DocumentError("tampered_export", "This sharing copy has changed since verification. Create a fresh copy.")
        from .export import word_model, excel_model, content_hash
        package = Package(data, path.suffix.lower()[1:])
        model = word_model(package) if package.kind == "docx" else excel_model(package)
        if content_hash(model) != receipt.get("visible_content_sha256"):
            raise DocumentError("tampered_export", "The sharing copy does not match its visible-content receipt.")
        scan = inspect_package(package, data)
        if scan["findings"]:
            raise DocumentError("tampered_export", "The sharing copy contains structural risks.")
        return {"ok": True, "export_id": export_id, "verified": True,
                "output_path": str(path), "output_sha256": receipt["output_sha256"], "remaining_findings": []}
