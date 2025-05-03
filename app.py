from flask import Flask, request, Response, abort
import requests

app = Flask(__name__)

# Hard-coded API key
INTERNAL_API_KEY = "OTTONRENT"

HTML_TO_IMAGE_URL = "https://htmlcsstoimage.com/demo_run"


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "alive"}), 200
    
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

    # 3) Build payload to forward
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

    # 4) Forward request
    upstream = requests.post(
        HTML_TO_IMAGE_URL,
        headers={
            "Accept":        "*/*",
            "Content-Type":  "application/json",
        },
        json=forward_payload,
        stream=True
    )

    # 5) Stream the response back
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
