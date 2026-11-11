"""Authenticated local review UI; same engine and operations as the Hermes plugin."""

import argparse
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sendsafe_core.api import guarded_dispatch
from sendsafe_core.archive import MAX_FILE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--storage", default=".local/review-data")
    parser.add_argument("--root", action="append", default=[])
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    assets = Path(__file__).resolve().parents[1] / "web"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            host = self.headers.get("Host", "")
            return host in (f"127.0.0.1:{args.port}", f"localhost:{args.port}") and secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/", "/app.js", "/style.css"):
                file = assets / {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
                self.send(200, file.read_bytes(), {"/": "text/html; charset=utf-8", "/app.js": "text/javascript", "/style.css": "text/css"}[path])
                return
            if not self.authorized():
                self.send(401, b'{"ok":false,"error":{"message":"Open the private local review link."}}')
                return
            if path == "/api/files":
                result = guarded_dispatch(args.storage, args.root, "files", {})
            elif path.startswith("/api/file/"):
                result = guarded_dispatch(args.storage, args.root, "verify", {"export_id": path.rsplit("/", 1)[-1]})
                if result.get("ok"):
                    file = Path(result["output_path"])
                    self.send(200, file.read_bytes(), "application/octet-stream")
                    return
            else:
                self.send(404, b'{"ok":false}')
                return
            self.send(200, json.dumps(result).encode())

        def do_POST(self):
            if not self.authorized():
                self.send(401, b'{"ok":false}')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_FILE * 4 // 3 + 10_000:
                    self.send(413, b'{"ok":false,"error":{"message":"Request too large."}}')
                    return
                payload = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                self.send(400, b'{"ok":false}')
                return
            action = urlsplit(self.path).path.removeprefix("/api/")
            result = guarded_dispatch(args.storage, args.root, action, payload)
            self.send(200, json.dumps(result, ensure_ascii=False).encode())

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"SendSafe review: http://127.0.0.1:{args.port}/#access={token}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
