# TradeX Pro AI Gateway (separate service)

This is a **read-only** Upstox market-data gateway for the TradeX Pro UI. It does not place, modify, or cancel real orders.

## Railway deployment

1. Create a **new** Railway service named `tradex-pro-ai-gateway`. Do not change `ai-trading-brain` or the existing `ai-trading-upstox-gateway`.
2. Deploy this folder/repository.
3. Set Railway variables:
   - `UPSTOX_CLIENT_ID` — Upstox API app client ID
   - `UPSTOX_CLIENT_SECRET` — Upstox API app secret (keep private)
   - `UPSTOX_REDIRECT_URI` — exact callback URL, e.g. `https://YOUR-GATEWAY-DOMAIN/upstox/callback`
   - `FLASK_SECRET_KEY` — a long random secret
   - `ALLOWED_ORIGINS` — TradeX UI origin, e.g. `https://tradex-pro-ai-ui-production.up.railway.app`
   - Optional `UPSTOX_ACCESS_TOKEN` — if you already manage a valid token securely; otherwise leave unset and use OAuth login.
4. In the Upstox developer console, set the redirect URL to exactly the same `UPSTOX_REDIRECT_URI`.
5. Set the start command to `gunicorn app:app --bind 0.0.0.0:$PORT` if Railway does not detect it automatically.
6. After deploy, open `/upstox/login` on the new gateway domain and approve access.
7. Check `/upstox/status`, then test `/market/ltp?instrument_key=NSE_INDEX%7CNifty%2050`.

## Routes

- `GET /` — health
- `GET /upstox/login` — start OAuth
- `GET /upstox/callback` — OAuth callback; access token stays server-side
- `GET /upstox/status` — connection status
- `GET /market/ltp?instrument_key=...`
- `GET /market/candles?instrument_key=...&interval=5m`
- `GET /options/contracts?instrument_key=...&expiry_date=YYYY-MM-DD`
- `GET /options/chain?instrument_key=...&expiry_date=YYYY-MM-DD`

## Security notes

- Never put the client secret or access token in frontend HTML/JavaScript.
- Do not paste credentials or tokens into chat.
- OAuth token is held in server memory in this starter version and is cleared on restart; log in again after restart. For production persistence, use a secure server-side token store.
- Configure `ALLOWED_ORIGINS` to your exact UI origin before connecting the frontend.
- No real-order endpoint is included.
