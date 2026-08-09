import logging
from flask import Flask, render_template, redirect, request, jsonify, session

from config import config
from auth import kite_auth, token_store
from scheduler import state, refresh_and_check, start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY

USING_PAYTM = config.MARKET_DATA_PROVIDER == "paytm"


def logged_in() -> bool:
    kite_ok = kite_auth is not None and token_store.load() is not None
    if not USING_PAYTM:
        return kite_ok
    from paytm_auth import paytm_auth, paytm_token_store
    paytm_ok = paytm_auth is not None and paytm_token_store.load() is not None
    return kite_ok and paytm_ok


@app.route("/")
def dashboard():
    if not logged_in():
        from paytm_auth import paytm_auth
        kite_logged_in = kite_auth is not None and token_store.load() is not None
        paytm_logged_in = (not USING_PAYTM) or (paytm_auth is not None and paytm_auth.get_authenticated_client() is not None)
        return render_template(
            "login.html",
            kite_login_url=kite_auth.login_url() if (kite_auth and not kite_logged_in) else None,
            paytm_login_url=paytm_auth.login_url() if (USING_PAYTM and paytm_auth and not paytm_logged_in) else None,
            configured=kite_auth is not None and (not USING_PAYTM or paytm_auth is not None),
            kite_logged_in=kite_logged_in,
            paytm_logged_in=paytm_logged_in,
            using_paytm=USING_PAYTM,
        )
    return render_template("dashboard.html")


@app.route("/login")
def login():
    if kite_auth is None:
        return "Kite API key/secret not configured. Fill in .env first.", 400
    return redirect(kite_auth.login_url())


@app.route("/callback")
def callback():
    request_token = request.args.get("request_token")
    status = request.args.get("status")
    if status != "success" or not request_token:
        return f"Login failed or was cancelled. Status: {status}", 400
    kite_auth.generate_session(request_token)
    refresh_and_check()  # populate data immediately after login
    return redirect("/")


@app.route("/logout")
def logout():
    token_store.clear()
    if USING_PAYTM:
        from paytm_auth import paytm_token_store
        paytm_token_store.clear()
    return redirect("/")


@app.route("/paytm_login")
def paytm_login():
    from paytm_auth import paytm_auth
    if paytm_auth is None:
        return "Paytm API key/secret not configured. Fill in .env first.", 400
    return redirect(paytm_auth.login_url())


@app.route("/paytm_callback")
def paytm_callback():
    from paytm_auth import paytm_auth
    request_token = request.args.get("request_token") or request.args.get("requestToken")
    if not request_token:
        return f"Paytm login failed or was cancelled. Query params: {dict(request.args)}", 400
    paytm_auth.generate_session(request_token)
    refresh_and_check()
    return redirect("/")


@app.route("/api/data")
def api_data():
    if not logged_in():
        return jsonify({"error": "not_logged_in"}), 401
    return jsonify(state)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    if not logged_in():
        return jsonify({"error": "not_logged_in"}), 401
    refresh_and_check()
    return jsonify(state)


if __name__ == "__main__":
    start_scheduler()
    app.run(host="127.0.0.1", port=config.FLASK_PORT, debug=False)
