from flask import Flask, request, jsonify, Response, abort, stream_with_context
import requests
import logging
from typing import Dict, Any, Tuple

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Hard-coded API key
INTERNAL_API_KEY = "OTTONRENT"

# Upstream HTML->image endpoint (unchanged)
HTML_TO_IMAGE_URL = "https://htmlcsstoimage.com/image-demo"

# Status endpoint (where we GET cookies + requestVerificationToken)
STATUS_URL = "http://166.0.242.212:7777/status"

# Recommended: tune these timeouts to your needs
STATUS_TIMEOUT = (5, 10)   # (connect, read)
UPSTREAM_TIMEOUT = (5, 120)


def fetch_status_json(status_url: str, timeout: Tuple[int, int] = STATUS_TIMEOUT) -> Dict[str, Any]:
    """GET /status, return parsed JSON. Raises requests.RequestException on failure."""
    r = requests.get(status_url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def build_session_and_headers_from_status(status_json: Dict[str, Any]) -> Tuple[requests.Session, Dict[str, str]]:
    """
    Build a requests.Session populated with cookies from status_json and headers containing
    requestVerificationToken (if present). Also prepares a fallback Cookie header string
    to send when cookie domains may not match automatically.
    """
    session = requests.Session()
    headers: Dict[str, str] = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        # some servers expect X-Requested-With
        "X-Requested-With": "XMLHttpRequest",
    }

    # Extract token and add common header names for compatibility
    token = status_json.get("requestVerificationToken") or status_json.get("request_verification_token")
    if token:
        headers["RequestVerificationToken"] = token
        headers["X-RequestVerificationToken"] = token

    # Extract cookies array and set into session cookie jar
    cookies_arr = status_json.get("cookies", []) or []
    cookie_pairs = []
    for c in cookies_arr:
        name = c.get("name")
        value = c.get("value", "")
        domain = c.get("domain")  # may be like ".htmlcsstoimage.com"
        path = c.get("path", "/")
        if not name:
            continue
        try:
            # Try to set domain/path (requests accepts those)
            session.cookies.set(name, value, domain=domain, path=path)
        except Exception:
            # Fallback if domain/path cause issues
            session.cookies.set(name, value)
        cookie_pairs.append(f"{name}={value}")

    # Build explicit Cookie header fallback (useful when posting to an IP or different hostname)
    if cookie_pairs:
        headers["Cookie"] = "; ".join(cookie_pairs)

    return session, headers


@app.route("/ping", methods=["GET"])
def ping():
    """
    Health-check endpoint to verify the service is running.
    Always returns HTTP 200.
    """
    return jsonify({
        "status": "ok",
        "message": "pong"
    }), 200


@app.route("/convert", methods=["POST"])
def render_html():
    # 1) Check API key
    client_key = request.headers.get("X-API-KEY", "")
    if client_key != INTERNAL_API_KEY:
        abort(401, "Invalid or missing X-API-KEY")

    # 2) Get JSON payload
    data = request.get_json(force=True)
    html = data.get("html")
    if not html:
        abort(400, "`html` field is required")

    # 3) Build payload to forward (keeps your original fields + preserves values)
    forward_payload = {
        "html": html,
        "css":               data.get("css", ""),
        "url":               data.get("url", ""),
        "selector":          data.get("selector", ""),
        "console_mode":      data.get("console_mode", ""),
        "ms_delay":          data.get("ms_delay", ""),
        "render_when_ready": data.get("render_when_ready", ""),
        "viewport_width":    data.get("viewport_width", ""),
        "viewport_height":   data.get("viewport_height", ""),
        "google_fonts":      data.get("google_fonts", ""),
        "device_scale":      data.get("device_scale", "")
    }

    # ===== ADDED: Fetch status and attach cookies + verification token =====
    try:
        status_json = fetch_status_json(STATUS_URL)
    except requests.RequestException as exc:
        logger.exception("Failed to fetch status JSON from %s", STATUS_URL)
        # upstream status unavailable -> return 502 with a helpful message
        return jsonify({"error": "failed to fetch upstream status", "detail": str(exc)}), 502

    session, extra_headers = build_session_and_headers_from_status(status_json)

    # Merge headers: keep your Accept/Content-Type but allow token/Cookie from extra_headers to override/add
    headers = {
        "Accept":        "*/*",
        "Content-Type":  "application/json",
    }
    # update with token + cookie + extras
    headers.update(extra_headers)

    # Also: include token in the JSON payload for compatibility (some servers expect token in body)
    token = status_json.get("requestVerificationToken") or status_json.get("request_verification_token")
    if token:
        # non-destructive: only add if not present already
        if "requestVerificationToken" not in forward_payload:
            forward_payload["requestVerificationToken"] = token

    # 4) Forward request to upstream using session (so cookies are sent automatically)
    try:
        upstream = session.post(
            HTML_TO_IMAGE_URL,
            headers=headers,
            json=forward_payload,
            stream=True,
            timeout=UPSTREAM_TIMEOUT
        )
    except requests.RequestException as exc:
        logger.exception("Upstream request to %s failed", HTML_TO_IMAGE_URL)
        return jsonify({"error": "upstream request failed", "detail": str(exc)}), 502

    # 5) Stream the response back (preserve most upstream headers except encoding/transfer)
    response_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in ("content-encoding", "transfer-encoding")
    }

    return Response(
        stream_with_context(upstream.iter_content(chunk_size=4096)),
        status=upstream.status_code,
        headers=response_headers
    )


if __name__ == "__main__":
    # Render will auto-detect and run this via gunicorn
    app.run(host="0.0.0.0", port=5000)
