"""Local PWA server + upload endpoint (Python 3.13+ compatible).

Serves the app on http://localhost:8765
POST /upload saves PDFs locally and optionally uploads to Dropbox API.
Configure server-config.json (copy from server-config.example.json).
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT_DIR = ROOT / "REPORT"


def load_server_config() -> dict:
    path = ROOT / "server-config.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[CONFIG WARN] Could not read server-config.json: {exc}")
        return {}


SERVER_CONFIG = load_server_config()


def get_report_dir() -> Path:
    custom = SERVER_CONFIG.get("localReportDir")
    if not custom:
        report_dir = DEFAULT_REPORT_DIR
    else:
        p = Path(str(custom))
        report_dir = p if p.is_absolute() else ROOT / p
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def dropbox_enabled() -> bool:
    if SERVER_CONFIG.get("dropboxUpload") is False:
        return False
    token = (SERVER_CONFIG.get("dropboxAccessToken") or "").strip()
    return bool(token)


def normalize_dropbox_folder(folder: str) -> str:
    p = (folder or "/REPORT/").strip() or "/REPORT/"
    if not p.startswith("/"):
        p = "/" + p
    return p if p.endswith("/") else f"{p}/"


def upload_to_dropbox(token: str, folder: str, filename: str, data: bytes) -> dict:
    path = f"{normalize_dropbox_folder(folder)}{Path(filename).name}"
    api_arg = json.dumps({"path": path, "mode": "add", "autorename": True}, ensure_ascii=False)
    req = urllib.request.Request(
        "https://content.dropboxapi.com/2/files/upload",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": api_arg.encode("utf-8"),
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_multipart(body: bytes, content_type: str) -> dict[str, tuple[str | None, bytes]]:
    """Parse multipart/form-data without the removed cgi module."""
    m = re.search(r"boundary=(?P<b>[^;]+)", content_type, re.I)
    if not m:
        raise ValueError("Missing multipart boundary")
    boundary = m.group("b").strip().strip('"').encode()
    parts: dict[str, tuple[str | None, bytes]] = {}

    for chunk in body.split(b"--" + boundary):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        header_block, _, data = chunk.partition(b"\r\n\r\n")
        data = data.rstrip(b"\r\n")
        headers = header_block.decode("utf-8", errors="replace")
        name_m = re.search(r'name="([^"]+)"', headers)
        if not name_m:
            continue
        name = name_m.group(1)
        fn_m = re.search(r'filename="([^"]*)"', headers)
        filename = fn_m.group(1) if fn_m else None
        parts[name] = (filename or None, data)
    return parts


def field_text(parts: dict, name: str, default: str = "") -> str:
    if name not in parts:
        return default
    _, data = parts[name]
    return data.decode("utf-8", errors="replace").strip() or default


def upload_status_payload() -> dict:
    report_dir = get_report_dir()
    return {
        "ok": True,
        "localUpload": True,
        "dropboxEnabled": dropbox_enabled(),
        "reportDir": str(report_dir),
        "hasDropboxToken": bool((SERVER_CONFIG.get("dropboxAccessToken") or "").strip()),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/upload/status":
            self.send_json(upload_status_payload())
            return
        super().do_GET()

    def send_json(self, payload: dict, status: int = 200):
        resp = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/upload":
            self.send_error(404, "Not Found")
            return
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self.send_error(400, "Expected multipart/form-data")
                return

            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            parts = parse_multipart(body, content_type)

            if "file" not in parts:
                self.send_error(400, "Missing file field")
                return

            _, file_data = parts["file"]
            filename = field_text(parts, "filename")
            if not filename and parts["file"][0]:
                filename = parts["file"][0]
            if not filename:
                filename = "report.pdf"
            filename = Path(str(filename)).name.replace("..", "_")

            report_dir = get_report_dir()
            dest = report_dir / filename
            dest.write_bytes(file_data)

            dropbox_folder = field_text(parts, "dropboxFolder", "/REPORT/")
            payload: dict = {
                "ok": True,
                "filename": filename,
                "savedTo": str(dest),
                "folder": str(report_dir),
                "size": len(file_data),
                "dropbox": None,
            }

            if dropbox_enabled():
                token = SERVER_CONFIG["dropboxAccessToken"].strip()
                try:
                    dbx = upload_to_dropbox(token, dropbox_folder, filename, file_data)
                    payload["dropbox"] = {
                        "ok": True,
                        "path": dbx.get("path_display") or dbx.get("path_lower"),
                        "id": dbx.get("id"),
                    }
                    print(f"[DROPBOX OK] {payload['dropbox']['path']} ({len(file_data)} bytes)")
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")
                    payload["dropbox"] = {"ok": False, "error": detail or str(exc)}
                    print(f"[DROPBOX ERROR] {detail or exc}")
                except Exception as exc:
                    payload["dropbox"] = {"ok": False, "error": str(exc)}
                    print(f"[DROPBOX ERROR] {exc}")
            else:
                payload["dropbox"] = {
                    "ok": False,
                    "skipped": True,
                    "reason": "Dropbox token not configured in server-config.json",
                }

            print(f"[UPLOAD OK] {dest} ({len(file_data)} bytes)")
            self.send_json(payload)
        except Exception as exc:
            print(f"[UPLOAD ERROR] {exc}")
            self.send_error(500, str(exc))


def main():
    port = 8765
    report_dir = get_report_dir()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Serving:  {ROOT}")
    print(f"Reports:  {report_dir}")
    if dropbox_enabled():
        print("Dropbox:  enabled (server-config.json token set)")
    else:
        print("Dropbox:  disabled - copy server-config.example.json to server-config.json")
    print(f"Open app: http://localhost:{port}/index.html")
    print(f"Upload:   POST http://localhost:{port}/upload")
    print(f"Status:   GET  http://localhost:{port}/upload/status")
    server.serve_forever()


if __name__ == "__main__":
    main()
