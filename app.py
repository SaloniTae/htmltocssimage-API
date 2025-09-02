#!/usr/bin/env python3
# app.py - forwarder that fetches /status, posts to html->image service and streams response back

import os
import random
import logging
from typing import Dict, Tuple, Optional
from flask import Flask, request, abort, Response, jsonify
import requests

# ---------- CONFIG ----------
INTERNAL_API_KEY = os.environ.get("HTMLCSI_API_KEY", "OTTONRENT")
STATUS_ENDPOINT = os.environ.get("STATUS_ENDPOINT", "http://166.0.242.212:7777/status")
POST_ENDPOINT = os.environ.get("POST_ENDPOINT", "https://htmlcsstoimage.com/image-demo")
HOMEPAGE = os.environ.get("HOMEPAGE", "https://htmlcsstoimage.com/")

# small realistic UA pool
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]
LOCALES = ["en-US,en;q=0.9", "en-GB,en;q=0.9", "en-IN,en;q=0.9"]

# ---------- APP ----------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


# ---------- UTILITIES ----------
def pick_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def generate_ch_ua_from_ua(ua: str):
    # simple heuristic for Sec-CH-UA-ish values
    if "Chrome" in ua and "Chromium" not in ua:
        brand = '"Chromium";v="121", "Google Chrome";v="121"'
        platform = '"Windows"' if "Windows" in ua else '"macOS"' if "Macintosh" in ua else '"Android"'
        mobile = "?0"
    elif "Safari" in ua and "Chrome" not in ua:
        brand = '"Safari";v="17", "Not A Brand";v="99"'
        platform = '"macOS"' if "Macintosh" in ua else '"iOS"'
        mobile = "?1" if "iPhone" in ua else "?0"
    else:
        brand = '"Not A Brand";v="99"'
        platform = '"Linux"'
        mobile = "?0"
    return brand, mobile, platform


def random_ipv4_public() -> str:
    while True:
        ip = ".".join(str(random.randint(1, 254)) for _ in range(4))
        first = int(ip.split(".")[0])
        # skip private/reserved-ish ranges for realism
        if first in (10, 127, 169, 172, 192):
            continue
        return ip


def generate_fingerprint_headers() -> Dict[str, str]:
    ua = pick_random_user_agent()
    ch_brand, ch_mobile, ch_platform = generate_ch_ua_from_ua(ua)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": random.choice(LOCALES),
        "Sec-CH-UA": ch_brand,
        "Sec-CH-UA-Mobile": ch_mobile,
        "Sec-CH-UA-Platform": ch_platform,
        "Origin": HOMEPAGE,
        "Referer": HOMEPAGE,
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "X-Forwarded-For": random_ipv4_public()
    }
    return headers


def fetch_status(session: requests.Session) -> Tuple[str, Optional[str]]:
    """
    Call STATUS_ENDPOINT to retrieve cookies and requestVerificationToken.
    Returns (cookie_str, token) where each may be empty/None if not found.
    Raises requests.exceptions.RequestException on failure.
    """
    resp = session.get(STATUS_ENDPOINT, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    cookies = data.get("cookies", [])
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
    # Let Flask set Content-Length; filter encoding/transfer to avoid duplication
    "content-encoding"
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
    """
    POST /convert
    Headers: X-API-KEY: <key>
    JSON body: must include "html": "<html string>".
    All other accepted fields will be forwarded to the upstream API (selector, render_when_ready, etc).
    The upstream response (image/json/text) is streamed back to the caller with upstream's status code.
    """
    require_api_key()

    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    body = request.get_json()
    html = body.get("html")
    if not html:
        return jsonify({"error": "Missing 'html' field"}), 400

    # Build payload to forward - include typical options if provided
    forward_payload = {}
    forward_payload["html"] = html
    for key in ("selector", "full_screen", "render_when_ready", "color_scheme", "timezone",
                "block_consent_banners", "viewport_width", "viewport_height", "device_scale", "css", "url"):
        if key in body:
            forward_payload[key] = body[key]

    # Prepare session and headers
    sess = requests.Session()
    headers = generate_fingerprint_headers()

    # Try to get cookies + token via /status. If it fails, we continue without them (best-effort).
    try:
        cookie_str, token = fetch_status(sess)
        if cookie_str:
            headers["Cookie"] = cookie_str
        if token:
            headers["requestverificationtoken"] = token
    except Exception as e:
        # log, but attempt forward without token/cookies
        logging = app.logger
        logging.warning("Failed to fetch /status: %s — proceeding without cookies/token", str(e))

    # Additional fixed headers requested by the target service
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "*/*")

    # Forward the request and stream back
    try:
        upstream = sess.post(POST_ENDPOINT, headers=headers, json=forward_payload, stream=True, timeout=(5, 120))
    except requests.RequestException as e:
        app.logger.exception("Error contacting upstream service")
        return jsonify({"error": "Failed to contact upstream service", "details": str(e)}), 502

    # Build headers to return to client — filter hop-by-hop and content-encoding (Flask will handle)
    forwarded_headers = {}
    for k, v in upstream.headers.items():
        if k.lower() in HOP_BY_HOP_HEADERS:
            continue
        # Do not forward server-specific security headers that may confuse client, optional
        forwarded_headers[k] = v

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(generate(), status=upstream.status_code, headers=forwarded_headers, content_type=upstream.headers.get("Content-Type", None))


# Basic health route
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "1.0"}), 200


if __name__ == "__main__":
    # For local dev only; for concurrency use Gunicorn as shown below.
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
