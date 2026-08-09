import smtplib
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import config

logger = logging.getLogger(__name__)


def send_email(subject: str, html_body: str):
    if not (config.SMTP_USER and config.SMTP_PASSWORD and config.EMAIL_TO):
        logger.warning("Email not configured (SMTP_USER/SMTP_PASSWORD/EMAIL_TO) — skipping send.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = config.EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=context) as server:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.EMAIL_FROM, [config.EMAIL_TO], msg.as_string())
        logger.info("Alert email sent: %s", subject)
        return True
    except Exception as e:
        logger.error("Failed to send email: %s", e)
        return False


def build_alert_email(alerts, funds_summary):
    level_color = {"CRITICAL": "#d32f2f", "WARNING": "#f57c00", "INFO": "#1976d2"}
    rows = "".join(
        f'<tr><td style="padding:6px;color:{level_color.get(a["level"], "#333")};font-weight:bold">{a["level"]}</td>'
        f'<td style="padding:6px">{a["symbol"]}</td>'
        f'<td style="padding:6px">{a["message"]}</td></tr>'
        for a in alerts
    )
    html = f"""
    <html><body style="font-family:sans-serif">
    <h2>Kite Portfolio Risk Alert</h2>
    <p>Available cash: ₹{funds_summary.get('available_cash') or 'N/A'} |
       Margin used: ₹{funds_summary.get('used_margin') or 'N/A'}</p>
    <table style="border-collapse:collapse;width:100%">
      <tr style="background:#eee"><th style="padding:6px;text-align:left">Level</th>
        <th style="padding:6px;text-align:left">Symbol</th>
        <th style="padding:6px;text-align:left">Details</th></tr>
      {rows}
    </table>
    <p style="color:#888;font-size:12px">
      This is an automated heuristic check, not a margin call from your broker.
      Verify in Kite Console before taking action.
    </p>
    </body></html>
    """
    return html
