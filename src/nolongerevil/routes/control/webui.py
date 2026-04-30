"""Web UI routes - serves the device management HTML interface."""

from pathlib import Path

from aiohttp import web

from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)

# Path to HTML template (CSS and JS are inlined to avoid ingress path issues)
TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
INDEX_TEMPLATE = TEMPLATE_DIR / "index.html"
USAGE_HISTORY_TEMPLATE = TEMPLATE_DIR / "usage_history.html"
MANIFEST = STATIC_DIR / "manifest.webmanifest"


async def handle_webui(request: web.Request) -> web.Response:
    """Handle GET / - serve the web UI.

    Reads X-Ingress-Path header for Home Assistant ingress support
    and injects it into the HTML via a data attribute.
    """
    ingress_path = request.headers.get("X-Ingress-Path", "")
    html = INDEX_TEMPLATE.read_text()

    # Inject the ingress path via data attribute on body tag
    html = html.replace("<body>", f'<body data-ingress-path="{ingress_path}">')

    return web.Response(text=html, content_type="text/html")


async def handle_usage_history_dashboard(request: web.Request) -> web.Response:
    """Handle GET /usage-history - serve the expanded usage history dashboard."""
    ingress_path = request.headers.get("X-Ingress-Path", "")
    html = USAGE_HISTORY_TEMPLATE.read_text()
    html = html.replace("<body>", f'<body data-ingress-path="{ingress_path}">')
    return web.Response(text=html, content_type="text/html")


async def handle_manifest(_request: web.Request) -> web.Response:
    """Serve the PWA web manifest."""
    return web.Response(
        text=MANIFEST.read_text(encoding="utf-8"),
        content_type="application/manifest+json",
    )


def create_webui_routes(app: web.Application) -> None:
    """Register web UI routes."""
    app.router.add_get("/", handle_webui)
    app.router.add_get("/usage-history", handle_usage_history_dashboard)
    app.router.add_static("/assets/", STATIC_DIR, name="control_assets")
    app.router.add_get("/manifest.webmanifest", handle_manifest)
    logger.info("Web UI routes registered")
