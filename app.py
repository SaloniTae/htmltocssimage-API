#!/usr/bin/env python3
# app.py
import os
import io
import json
import random
import logging
from typing import Tuple, Dict, Optional
from flask import Flask, request, jsonify, send_file, abort
import requests

# --- Configuration ---
API_KEY = os.environ.get("HTMLCSI_API_KEY", "OTTONRENT")
STATUS_ENDPOINT = os.environ.get("STATUS_ENDPOINT", "http://166.0.242.212:7777/status")
POST_ENDPOINT = os.environ.get("POST_ENDPOINT", "https://htmlcsstoimage.com/image-demo")
HOMEPAGE = os.environ.get("HOMEPAGE", "https://htmlcsstoimage.com/")

# maximum size for incoming JSON (prevent huge uploads)
MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2 MiB for HTML payloads — tune if needed

# A small pool of realistic user agents to choose from
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

# Accept-Language options
LOCALES = ["en-US,en;q=0.9", "en-GB,en;q=0.9", "en-IN,en;q=0.9", "fr-FR,fr;q=0.9,en;q=0.8"]

# Default payload template for the htmlcsstoimage API
DEFAULT_PAYLOAD = {
    "html": None,
    "selector": "#qr-container",
    "full_screen": False,
    "render_when_ready": False,
    "color_scheme": "light",
    "timezone": "UTC",
    "block_consent_banners": False
}

# Flask app
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
logging.basicConfig(level=logging.INFO)


# --- Utilities ---
def pick_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def generate_ch_ua_from_ua(ua: str) -> Tuple[str, str, str]:
    """
    Generate approximate Sec-CH-UA header values based on UA string.
    This is heuristic and only aims to create consistent-looking headers.
    """
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
    """Generate a plausible public IPv4 to use in X-Forwarded-For (not guaranteed unique)."""
    # Avoid RFC1918 private ranges
    while True:
        ip = ".".join(str(random.randint(1, 254)) for _ in range(4))
        first = int(ip.split(".")[0])
        # skip private blocks
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
        # Client Hints (approximate)
        "Sec-CH-UA": ch_brand,
        "Sec-CH-UA-Mobile": ch_mobile,
        "Sec-CH-UA-Platform": ch_platform,
        "Origin": HOMEPAGE,
        "Referer": HOMEPAGE,
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1"
    }
    # Occasionally include a pseudo-remote IP to look like a different client (useful behind proxies)
    headers["X-Forwarded-For"] = random_ipv4_public()
    return headers


def fetch_status(session: requests.Session) -> Tuple[str, Optional[str]]:
    """
    Call the STATUS_ENDPOINT and return cookie header string and request verification token.
    Raises requests.HTTPError on bad status.
    """
    resp = session.get(STATUS_ENDPOINT, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    cookies = data.get("cookies", [])
    cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies]) if cookies else ""

    token = (data.get("requestVerificationToken")
             or data.get("__RequestVerificationToken")
             or data.get("RequestVerificationToken"))

    return cookie_str, token


def call_image_api(html: str, extra_payload: dict = None) -> Tuple[bytes, str]:
    """
    Perform POST to the html -> image service and return (content, content_type).
    Raises requests.HTTPError if the remote call fails.
    """
    s = requests.Session()
    # Generate realistic fingerprint headers
    headers = generate_fingerprint_headers()
    # Fetch the /status to obtain cookies + token
    cookie_str, token = fetch_status(s)
    if cookie_str:
        headers["Cookie"] = cookie_str
    if token:
        headers["requestverificationtoken"] = token

    # Add a few fixed headers similar to a browser request
    headers.setdefault("content-type", "application/json")
    headers.setdefault("sec-fetch-dest", "empty")
    headers.setdefault("sec-fetch-mode", "cors")
    headers.setdefault("sec-fetch-site", "same-origin")

    payload = DEFAULT_PAYLOAD.copy()
    payload["html"] = html
    if extra_payload:
        payload.update(extra_payload)

    resp = s.post(POST_ENDPOINT, headers=headers, data=json.dumps(payload), timeout=40)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if content_type.startswith("image/"):
        return resp.content, content_type
    else:
        # If non-image returned, attempt to give helpful error
        try:
            error_json = resp.json()
        except Exception:
            raise RuntimeError(f"Non-image response from image service. Status: {resp.status_code}, Body: {resp.text[:1000]}")
        raise RuntimeError(f"Image service returned JSON error: {error_json}")


# --- API endpoints ---
def _require_api_key() -> None:
    # API key may be provided as header X-API-KEY or Authorization: Bearer <key>
    header_key = request.headers.get("X-API-KEY") or ""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        header_key = auth.split(None, 1)[1].strip()
    if not header_key or header_key != API_KEY:
        abort(401, description="Invalid or missing API key.")


@app.route("/convert", methods=["POST"])
def convert_endpoint():
    """
    POST /convert
    Headers:
      - X-API-KEY: <key>    (or Authorization: Bearer <key>)
    JSON body:
      {
        "html": "<html string>",            # optional if you want to use a stored template
        "selector": "#qr-container",        # optional overrides
        "full_screen": false,
        ... any other keys to merge into DEFAULT_PAYLOAD ...
      }
    Response:
      - image/png binary (Content-Type set by remote service) on success
      - JSON error on failure
    """
    _require_api_key()

    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    body = request.get_json()
    html = body.get("html")
    if not html:
        return jsonify({"error": "Missing 'html' field in JSON body"}), 400

    # Extract optional fields to pass to the remote POST
    extras = {}
    for k in ("selector", "full_screen", "render_when_ready", "color_scheme", "timezone", "block_consent_banners"):
        if k in body:
            extras[k] = body[k]

    try:
        content, content_type = call_image_api(html, extra_payload=extras)
    except requests.HTTPError as e:
        logging.exception("HTTP error calling remote image API")
        # bubble up remote status code if available
        return jsonify({"error": "Remote service HTTP error", "details": str(e)}), 502
    except Exception as e:
        logging.exception("Error calling remote image API")
        return jsonify({"error": "Failed to generate image", "details": str(e)}), 500

    # Serve binary image back to the caller
    return send_file(io.BytesIO(content),
                     mimetype=content_type,
                     as_attachment=False,
                     download_name="result.png")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "1.0"})


if __name__ == "__main__":
    # For development only; use Gunicorn in production
    app.run(host="0.0.0.0", port=8000, debug=True, threaded=True)
