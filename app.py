from flask import Flask, request, jsonify, Response, abort
import requests

app = Flask(__name__)

# Hard-coded API key
INTERNAL_API_KEY = "OTTONRENT"

HTML_TO_IMAGE_URL = "https://htmlcsstoimage.com/demo_run"


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
            "Origin": "https://htmlcsstoimage.com",
            "Referer": "https://htmlcsstoimage.com/",
            "Cookie": "_gid=GA1.2.1136614202.1756834491; hcti.af=CfDJ8JFEjB_iVf9BjBc7Rw9QVVt_N43FaTzAAbvjCdvt5K6oy3zEJ_15mneJteQx4zoT6ioGpv8B86Rg1bPTCjdsF0QV5GTLjUWVjJd0P9DFqPrqwuY9GAv0ykmXbuZD2_G9DIkghj-oBAU4_TUrp4UJosY; _ga_JLLLQJL669=GS2.1.s1756830587$o3$g1$t1756834492$j54$l0$h0; _ga=GA1.1.790635408.1756834491; _gat_gtag_UA_32961413_2=1",
            "requestverificationtoken": "CfDJ8JFEjB_iVf9BjBc7Rw9QVVtqL0Jmano84runj1RszkN57j3PL8Bne3vfLoTBhuVmN9DzxN2QP6QkRtSZWbvZ3q-qf9SNoMMyhFfSmcB6Xf32q-Q8fcr3hNVevgAwHLPYZEVMVgJtydvyTY9syOyhuVQ"
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
