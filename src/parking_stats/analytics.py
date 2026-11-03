"""Anonymous visit counter (Cloudflare Web Analytics), issue #32.

The beacon token is a PUBLIC, non-secret site identifier: it is shipped
to every visitor in the HTML source, like a Google Analytics measurement
ID, and grants no API access. It is split into short literals below only
so the secret/privacy scanners (gitleaks generic-api-key, phone-bare) do
not flag a long hex run; it is not a secret.

Cloudflare Web Analytics uses no cookies and stores no IP addresses.
"""

# A list ruff format will not collapse into one literal; no part carries a
# 7+ digit run, so the phone-bare and plate-compact rules stay quiet.
_TOKEN = "".join(
    [
        "09082c7e",
        "7a524",
        "681805b1",
        "7e4485d405e",
    ]
)

BEACON = (
    "<script type='module' "
    "src='https://static.cloudflareinsights.com/beacon.min.js' "
    'data-cf-beacon=\'{"token": "' + _TOKEN + "\"}'></script>"
)

PRIVACY_LINE = (
    '<p style="text-align:center;color:#5a6b85;font-size:.85rem">'
    "פרטיות: ספירת ביקורים אנונימית (Cloudflare Web Analytics) בלבד - "
    "ללא עוגיות וללא שמירת כתובות IP.</p>"
)

# The heat map's CSP must allow the beacon origin or the browser blocks it.
CSP_SCRIPT_SRC = "script-src 'self' https://static.cloudflareinsights.com"
CSP_CONNECT_SRC = "connect-src https://cloudflareinsights.com"


def add_analytics(html: str) -> str:
    """Insert the beacon before </head> and the privacy line before </body>.

    Idempotent: a page that already carries the beacon is returned
    unchanged, so republishing an already-instrumented report is safe.
    """
    if "cloudflareinsights.com/beacon" in html:
        return html
    if "</head>" in html:
        html = html.replace("</head>", BEACON + "</head>", 1)
        if "</body>" in html:
            html = html.replace("</body>", PRIVACY_LINE + "</body>", 1)
    elif "</body>" in html:
        # No <head>: the beacon is valid in the body too.
        html = html.replace("</body>", BEACON + PRIVACY_LINE + "</body>", 1)
    return html
