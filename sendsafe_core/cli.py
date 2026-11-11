"""Local commands for inspecting documents without a model or server."""

import argparse
import json
from pathlib import Path
from .archive import DocumentError
from .fixtures import create_fixtures
from .service import SendSafe


def main():
    parser = argparse.ArgumentParser(description="Inspect and rebuild Word/Excel sharing snapshots locally.")
    parser.add_argument("--storage", default=".sendsafe-exports")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan"); scan.add_argument("path")
    prepare = commands.add_parser("prepare"); prepare.add_argument("path"); prepare.add_argument("--sha256", required=True)
    verify = commands.add_parser("verify"); verify.add_argument("export_id")
    demo = commands.add_parser("fixtures"); demo.add_argument("folder")
    args = parser.parse_args()
    try:
        service = SendSafe(args.storage)
        if args.command == "fixtures":
            result = create_fixtures(args.folder)
        elif args.command == "scan":
            result = service.scan(args.path)
        elif args.command == "prepare":
            result = service.prepare(args.path, args.sha256)
        else:
            result = service.verify(args.export_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except DocumentError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()

