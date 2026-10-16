import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, redirect, request, session
from flask_cors import CORS

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
CORS(app, resources={r"/*": {"origins": os.environ.get("ALLOWED_ORIGINS", "*").split(",")}})

UPSTOX_BASE = "https://api.upstox.com"
UPSTOX_LOGIN = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
API_VERSION = os.environ.get("UPSTOX_API_VERSION", "2.0")
IST = ZoneInfo("Asia/Kolkata")

# Token is deliberately server-side only. Railway restarts clear it; re-login after restart.
_access_token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()


def _json_error(message, status=400, details=None):
    payload = {"ok": False, "error": message}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status


def _headers():
    if not _access_token:
        return None
    return {
        "Authorization": f"Bearer {_access_token}",
        "Accept": "application/json",
        "Api-Version": API_VERSION,
    }


def _upstox_get(url, params=None):
    headers = _headers()
    if not headers:
        return None, ({"ok": False, "error": "Upstox not connected. Open /upstox/login first."}, 401)
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:500]}
        if not resp.ok:
            return None, ({"ok": False, "error": "Upstox API request failed", "upstox_status": resp.status_code, "details": body}, resp.status_code if 400 <= resp.status_code < 600 else 502)
        return body, None
    except requests.RequestException as exc:
        return None, ({"ok": False, "error": "Could not reach Upstox API", "details": str(exc)}, 502)


@app.get("/")
def health():
    return jsonify({
        "service": "tradex-pro-ai-gateway",
        "ok": True,
        "upstox_connected": bool(_access_token),
        "mode": "read-only",
    })


@app.get("/upstox/login")
def upstox_login():
    client_id = os.environ.get("UPSTOX_CLIENT_ID", "").strip()
    redirect_uri = os.environ.get("UPSTOX_REDIRECT_URI", "").strip()
    if not client_id or not redirect_uri:
        return _json_error("Set UPSTOX_CLIENT_ID and UPSTOX_REDIRECT_URI in Railway variables.", 503)
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return redirect(f"{UPSTOX_LOGIN}?{query}")


@app.get("/upstox/callback")
def upstox_callback():
    global _access_token
    code = request.args.get("code", "").strip()
    state = request.args.get("state", "").strip()
    expected_state = session.pop("oauth_state", "")
    if not code:
        return _json_error("Missing authorization code from Upstox.", 400)
    if not expected_state or not secrets.compare_digest(state, expected_state):
        return _json_error("OAuth state check failed. Start again at /upstox/login.", 400)

    required = ["UPSTOX_CLIENT_ID", "UPSTOX_CLIENT_SECRET", "UPSTOX_REDIRECT_URI"]
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        return _json_error("Missing Railway environment variables.", 503, missing)

    try:
        resp = requests.post(
            UPSTOX_TOKEN_URL,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            data={
                "code": code,
                "client_id": os.environ["UPSTOX_CLIENT_ID"].strip(),
                "client_secret": os.environ["UPSTOX_CLIENT_SECRET"].strip(),
                "redirect_uri": os.environ["UPSTOX_REDIRECT_URI"].strip(),
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
        body = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return _json_error("Token exchange failed.", 502, str(exc))

    if not resp.ok or not body.get("access_token"):
        return _json_error("Upstox did not return an access token.", 502, body)

    _access_token = body["access_token"]
    return jsonify({
        "ok": True,
        "message": "Upstox connected. Token is stored server-side in memory.",
        "note": "Do not share this response's secrets; this endpoint does not return the token.",
    })


@app.get("/upstox/status")
def upstox_status():
    return jsonify({"ok": True, "connected": bool(_access_token), "token_source": "environment" if os.environ.get("UPSTOX_ACCESS_TOKEN") else ("oauth-memory" if _access_token else "none")})


@app.get("/market/ltp")
def market_ltp():
    instrument_key = request.args.get("instrument_key", "").strip()
    if not instrument_key:
        return _json_error("Pass instrument_key, e.g. NSE_INDEX%7CNifty%2050.")
    body, err = _upstox_get(f"{UPSTOX_BASE}/v2/market-quote/ltp", {"instrument_key": instrument_key})
    if err:
        return jsonify(err[0]), err[1]
    return jsonify({"ok": True, "data": body.get("data", body)})


@app.get("/market/candles")
def market_candles():
    instrument_key = request.args.get("instrument_key", "").strip()
    interval = request.args.get("interval", "5m").strip().lower()
    if not instrument_key:
        return _json_error("Pass instrument_key, e.g. NSE_INDEX%7CNifty%2050.")
    if interval not in {"1m", "5m", "15m", "30m", "1h", "1d"}:
        return _json_error("interval must be one of 1m, 5m, 15m, 30m, 1h, 1d.")

    encoded_key = requests.utils.quote(instrument_key, safe="")
    now = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    # Intraday endpoint for current-day candles; use URL-encoded instrument key.
    if interval in {"1m", "5m", "15m", "30m"}:
        unit, number = interval[-1], interval[:-1]
        url = f"{UPSTOX_BASE}/v3/historical-candle/intraday/{encoded_key}/{number}_{unit}"
    elif interval == "1h":
        url = f"{UPSTOX_BASE}/v3/historical-candle/intraday/{encoded_key}/60_minutes"
    else:
        from_date = (now.date() - timedelta(days=365)).strftime("%Y-%m-%d")
        url = f"{UPSTOX_BASE}/v3/historical-candle/{encoded_key}/days/1/{today}/{from_date}"

    body, err = _upstox_get(url)
    if err:
        return jsonify(err[0]), err[1]
    return jsonify({"ok": True, "instrument_key": instrument_key, "interval": interval, "data": body.get("data", body)})


@app.get("/options/contracts")
def options_contracts():
    instrument_key = request.args.get("instrument_key", "").strip()
    expiry_date = request.args.get("expiry_date", "").strip()
    if not instrument_key:
        return _json_error("Pass instrument_key for the underlying, e.g. NSE_INDEX|Nifty 50.")
    params = {"instrument_key": instrument_key}
    if expiry_date:
        params["expiry_date"] = expiry_date
    body, err = _upstox_get(f"{UPSTOX_BASE}/v2/option/contract", params)
    if err:
        return jsonify(err[0]), err[1]
    return jsonify({"ok": True, "data": body.get("data", body)})


@app.get("/options/chain")
def options_chain():
    instrument_key = request.args.get("instrument_key", "").strip()
    expiry_date = request.args.get("expiry_date", "").strip()
    if not instrument_key or not expiry_date:
        return _json_error("Pass instrument_key and expiry_date (YYYY-MM-DD).")
    body, err = _upstox_get(f"{UPSTOX_BASE}/v2/option/chain", {
        "instrument_key": instrument_key,
        "expiry_date": expiry_date,
    })
    if err:
        return jsonify(err[0]), err[1]
    return jsonify({"ok": True, "data": body.get("data", body)})


@app.errorhandler(404)
def not_found(_):
    return _json_error("Route not found.", 404)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
