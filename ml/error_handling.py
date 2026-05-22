"""
Error handling utilities — retry logic & error alert emails.
"""

import os
import time
import logging
import smtplib
import functools
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Retry Decorator ─────────────────────────────────────────


def retry(max_attempts: int = 3, base_delay: float = 1.0, backoff: float = 2.0,
          exceptions: tuple = (Exception,), warn_on_retry: bool = True):
    """
    Retry decorator with exponential backoff.

    Args:
        max_attempts:   Maximum number of attempts (including the first).
        base_delay:     Initial delay in seconds between retries.
        backoff:        Multiplier applied to delay after each failure.
        exceptions:     Tuple of exception types that trigger a retry.
        warn_on_retry:  If True (default), log intermediate failures at WARNING.
                        Set False to use DEBUG — useful for self-healing transient
                        errors (e.g. stale connections) that always succeed on retry.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise
                    log_fn = logger.warning if warn_on_retry else logger.debug
                    log_fn(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}  "
                        f"— retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    delay *= backoff
            raise last_exc  # should never reach here
        return wrapper
    return decorator


async def async_retry(coro_func, *args, max_attempts: int = 3,
                      base_delay: float = 1.0, backoff: float = 2.0,
                      exceptions: tuple = (Exception,), **kwargs):
    """
    Retry helper for async coroutines.
    Usage: result = await async_retry(some_async_fn, arg1, arg2)
    """
    import asyncio

    delay = base_delay
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_func(*args, **kwargs)
        except exceptions as e:
            last_exc = e
            if attempt == max_attempts:
                logger.error(
                    f"{coro_func.__name__} failed after {max_attempts} attempts: {e}"
                )
                raise
            logger.warning(
                f"{coro_func.__name__} attempt {attempt}/{max_attempts} failed: {e}  "
                f"— retrying in {delay:.1f}s"
            )
            await asyncio.sleep(delay)
            delay *= backoff
    raise last_exc


# ─── Error Alert Emails ──────────────────────────────────────

_last_alert_time: dict = {}  # category → timestamp (throttle repeated alerts)
ALERT_COOLDOWN = 300  # Don't send same alert category more than once per 5 min


def send_error_alert(
    subject: str,
    body: str,
    category: str = "general",
    force: bool = False,
) -> bool:
    """
    Send an error alert email to the configured notification address.
    Throttled per category to avoid spamming.

    Returns True if sent, False if throttled or failed.
    """
    # Throttle check
    now = time.time()
    if not force and category in _last_alert_time:
        elapsed = now - _last_alert_time[category]
        if elapsed < ALERT_COOLDOWN:
            logger.debug(
                f"Alert throttled ({category}) — last sent {elapsed:.0f}s ago"
            )
            return False

    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    to_addr = os.getenv("TRADE_NOTIFICATION_EMAIL", "")

    if not all([smtp_user, smtp_pass, to_addr]):
        logger.warning("Cannot send error alert — SMTP credentials not configured")
        return False

    full_subject = f"[CryptoApp ALERT] {subject}"
    body_escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    timestamp = datetime.utcnow().isoformat() + "Z"
    host = os.uname().nodename
    html_body = f"""<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d0d14; color: #e2e8f0; padding: 20px;">
  <div style="max-width: 500px; margin: 0 auto; background: #151520; border-radius: 12px; border: 1px solid #2d3748; overflow: hidden;">
    <div style="background: linear-gradient(90deg, #e53e3e, #c53030); padding: 16px 20px;">
      <h2 style="margin: 0; color: white; font-size: 18px;">{subject}</h2>
    </div>
    <div style="padding: 20px;">
      <p style="font-size: 14px; line-height: 1.6; margin: 0 0 16px;">{body_escaped}</p>
      <div style="font-size: 11px; color: #a0aec0; border-top: 1px solid #2d3748; padding-top: 12px;">
        Category: {category}<br>
        Time: {timestamp}<br>
        Host: {host}
      </div>
    </div>
  </div>
</body>
</html>"""

    try:
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart("alternative")
        msg["Subject"] = full_subject
        msg["From"] = smtp_user
        msg["To"] = to_addr
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_addr, msg.as_string())

        _last_alert_time[category] = now
        logger.info(f"Error alert sent: {full_subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send error alert: {e}")
        return False


# ─── Convenience alert functions ──────────────────────────────

def alert_trade_failure(symbol: str, error: str, details: Optional[dict] = None):
    """Alert when a trade execution fails."""
    body = (
        f"Trade execution failed for {symbol}.\n\n"
        f"Error: {error}\n"
    )
    if details:
        body += f"Details: {details}\n"
    send_error_alert(
        subject=f"Trade Failed — {symbol}",
        body=body,
        category=f"trade_fail_{symbol}",
    )


def alert_api_quota(api_name: str, error: str):
    """Alert when an API quota is exhausted."""
    send_error_alert(
        subject=f"API Quota Exhausted — {api_name}",
        body=f"API quota exhausted for {api_name}.\n\nError: {error}",
        category=f"quota_{api_name}",
    )


def alert_exchange_down(exchange_id: str, error: str):
    """Alert when an exchange connection fails."""
    send_error_alert(
        subject=f"Exchange Down — {exchange_id}",
        body=f"Cannot connect to {exchange_id}.\n\nError: {error}",
        category=f"exchange_{exchange_id}",
    )


def alert_scan_failure(error: str):
    """Alert when the automated scan loop fails."""
    send_error_alert(
        subject="Scan Loop Failed",
        body=f"The automated scan loop encountered an error.\n\nError: {error}",
        category="scan_failure",
    )


def send_email_alert(subject: str, body: str, category: str = "general"):
    """Convenience alias used by scheduler and market monitor."""
    send_error_alert(subject=subject, body=body, category=category, force=True)
