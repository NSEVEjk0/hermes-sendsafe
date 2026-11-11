"""Native Hermes API. The bounded child runs only SendSafe's own local engine."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse
from hermes_cli.config import load_config_readonly
from plugins.plugin_storage import plugin_data_dir

router = APIRouter()


def _call(action, body):
    config = load_config_readonly()
    roots = config.get("plugins", {}).get("entries", {}).get("sendsafe", {}).get("settings", {}).get("allowed_roots", [])
    if not isinstance(roots, list) or any(not isinstance(r, str) for r in roots):
        return {"ok": False, "error": {"code": "configuration", "message": "Allowed folders must be a list of folder paths."}}
    try:
        result = subprocess.run(
            [sys.executable, "-m", "sendsafe_core.api", "--storage", str(plugin_data_dir("sendsafe")), "--roots", json.dumps(roots)],
            cwd=Path(__file__).resolve().parents[1], input=json.dumps({"action": action, "body": body}),
            text=True, capture_output=True, timeout=30, check=False,
        )
        if result.returncode:
            return {"ok": False, "error": {"code": "engine_failed", "message": "The local document engine could not complete this operation."}}
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"ok": False, "error": {"code": "engine_failed", "message": "The local document operation failed or timed out."}}


@router.get("/files")
async def files():
    return await asyncio.to_thread(_call, "files", {})


@router.post("/demo")
async def demo():
    return await asyncio.to_thread(_call, "demo", {})


@router.post("/scan")
async def scan(body: dict):
    return await asyncio.to_thread(_call, "scan", body)


@router.post("/upload")
async def upload(body: dict):
    return await asyncio.to_thread(_call, "upload", body)


@router.post("/prepare")
async def prepare(body: dict):
    return await asyncio.to_thread(_call, "prepare", body)


@router.post("/verify")
async def verify(body: dict):
    return await asyncio.to_thread(_call, "verify", body)


@router.post("/preview")
async def preview(body: dict):
    return await asyncio.to_thread(_call, "preview", body)


@router.get("/file/{export_id}")
async def download(export_id: str):
    result = await asyncio.to_thread(_call, "verify", {"export_id": export_id})
    if not result.get("ok"):
        return result
    path = Path(result["output_path"])
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
