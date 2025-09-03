#!/usr/bin/env python3

import ua_generator

import os
import random
import logging
from typing import Dict, Tuple, Optional
from flask import Flask, request, abort, Response, jsonify
import requests

# ---------- APP ----------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = app.logger

# ---------- CONFIG ----------
CONNECT_TIMEOUT = int(os.environ.get("HTMLCSI_CONNECT_TIMEOUT", 25))
READ_TIMEOUT    = int(os.environ.get("HTMLCSI_READ_TIMEOUT", 120))
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

INTERNAL_API_KEY = os.environ.get("HTMLCSI_API_KEY", "OTTONRENT")
STATUS_ENDPOINT = os.environ.get("STATUS_ENDPOINT", "http://166.0.242.212:7777/status")
POST_ENDPOINT = os.environ.get("POST_ENDPOINT", "https://httpbin.org/anything")
HOMEPAGE = os.environ.get("HOMEPAGE", "https://htmlcsstoimage.com/")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]
LOCALES = ["en-US,en;q=0.9", "en-GB,en;q=0.9", "en-IN,en;q=0.9"]
    
# ---------- UTILITIES ----------
def pick_random_user_agent() -> str:
    """
    Prefer ua-generator when available; fall back to the static USER_AGENTS list.
    Returns a single User-Agent string.
    """
    try:
        # generate a random UA (no args -> fully random)
        ua = ua_generator.generate()
        hdrs = ua.headers.get()  # returns dict like {'user-agent': '...', 'sec-ch-ua': '...', ...}
        # prefer the returned user-agent value (header key is lowercase according to README)
        ua_text = hdrs.get("user-agent") or hdrs.get("User-Agent")
        if isinstance(ua_text, str) and ua_text.strip():
            return ua_text
    except Exception as e:
        # on any failure, fall back to static list
        logger.debug("ua-generator failed, falling back to static list: %s", e, exc_info=True)

    # fallback
    return random.choice(USER_AGENTS)


def random_ipv4_public() -> str:
    while True:
        ip = ".".join(str(random.randint(1, 254)) for _ in range(4))
        first = int(ip.split(".")[0])
        if first in (10, 127, 169, 172, 192):
            continue
        return ip


def generate_minimal_headers(cookie_str: Optional[str], token: Optional[str]) -> Dict[str, str]:
    """
    Build headers using ua-generator where possible and fall back to static UA.
    Adds Sec-CH-UA* headers from ua-generator and the requested Sec-Fetch-* headers.
    """
    # Try to get complete headers (user-agent + client hints) from ua-generator
    ch_headers = {}
    try:
        ua = ua_generator.generate()
        ch_headers = ua.headers.get() or {}
    except Exception:
        # silent fallback; pick_random_user_agent() will use static list
        ch_headers = {}

    # choose final User-Agent (either from ch_headers or fallback function)
    ua_text = ch_headers.get("user-agent") or ch_headers.get("User-Agent") or pick_random_user_agent()

    headers = {
        "User-Agent": ua_text,
        "Accept": "*/*",
        "Accept-Language": random.choice(LOCALES),
        "Content-Type": "application/json",
        "Origin": HOMEPAGE,
        "Referer": HOMEPAGE,
        "DNT": "1",
        # Add the Sec-Fetch headers you requested (minimal fixed values)
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    # Map client-hint keys from ua-generator into canonical header names if present
    # ua-generator returns keys like 'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform'
    if ch_headers:
        if "sec-ch-ua" in ch_headers:
            headers["Sec-CH-UA"] = ch_headers["sec-ch-ua"]
        if "sec-ch-ua-mobile" in ch_headers:
            headers["Sec-CH-UA-Mobile"] = ch_headers["sec-ch-ua-mobile"]
        if "sec-ch-ua-platform" in ch_headers:
            headers["Sec-CH-UA-Platform"] = ch_headers["sec-ch-ua-platform"]

    # Attach cookie / token if present (unchanged)
    if cookie_str:
        headers["Cookie"] = cookie_str
    if token:
        headers["requestverificationtoken"] = token

    return headers


def fetch_status(session: requests.Session) -> Tuple[str, Optional[str]]:
    resp = session.get(STATUS_ENDPOINT, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    cookies = data.get("cookies", []) or []
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies) if cookies else ""
    token = data.get("requestVerificationToken") or data.get("__RequestVerificationToken") or data.get("RequestVerificationToken")
    return cookie_str, token


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
}

# ---------- ROUTES ----------
@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "pong"}), 200


def require_api_key():
    client_key = request.headers.get("X-API-KEY", "")
    if not client_key or client_key != INTERNAL_API_KEY:
        abort(401, "Invalid or missing X-API-KEY")


@app.route("/convert", methods=["POST"])
def convert():
    require_api_key()

    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    body = request.get_json()
    html = body.get("html")
    if not html:
        return jsonify({"error": "Missing 'html' field"}), 400

    forward_payload = {"html": html}
    for key in ("selector", "full_screen", "render_when_ready", "color_scheme", "timezone",
                "block_consent_banners", "viewport_width", "viewport_height", "device_scale", "css", "url"):
        if key in body:
            forward_payload[key] = body[key]

    sess = requests.Session()

    cookie_str = ""
    token = None
    try:
        cookie_str, token = fetch_status(sess)
        logger.info("Fetched status: cookie_present=%s token_present=%s", bool(cookie_str), bool(token))
    except Exception as e:
        logger.warning("Failed to fetch /status: %s — proceeding without cookies/token", str(e))

    headers = generate_minimal_headers(cookie_str, token)

    try:
        upstream = sess.post(POST_ENDPOINT, headers=headers, json=forward_payload, stream=True, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        logger.exception("Error contacting upstream service")
        return jsonify({"error": "Failed to contact upstream service", "details": str(e)}), 502

    # Log upstream status for debugging
    logger.info("Upstream status: %s headers: %s", upstream.status_code, {k: v for k, v in upstream.headers.items() if k.lower() in ("content-type", "content-length")})

    # If upstream returned JSON or text (not image), capture a small debug snippet
    content_type = upstream.headers.get("Content-Type", "")
    if not content_type.startswith("image/") and upstream.status_code != 200:
        # try to parse json or text for debugging
        try:
            debug_body = upstream.json()
            logger.warning("Upstream non-image JSON response: %s", debug_body)
        except Exception:
            text = upstream.text[:1000]
            logger.warning("Upstream non-image text: %s", text)

    forwarded_headers = {}
    for k, v in upstream.headers.items():
        if k.lower() in HOP_BY_HOP_HEADERS:
            continue
        forwarded_headers[k] = v

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    resp_content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    return Response(generate(), status=upstream.status_code, headers=forwarded_headers, content_type=resp_content_type)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "1.0"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
