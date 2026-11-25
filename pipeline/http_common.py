"""pipeline/http_common.py: routes + HTTP hygiene shared by server.py and the Vercel api/*.py handlers.

Route functions are pure (body dict -> (status, dict)); the transport layer
adds the body cap, security headers, JSON-only errors and rate limiting.
"""

import json
import re
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler

from ai import assistant
from ai.provider import generate_narrative
from pipeline.prioritise import normalise_context, prioritise
from pipeline.service import entities_map, get_ring, get_rings, get_total_ring_count, run_rescore

MAX_BODY_BYTES = 16 * 1024
RING_ID_RE = re.compile(r"^[A-Z0-9]{1,20}$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    # Demo pages use inline <script>/<style>, so those stay allowed; all fetches are same-origin.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}


# --- rate limiting (in-memory) -------------------------------------------------
# ponytail: per-process, so on Vercel each warm instance counts separately.
# Move to a shared store (e.g. Redis) if abuse is real.
class RateLimiter:
    def __init__(self, limit=20, window=60):
        self.limit, self.window = limit, window
        self._hits = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, key, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = [t for t in self._hits[key] if now - t < self.window]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


LLM_LIMITER = RateLimiter(limit=20, window=60)


# --- routes ----------------------------------------------------------------------

def _ring_id(body):
    rid = body.get("ring_id")
    return rid if isinstance(rid, str) and RING_ID_RE.match(rid) else None


def _context(body):
    entities = entities_map()
    valid = {e.get("industry_class") for e in entities.values()} - {None}
    return normalise_context(body.get("context"), valid), entities


def route_prioritise(body):
    try:
        ctx, entities = _context(body)
    except ValueError as e:
        return 400, {"error": str(e)}
    return 200, prioritise(get_rings(), entities, ctx, total_count=get_total_ring_count())


def route_assistant(body):
    rid = _ring_id(body)
    if not rid:
        return 400, {"error": "ring_id is required"}
    history = body.get("history", [])
    if not isinstance(history, list) or len(history) > 20:
        return 400, {"error": "history must be a short list"}
    try:
        ctx, _ = _context(body)
    except ValueError as e:
        return 400, {"error": str(e)}
    return assistant.answer(rid, body.get("question"), ctx, [h for h in history if isinstance(h, str)])


def route_narrative(body):
    rid = _ring_id(body)
    if not rid:
        return 400, {"error": "ring_id is required"}
    ring = get_ring(rid)  # never trust a ring object from the client
    if ring is None:
        return 404, {"error": "unknown ring_id"}
    return 200, generate_narrative(ring, actor="investigator")


def route_rescore(body):
    return run_rescore(body.get("investigator_invoices", []))


# path -> (route fn, rate-limited?)
POST_ROUTES = {
    "/api/prioritise": (route_prioritise, False),
    "/api/assistant": (route_assistant, True),
    "/api/ai/narrative": (route_narrative, True),
    "/api/narrative": (route_narrative, True),
    "/api/rescore": (route_rescore, False),
}


# --- transport -------------------------------------------------------------------

def client_ip(handler):
    fwd = handler.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() or handler.client_address[0]


def send_json(handler, status, data):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if not getattr(handler, "adds_security_headers", False):
        for k, v in SECURITY_HEADERS.items():
            handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler):
    """Returns (body_dict, None) or (None, (status, error_dict))."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return None, (400, {"error": "invalid Content-Length"})
    if length < 0:
        return None, (400, {"error": "invalid Content-Length"})
    if length > MAX_BODY_BYTES:
        return None, (413, {"error": "request body too large"})
    raw = handler.rfile.read(length) if length else b""
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, (400, {"error": "invalid JSON"})
    if not isinstance(body, dict):
        return None, (400, {"error": "JSON body must be an object"})
    return body, None


def handle_post(handler, path):
    """Serve a POST route. Returns False if the path is not an API route."""
    entry = POST_ROUTES.get(path)
    if entry is None:
        return False
    fn, limited = entry
    if limited and not LLM_LIMITER.allow(client_ip(handler)):
        send_json(handler, 429, {"error": "rate limit exceeded, try again shortly"})
        return True
    body, err = read_json_body(handler)
    if err:
        send_json(handler, *err)
        return True
    try:
        status, data = fn(body)
    except Exception as e:  # log server-side; never echo internals
        print(f"{path}: unhandled {type(e).__name__}")
        status, data = 500, {"error": "internal error"}
    send_json(handler, status, data)
    return True


def make_vercel_handler(path):
    """BaseHTTPRequestHandler for one Vercel function bound to one POST route."""

    class handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if not handle_post(self, path):
                send_json(self, 404, {"error": "not found"})

        def do_OPTIONS(self):  # same-origin only: no CORS grant
            self.send_response(204)
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()

        def log_message(self, fmt, *args):
            pass

    return handler
