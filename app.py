from flask import Flask, request, jsonify, Response, abort
import requests

app = Flask(__name__)

# Hard-coded API key
INTERNAL_API_KEY = "OTTONRENT"

HTML_TO_IMAGE_URL = "https://htmlcsstoimage.com/image-demo"
STATUS_URL = "http://166.0.242.212:7777/status"
TIMEOUT = 20  # seconds

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

    # 3) Fetch /status to get cookie value and token (priority: GET)
    try:
        status_resp = requests.get(STATUS_URL, timeout=TIMEOUT)
        status_resp.raise_for_status()
        status_json = status_resp.json()
    except Exception as e:
        abort(502, f"Failed to fetch status endpoint: {e}")

    # 4) Extract single cookie 'value' (prefer hcti.af)
    cookies_arr = status_json.get("cookies", []) or []
    cookie_value = None
    # prefer cookie named 'hcti.af' if present
    for c in cookies_arr:
        if isinstance(c, dict) and c.get("name") == "hcti.af" and c.get("value"):
            cookie_value = c.get("value")
            break
    # fallback to first cookie.value if hcti.af not present
    if not cookie_value and cookies_arr:
        first = cookies_arr[0]
        if isinstance(first, dict):
            cookie_value = first.get("value")

    # 5) Extract token (try several common keys)
    token = status_json.get("requestVerificationToken") \
            or status_json.get("requestverificationtoken") \
            or status_json.get("RequestVerificationToken") \
            or status_json.get("__RequestVerificationToken") \
            or status_json.get("token")
    # last resort: search any key with 'token' in its name
    if not token:
        for k, v in status_json.items():
            if isinstance(k, str) and ("token" in k.lower() or "verification" in k.lower()):
                token = v
                break

    if not cookie_value or not token:
        abort(502, "Missing cookie value or token from /status response")

    # 6) Build payload to forward
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

    # 7) Forward request with extracted cookie "value" and token in headers
    headers = {
        "Accept":       "*/*",
        "Content-Type": "application/json",
        # as you requested: Cookie header contains the cookie "value" (not name=value)
        "Cookie": cookie_value,
        # lowercase header name exactly as you wanted
        "requestverificationtoken": token
    }

    try:
        upstream = requests.post(
            HTML_TO_IMAGE_URL,
            headers=headers,
            json=forward_payload,
            stream=True,
            timeout=TIMEOUT
        )
    except Exception as e:
        abort(502, f"Failed to call upstream HTML->image service: {e}")

    # 8) Stream the response back
    return Response(
        upstream.iter_content(chunk_size=4096),
        status=upstream.status_code,
        headers={
            k: v for k, v in upstream.headers.items()
            if k.lower() not in ("content-encoding", "transfer-encoding")
        }
    )

if __name__ == "__main__":
    # Render will auto-detect and run this via gunicorn
    app.run(host="0.0.0.0", port=5000)
