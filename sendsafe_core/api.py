"""Shared UI operations. Used by the native backend and local preview."""

import argparse
import base64
import json
import os
import re
import secrets
import sys
from pathlib import Path
from .archive import MAX_FILE, DocumentError, Package
from .fixtures import create_fixtures
from .export import excel_model, word_model
from .service import SendSafe


def dispatch(storage, roots, action, body):
    data = Path(storage)
    allowed = list(roots) + [str(data / "inputs"), str(data / "demo"), str(data / "exports")]
    service = SendSafe(data / "exports", allowed)
    if action == "demo":
        return {"ok": True, "files": [{"name": Path(p).name, "path": p, "kind": kind}
                                      for kind, p in create_fixtures(data / "demo").items()], "fictional": True}
    if action == "files":
        files = []
        for root in allowed[:-1]:
            directory = Path(root).expanduser()
            if directory.is_dir():
                for path in sorted(directory.iterdir()):
                    if path.is_file() and not path.is_symlink() and path.suffix.lower() in (".docx", ".xlsx"):
                        files.append({"name": path.name, "path": str(path.absolute()), "kind": path.suffix[1:]})
                        if len(files) >= 100:
                            break
        return {"ok": True, "files": files[:100], "allowed_folders": list(roots)}
    if action == "upload":
        name, encoded = body.get("name", ""), body.get("data", "")
        if not isinstance(name, str) or not isinstance(encoded, str) or len(encoded) > (MAX_FILE * 4 // 3 + 8):
            raise DocumentError("upload_limit", "Select a Word or Excel document under 20 MiB.")
        kind = Path(name).suffix.lower()[1:]
        if kind not in ("docx", "xlsx"):
            raise DocumentError("unsupported_format", "Only .docx and .xlsx uploads are supported.")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise DocumentError("invalid_upload", "The document upload is invalid.") from None
        if len(content) > MAX_FILE:
            raise DocumentError("upload_limit", "The document exceeds the 20 MiB input limit.")
        Package(content, kind)
        folder = data / "inputs"
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", Path(name).stem)[:70].strip(".-") or "document"
        path = folder / (safe_name + "-" + secrets.token_hex(6) + "." + kind)
        SendSafe._exclusive_write(path, content)
        return {"ok": True, "file": {"name": name, "path": str(path), "kind": kind}, "report": service.scan(str(path))}
    if action == "scan":
        return service.scan(body.get("path", ""))
    if action == "prepare":
        return service.prepare(body.get("path", ""), body.get("expected_sha256", ""))
    if action == "verify":
        return service.verify(body.get("export_id", ""))
    if action == "preview":
        result = service.verify(body.get("export_id", ""))
        path = Path(result["output_path"])
        package = Package(path.read_bytes(), path.suffix[1:])
        model = excel_model(package) if package.kind == "xlsx" else word_model(package)
        if package.kind == "xlsx":
            model = [
                {"name": sheet["name"], "cells": [
                    {**cell, "value": cell["value"][:300]}
                    for cell in sheet["cells"][:100]
                ]}
                for sheet in model[:10]
            ]
        else:
            model = [{**b, **({"text": b["text"][:2000]} if b["type"] == "paragraph" else {"rows": [[c[:300] for c in row[:20]] for row in b["rows"][:20]]})} for b in model[:30]]
        return {"ok": True, "kind": package.kind, "content": model}
    raise DocumentError("invalid_action", "This operation is not supported.")


def guarded_dispatch(storage, roots, action, body):
    try:
        return dispatch(storage, roots, action, body)
    except DocumentError as error:
        return {"ok": False, "error": {"code": error.code, "message": str(error)}}
    except (OSError, ValueError, TypeError, KeyError):
        return {"ok": False, "error": {"code": "processing_failed", "message": "The document operation failed. No file is approved for sharing."}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage", required=True)
    parser.add_argument("--roots", default="[]")
    args = parser.parse_args()
    text = sys.stdin.read(MAX_FILE * 4 // 3 + 20_000)
    payload = json.loads(text)
    print(json.dumps(guarded_dispatch(args.storage, json.loads(args.roots), payload["action"], payload.get("body") or {}), ensure_ascii=False))


if __name__ == "__main__":
    main()
