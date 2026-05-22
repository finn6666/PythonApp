#!/usr/bin/env python3
"""
CryptoApp — slim entry point.

All shared state, initialisation logic, and helpers live in services/app_state.py.
Route handlers are organised into Flask Blueprints under routes/.
"""

import os
import subprocess
import logging
from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__,
            template_folder='src/web/templates',
            static_folder='src/web/static')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    if os.environ.get('FLASK_ENV') == 'production' or not app.debug:
        raise RuntimeError('SECRET_KEY must be set in production — add it to .env')
    app.secret_key = os.urandom(32)
    logger.warning("No SECRET_KEY set — using random key (sessions won't persist across restarts)")

app.config.update(
    SESSION_COOKIE_SECURE=not app.debug,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

# ---------------------------------------------------------------------------
# Extensions — CORS + rate limiting
# ---------------------------------------------------------------------------
from extensions import limiter, init_cors  # noqa: E402

limiter.init_app(app)
init_cors(app)

# ---------------------------------------------------------------------------
# Shared state — initialise all ML / data components
# ---------------------------------------------------------------------------
import services.app_state as state       # noqa: E402
state.init_all()                         # ML, data pipeline, ADK, analyzer
state.start_idle_monitor()

# ---------------------------------------------------------------------------
# Start automated scan scheduler (if not in debug mode)
# ---------------------------------------------------------------------------
try:
    from ml.scan_loop import get_scan_loop  # noqa: E402
    scan_enabled = os.environ.get('SCAN_ENABLED', 'true').lower() in ('1', 'true', 'yes')
    if scan_enabled:
        _scanner = get_scan_loop()
        _scanner.start_scheduler()
except Exception as e:
    logger.warning(f'Scan scheduler not started: {e}')

# ---------------------------------------------------------------------------
# Register blueprints
# ---------------------------------------------------------------------------
from routes.coins import coins_bp        # noqa: E402
from routes.ml_routes import ml_bp       # noqa: E402
from routes.symbols import symbols_bp    # noqa: E402
from routes.trading import trading_bp    # noqa: E402
from routes.health import health_bp      # noqa: E402

app.register_blueprint(coins_bp)
app.register_blueprint(ml_bp)
app.register_blueprint(symbols_bp)
app.register_blueprint(trading_bp)
app.register_blueprint(health_bp)

# ---------------------------------------------------------------------------
# Template globals
# ---------------------------------------------------------------------------

def _git_sha() -> str:
    """Return the current git short SHA for cache-busting static assets."""
    try:
        sha = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return sha or 'dev'
    except Exception:
        return 'dev'

app.jinja_env.globals['asset_version'] = _git_sha()

# ---------------------------------------------------------------------------
# Top-level page routes
# ---------------------------------------------------------------------------

@app.before_request
def track_activity():
    """Track all requests to reset idle timer"""
    state.update_activity()

@app.route('/')
def index():
    """Serve the main page with dashboard improvements"""
    return render_template('index.html')

@app.route('/legacy')
def legacy():
    """Legacy route for the original 2100+ line HTML file"""
    return render_template('index.html')


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    try:
        port = int(os.environ.get('PORT', '5001'))
    except ValueError:
        port = 5001
    debug = os.environ.get('DEBUG', 'false').lower() in ('1', 'true', 'yes')
    logger.info(f'Starting on {host}:{port} (debug={debug})')
    app.run(host=host, port=port, debug=debug)
