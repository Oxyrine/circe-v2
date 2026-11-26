import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.http_common import SECURITY_HEADERS, send_json  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        send_json(self, 200, {"status": "ok", "service": "circe-api"})

    def do_OPTIONS(self):  # same-origin only: no CORS grant
        self.send_response(204)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass

