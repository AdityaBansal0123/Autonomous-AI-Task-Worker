"""Local-only HTTP application. Run with: python app.py"""
import argparse
import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from worker.engine import Engine
from worker.store import Store

ROOT = Path(__file__).resolve().parent


def make_server(db_path, port=8000, delay=0.25):
    store = Store(db_path)
    engine = Engine(store, delay=delay)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logging.info(fmt, *args)

        def send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
            data = json.dumps(body).encode() if content_type.startswith("application/json") else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def allowed(self):
            actual_port = self.server.server_port
            allowed_hosts = {f"localhost:{actual_port}", f"127.0.0.1:{actual_port}"}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            return host in allowed_hosts and (not origin or origin in {f"http://{h}" for h in allowed_hosts})

        def do_GET(self):
            if not self.allowed():
                return self.send(403, {"error": "Local same-origin access only"})
            path = urlparse(self.path).path
            try:
                if path == "/api/state":
                    return self.send(200, {"runs": store.runs(), "invoices": store.invoices(), "documents": store.documents(),
                                           "model_available": bool(os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_MODEL")),
                                           "model": os.getenv("OPENAI_MODEL", ""), "approval_threshold_cents": 1000000})
                match = re.fullmatch(r"/api/runs/([a-f0-9]{12})(/export)?", path)
                if match:
                    headers = {"Content-Disposition": f'attachment; filename="relay-{match[1]}.json"'} if match[2] else None
                    return self.send(200, store.run(match[1]), headers=headers)
                match = re.fullmatch(r"/api/documents/([a-z0-9-]+)", path)
                if match:
                    return self.send(200, store.document(match[1]))
                if path == "/health":
                    return self.send(200, {"status": "ok"})
                static = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8")}
                if path in static:
                    file, mime = static[path]
                    return self.send(200, (ROOT / "static" / file).read_bytes(), mime)
                return self.send(404, {"error": "Not found"})
            except (KeyError, ValueError):
                return self.send(404, {"error": "Resource not found"})

        def do_POST(self):
            if not self.allowed():
                return self.send(403, {"error": "Local same-origin access only"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "Use application/json"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    return self.send(413, {"error": "Request body must be between 1 and 16,384 bytes"})
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object")
                path = urlparse(self.path).path
                if path == "/api/runs":
                    if sum(r["status"] in ("queued", "running") for r in store.runs()) >= 4:
                        return self.send(429, {"error": "Four tasks are already active. Wait for one to finish."})
                    return self.send(201, engine.create(data.get("task"), data.get("mode", "demo"), data.get("fault", "none")))
                match = re.fullmatch(r"/api/runs/([a-f0-9]{12})/(respond|cancel|resume)", path)
                if match:
                    run_id, action = match.groups()
                    result = engine.respond(run_id, data.get("answer")) if action == "respond" else getattr(engine, action)(run_id)
                    return self.send(200, result)
                return self.send(404, {"error": "Not found"})
            except KeyError:
                return self.send(404, {"error": "Task not found"})
            except (ValueError, TypeError) as exc:
                return self.send(400, {"error": str(exc)})
            except Exception as exc:
                from worker.providers import ProviderError
                if isinstance(exc, ProviderError):
                    return self.send(400, {"error": str(exc)})
                logging.exception("Request failed")
                return self.send(500, {"error": "Unexpected server error; inspect server logs"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.engine = engine
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Relay autonomous task worker (local sandbox)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(ROOT / "data" / "relay.db"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = make_server(args.db, args.port)
    print(f"Relay is running at http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
