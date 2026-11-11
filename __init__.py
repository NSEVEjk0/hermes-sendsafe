"""Hermes wiring; all document processing is in the stateless local engine."""

import json
from pathlib import Path
from .sendsafe_core.archive import DocumentError
from .sendsafe_core.service import SendSafe


def _schema(name, description, properties, required):
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}


SCHEMAS = [
    _schema("sendsafe_inspect", "Inspect a local .docx or .xlsx for hidden sheets, rows, columns, comments, tracked deletions, metadata and embedded content. Returns counts, never hidden contents. Scan before preparing a sharing copy. Visible content is not PII-redacted.",
            {"path": {"type": "string", "description": "Document path inside a user-allowed folder."}}, ["path"]),
    _schema("sendsafe_prepare", "Create a separate verified sharing snapshot from an inspected Word or Excel document. Requires the exact scan fingerprint; changed files are refused. Excel keeps visible values and cached formula results. Word keeps visible body text and tables. Original formatting and images are omitted. Never sends the file.",
            {"path": {"type": "string"}, "expected_sha256": {"type": "string", "description": "Exact source_sha256 returned by sendsafe_inspect."}}, ["path", "expected_sha256"]),
    _schema("sendsafe_verify", "Recheck an existing SendSafe sharing copy against its receipt and rescan its structure. Refuses a changed file. This verifies structural cleanup, not that visible content contains no personal information.",
            {"export_id": {"type": "string", "description": "Export identifier returned by sendsafe_prepare."}}, ["export_id"]),
]


def service_for_context(ctx):
    from plugins.plugin_storage import plugin_data_dir
    roots = ctx.get_config("allowed_roots", default=[])
    if not isinstance(roots, list) or any(not isinstance(r, str) or not r.strip() for r in roots):
        raise DocumentError("configuration", "Allowed folders must be a list of non-empty folder paths.")
    data = plugin_data_dir("sendsafe")
    return SendSafe(data / "exports", roots + [str(data / "inputs"), str(data / "demo"), str(data / "exports")])


def _handler(ctx, method, args):
    try:
        service = service_for_context(ctx)
        if method == "scan":
            result = service.scan(args.get("path", ""))
        elif method == "prepare":
            result = service.prepare(args.get("path", ""), args.get("expected_sha256", ""))
        else:
            result = service.verify(args.get("export_id", ""))
        return json.dumps(result, ensure_ascii=False)
    except DocumentError as error:
        return json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}})
    except (OSError, ValueError, TypeError, KeyError):
        return json.dumps({"ok": False, "error": {"code": "processing_failed", "message": "The document could not be processed. No sharing copy was approved."}})


def before_read(ctx, **kwargs):
    if kwargs.get("tool_name") != "read_file" or not ctx.get_config("guard_reads", default=True):
        return None
    args = kwargs.get("args") or {}
    source = args.get("path") or args.get("file_path")
    if not isinstance(source, str) or Path(source).suffix.lower() not in (".docx", ".xlsx", ".docm", ".xlsm"):
        return None
    try:
        service = service_for_context(ctx)
        resolved = Path(source).expanduser().resolve()
        protected = any(resolved.is_relative_to(Path(r).expanduser().resolve()) for r in service.roots)
        if not protected:
            return None
        report = service.scan(source)
        if report["findings"]:
            summary = ", ".join(f"{f['title']} ({f['count']})" for f in report["findings"])
            return {"action": "block", "message": "SendSafe stopped this document before read_file could expose its hidden content: " + summary + ". Use sendsafe_inspect, then ask the user whether to prepare a visible-content sharing snapshot. The original was not read by the tool."}
        return None
    except DocumentError as error:
        return {"action": "block", "message": "SendSafe could not establish that this protected document is readable: " + str(error)}
    except (OSError, ValueError, TypeError, RuntimeError):
        return {"action": "block", "message": "SendSafe's inspection failed. This document read is blocked; fix the scan or allowed-folder configuration first."}


def register(ctx):
    for schema, method in zip(SCHEMAS, ("scan", "prepare", "verify")):
        def handler(args, _method=method, **kwargs):
            return _handler(ctx, _method, args)
        ctx.register_tool(name=schema["name"], toolset="sendsafe", schema=schema, handler=handler, check_fn=lambda: True)
    ctx.register_hook("pre_tool_call", lambda **kwargs: before_read(ctx, **kwargs))

