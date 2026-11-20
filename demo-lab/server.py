"""Disposable demo workload: a health endpoint with controlled failures.

The three demo services use this same tiny server. A service can fail itself
via /run/demo/fail or degrade when its declared upstream fails. No database,
credentials, exposed host ports, or production resources are involved.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

FAIL_FLAG = Path("/run/demo/fail")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "demo-service")
UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "")


def check_health(fail_flag=FAIL_FLAG, upstream_url=UPSTREAM_URL):
    """Return a tuple (http_status, reason), no external side effects."""
    if fail_flag.exists():
        return 503, "injected_failure"
    if upstream_url:
        try:
            with urlopen(upstream_url, timeout=1.5) as response:
                if response.status != 200:
                    return 503, "upstream_unhealthy"
        except HTTPError:
            return 503, "upstream_unhealthy"
        except (URLError, TimeoutError, OSError):
            return 503, "upstream_unreachable"
    return 200, "healthy"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_json(404, {"error": "not_found"})
            return

        status, reason = check_health()
        payload = {
            "service": SERVICE_NAME,
            "status": "ok" if status == 200 else "degraded",
            "reason": reason,
        }
        self.send_json(status, payload)

    def send_json(self, status, payload):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
