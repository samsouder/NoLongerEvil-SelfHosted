"""Tests for Web UI PWA and icon assets."""

import pytest

from nolongerevil.main import create_control_app
from nolongerevil.routes.control.webui import STATIC_DIR

PUBLIC_ICON_FILENAMES = {
    "apple-touch-icon.png",
    "favicon-16x16.png",
    "favicon-32x32.png",
    "icon-192.png",
    "icon-512.png",
}


@pytest.fixture
async def webui_client(
    aiohttp_client,
    state_service,
    subscription_manager,
    device_availability,
):
    """Create a control app client for Web UI asset tests."""
    app = create_control_app(state_service, subscription_manager, device_availability)
    return await aiohttp_client(app)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/usage-history"])
async def test_webui_pages_reference_pwa_assets(webui_client, path):
    """Both Web UI pages advertise the manifest and platform icons."""
    resp = await webui_client.get(path)
    assert resp.status == 200

    html = await resp.text()
    assert 'name="theme-color" content="#111827"' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'name="apple-mobile-web-app-title" content="No Longer Evil"' in html
    assert 'name="apple-mobile-web-app-status-bar-style" content="black-translucent"' in html
    assert 'rel="manifest" href="manifest.webmanifest"' in html
    assert 'rel="apple-touch-icon" sizes="180x180" href="assets/icons/apple-touch-icon.png"' in html
    assert 'rel="icon" type="image/png" sizes="32x32" href="assets/icons/favicon-32x32.png"' in html
    assert 'rel="icon" type="image/png" sizes="16x16" href="assets/icons/favicon-16x16.png"' in html
    assert 'rel="shortcut icon" type="image/png" href="assets/icons/favicon-32x32.png"' in html
    assert "nle-icon.png" not in html
    assert "nle-favicon.png" not in html


@pytest.mark.asyncio
async def test_web_manifest_is_served(webui_client):
    """Serve the PWA manifest with the dark NLE theme color."""
    resp = await webui_client.get("/manifest.webmanifest")
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("application/manifest+json")

    manifest = await resp.json(content_type=None)
    assert manifest["name"] == "No Longer Evil"
    assert manifest["theme_color"] == "#111827"
    assert manifest["background_color"] == "#111827"
    assert manifest["start_url"] == "."
    assert manifest["scope"] == "."
    assert {icon["src"] for icon in manifest["icons"]} == {
        "assets/icons/icon-192.png",
        "assets/icons/icon-512.png",
    }
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "512x512"}


def test_static_icons_only_include_resized_public_assets():
    """Keep original source images out of the publicly mounted icon directory."""
    assert {path.name for path in (STATIC_DIR / "icons").iterdir()} == PUBLIC_ICON_FILENAMES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/assets/icons/apple-touch-icon.png",
        "/assets/icons/favicon-16x16.png",
        "/assets/icons/favicon-32x32.png",
        "/assets/icons/icon-192.png",
        "/assets/icons/icon-512.png",
    ],
)
async def test_webui_icons_are_served(webui_client, path):
    """Serve public resized icon URLs as PNGs."""
    resp = await webui_client.get(path)
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("image/png")
    assert (await resp.read()).startswith(b"\x89PNG")
