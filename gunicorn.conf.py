# Gunicorn configuration for CryptoApp
import multiprocessing

# Keep the app bound to localhost; expose via Nginx
bind = "127.0.0.1:5001"

# 1 worker to stay within Pi 4 (4GB) memory budget.
workers = 1
worker_class = "gthread"  # good for mixed I/O
threads = 4  # 2 was too few: SSE + health poll + ticker can saturate both slots
# NOTE: preload_app disabled — with 1 worker it has no COW benefit,
# and it kills the scan scheduler thread on fork.

# Timeouts — agent orchestrator can take up to 120s
timeout = 120
graceful_timeout = 30
keepalive = 2

# Cap worker memory — auto-restart if it leaks beyond 512 MB
max_requests = 500
max_requests_jitter = 50

# Logging
accesslog = "-"  # stdout
errorlog = "-"   # stderr
loglevel = "info"

# gunicorn 26+ introduces a control socket at ~/.gunicorn which fails on
# read-only or restricted home dirs; redirect to /tmp.
control_socket = "/tmp/cryptoapp-gunicorn.sock"
