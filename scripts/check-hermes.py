"""Exercise the real installed Hermes loader, tool dispatcher and hook bus.

Run with Hermes and its dependencies on PYTHONPATH. No model is called.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source))
    with tempfile.TemporaryDirectory(prefix="sendsafe-hermes-") as tmp:
        home = Path(tmp)
        os.environ["HERMES_HOME"] = str(home)
        plugin = home / "plugins" / "sendsafe"
        plugin.mkdir(parents=True)
        for name in ("__init__.py", "plugin.yaml"):
            shutil.copyfile(source / name, plugin / name)
        for name in ("sendsafe_core", "dashboard", "desktop"):
            shutil.copytree(source / name, plugin / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        inputs = home / "documents"
        home.joinpath("config.yaml").write_text(
            "plugins:\n  enabled: [sendsafe]\n  entries:\n    sendsafe:\n      settings:\n        allowed_roots:\n          - " + json.dumps(str(inputs)) + "\n        guard_reads: true\n"
        )
        from hermes_cli.plugins import PluginManager
        from tools.registry import registry
        from sendsafe_core.fixtures import create_fixtures
        manager = PluginManager(scope_key=str(home))
        manager.discover_and_load()
        loaded = next(p for p in manager.list_plugins() if p["name"] == "sendsafe")
        assert loaded.get("enabled") and not loaded.get("error"), loaded
        paths = create_fixtures(inputs)

        def call(name, body):
            result = registry.dispatch(name, body, scope=manager.scope_key)
            return json.loads(result) if isinstance(result, str) else result

        proof = {"hermes_loader": "PASS", "cases": []}
        for kind, path in paths.items():
            before = Path(path).read_bytes()
            report = call("sendsafe_inspect", {"path": path})
            assert report["ok"] and report["finding_count"] > 0, report
            assert "CANARY" not in json.dumps(report)
            blocked = manager.invoke_hook("pre_tool_call", tool_name="read_file", args={"path": path})
            assert blocked and blocked[0]["action"] == "block", blocked
            assert "CANARY" not in json.dumps(blocked)
            copy = call("sendsafe_prepare", {"path": path, "expected_sha256": report["source_sha256"]})
            assert copy["ok"] and copy["verified"], copy
            verified = call("sendsafe_verify", {"export_id": copy["export_id"]})
            assert verified["ok"] and verified["verified"], verified
            assert Path(path).read_bytes() == before
            allowed = manager.invoke_hook("pre_tool_call", tool_name="read_file", args={"path": copy["output_path"]})
            assert allowed == [], allowed
            copied = Path(copy["output_path"])
            copied.write_bytes(copied.read_bytes() + b"changed")
            tampered = call("sendsafe_verify", {"export_id": copy["export_id"]})
            assert not tampered["ok"] and tampered["error"]["code"] == "tampered_export", tampered
            proof["cases"].append({"format": kind, "inspect": "PASS", "original_read": "BLOCKED",
                                   "block_message": blocked[0]["message"], "copy_read": "ALLOWED",
                                   "source_unchanged": True, "tampered_copy": "REFUSED"})
        manager.unload()
        folder = source / "reports"
        folder.mkdir(exist_ok=True)
        (folder / "hermes-integration.json").write_text(json.dumps(proof, indent=2))
        print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    main()
