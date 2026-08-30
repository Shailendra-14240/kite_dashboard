# Kite Risk Dashboard

A local web dashboard that logs into your Zerodha Kite account, pulls your holdings,
F&O positions, and funds, and flags positions that are close to (or already) ITM
where you might be short on cash/margin to cover them. Sends email alerts.

**This tool only notifies — it does not place trades automatically.** Automated
order placement based on a heuristic risk check is genuinely risky (a bug or bad
price feed could trigger a wrong trade with real money), so the dashboard tells
you exactly what's at risk and why; you place the covering trade yourself in Kite.
If you later want a "confirm and place order" button added, that's a reasonable
next step, but it should require an explicit click every time — never fire silently.

## 1. Get Kite Connect API credentials

Kite Connect is Zerodha's trading API and is a **separate paid subscription**
(₹500 + GST/month at time of writing, billed separately from your trading account)
— your regular Zerodha login does not include API access by default.

1. Go to https://developers.kite.trade and log in with your Zerodha credentials.
2. Click **Create new app**.
   - App type: **Connect**
   - App name: anything, e.g. "Risk Dashboard"
   - Redirect URL: `http://127.0.0.1:5000/callback` (must match exactly what's in `.env`)
   - Description: anything
3. Subscribe to the Connect API for that app (you'll be prompted for billing).
4. Once approved, you'll see your app's **API key** and **API secret** on the dashboard —
   copy both.

Note: Kite access tokens expire daily (~6 AM IST), and Zerodha requires the login
to go through their actual login page (with your password + TOTP) — this can't be
automated end-to-end, by design, for security. So each trading day you'll open the
dashboard and click "Login with Kite" once.

## 2. Configure the app

```bash
cd kite_dashboard
cp .env.example .env
```

Edit `.env`:
- `KITE_API_KEY`, `KITE_API_SECRET` — from step 1.
- `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` — for alert emails. If using Gmail,
  create an **App Password** (requires 2FA enabled on the Google account) at
  https://myaccount.google.com/apppasswords — don't use your normal Gmail password.
- Adjust `ITM_WARNING_PCT` (how close to the strike counts as "near ITM"),
  `MARGIN_BUFFER_PCT` (spare margin you want to keep), and `CHECK_INTERVAL_MINUTES`
  (how often it re-checks) if you want different defaults.

## Optional: use Paytm Money for prices instead of Kite (free)

Kite's `ltp()`/`quote()` calls need the **paid** Kite Connect subscription.
If you'd rather not pay for that, you can use your existing **free** Paytm
Money account for spot prices while everything else (positions, holdings,
funds) still comes from Kite, which works fine on the free/Personal Kite app.

1. Go to https://developer.paytmmoney.com, log in, create an app — Trading
   API + Market Data API are included for free.
2. Set the redirect URL to `http://127.0.0.1:5000/paytm_callback`.
3. In `.env`, set:
   ```
   MARKET_DATA_PROVIDER=paytm
   PAYTM_API_KEY=your_key
   PAYTM_API_SECRET=your_secret
   ```
4. Install it with uv (preferred — see below) or, if you don't use uv,
   `pip install -r requirements.txt`.
5. Run the app, log in with **both** Kite and Paytm Money (two separate
   buttons will show on the login page), and you're set.

**Heads up on this integration specifically:** Paytm's exact API response
format for `security_master()` isn't something that could be verified ahead
of time — `paytm_market_data.py` is written to handle several likely field-
name variants, but if it can't map a symbol you'll see that clearly in the
dashboard error banner rather than a silent wrong price. If that happens,
run:
```
python debug_paytm.py
```
(after logging in via the dashboard) and share the output — the field-name
guesses in `paytm_market_data.py` can then be corrected exactly.

## 3. Install and run

uv is preferred (per the official Zerodha Kite Connect agent setup guide). If
you don't have it, install it with `winget install --id=astral-sh.uv -e`.

```bash
uv sync                # creates .venv and installs all deps
uv run python app.py   # run the dashboard
```

Using the Paytm Money integration? Then sync with the extra:

```bash
uv sync --extra paytm  # installs httpx + websocket-client for the vendored pyPMClient
```

Not using uv? `pip install -r requirements.txt` and `python app.py` work too
(the Paytm SDK is vendored in `pyPMClient/`, so no install is needed for it —
its deps are listed in `requirements.txt` under a comment).

Open http://127.0.0.1:5000 — click **Login with Kite**, log in as normal
(password + TOTP), and you'll land on the dashboard.

## What it checks

- **Funds**: available cash, margin used, net — from `kite.margins()`.
- **Holdings**: your equity portfolio — from `kite.holdings()`.
- **F&O positions**: from `kite.positions()`, cross-referenced with instrument
  data to get each option's strike, expiry, and underlying.
- **ITM proximity**: for every open option position, compares the underlying's
  live price to the strike. Flags:
  - `NEAR_ITM` — within `ITM_WARNING_PCT`% of the strike.
  - `ITM` — already past the strike.
  - `CRITICAL` — a **short** option that's ITM/near-ITM *and* your available
    cash looks insufficient to cover an estimated closing/margin requirement
    (estimated via Kite's `order_margins` API).
  - Physical settlement note for **stock** options within 2 days of expiry
    (NSE stock F&O settle in shares, not cash — index F&O are cash-settled).
- Alerts are emailed with a cooldown (`ALERT_COOLDOWN_MINUTES`) so you're not
  spammed every 5 minutes for the same issue.

## Important caveats

- This is a **monitoring aid**, not a broker margin call — always double-check
  actual obligations in Kite Console before acting, especially near expiry.
- The margin estimate uses Kite's order-margin API against your *current*
  position size, not a true what-if stress test at a hypothetical future price.
- Index-to-spot-symbol mapping covers NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY,
  SENSEX, BANKEX. Stock F&O map automatically via the NSE equity symbol.
- Runs locally on your machine — nothing is sent anywhere except the SMTP
  server you configure for email alerts.

## File overview

| File | Purpose |
|---|---|
| `app.py` | Flask routes (dashboard, login/callback, API) |
| `auth.py` | Kite login flow + daily token caching |
| `kite_client.py` | Thin wrapper over kiteconnect SDK calls |
| `risk_engine.py` | ITM proximity + margin coverage logic |
| `notifier.py` | Email sending |
| `scheduler.py` | Background job that refreshes data & triggers alerts |
| `templates/`, `static/` | Dashboard UI |
