"""The /debug console follows the shared design foundation.

Colour comes from the copied `tokens.css` alone, the theme control and the
IBM Plex faces are served from the package, and state shows a glyph beside its
colour. The static route is part of the console, so eval mode hides it too.
"""

import re
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from anomaly_metric_creator import server
from anomaly_metric_creator.server_debug_ui import DEBUG_HTML

STATIC = Path(server.__file__).parent / "debug_static"
PROJECT = Path(__file__).resolve().parent.parent

# A colour written outside the token copy: a hex literal, a colour function, or
# a bare black/white keyword. `&#39;` and `#id` selectors are not colours.
RAW_COLOUR = re.compile(
    r"(?<![\w&])#[0-9a-fA-F]{3,8}(?![\w-])"
    r"|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color-mix)\("
    r"|:\s*(?:white|black)\b"
)


def _own_sources() -> dict[str, str]:
    """Every authored text of the console except the token copy."""
    sources = {"DEBUG_HTML": DEBUG_HTML}
    for path in sorted(STATIC.glob("*")):
        if path.suffix in {".css", ".js"} and path.name != "tokens.css":
            sources[path.name] = path.read_text(encoding="utf-8")
    return sources


def test_debug_ui_has_no_raw_colours() -> None:
    found = {
        name: sorted(set(RAW_COLOUR.findall(text)))
        for name, text in _own_sources().items()
        if RAW_COLOUR.search(text)
    }
    assert not found, f"colour literals outside tokens.css: {found}"


def test_raw_colour_pattern_catches_each_form() -> None:
    for sample in (
        "color: #fff;",
        "background: #0f1720",
        "border-color: rgb(1 2 3 / 4%)",
        "box-shadow: 0 0 1px rgba(0,0,0,.2)",
        "fill: oklch(50% 0 0)",
        "color: white",
    ):
        assert RAW_COLOUR.search(sample), sample
    for sample in ("&#39;", "#commands tr", "color: var(--color-ink)", "tr#row-12"):
        assert not RAW_COLOUR.search(sample), sample


def test_debug_ui_loads_foundation_assets_and_theme_control() -> None:
    for asset in ("tokens.css", "fonts.css", "debug.css", "theme.js"):
        assert f"/debug/static/{asset}" in DEBUG_HTML, asset
    for choice in ("dark", "light", "system"):
        assert f'data-theme-choice="{choice}"' in DEBUG_HTML, choice
    theme_tag = re.search(r"<script[^>]*theme\.js[^>]*>", DEBUG_HTML)
    assert theme_tag and "defer" not in theme_tag.group(0), "theme.js must run before first paint"
    assert "<style>" not in DEBUG_HTML


def test_debug_ui_state_grammar_pairs_each_state_with_its_glyph() -> None:
    for glyph in ("●", "▲", "■", "◌", "▨"):
        assert glyph in DEBUG_HTML, glyph
    css = (STATIC / "debug.css").read_text(encoding="utf-8")
    for token in ("--color-ok", "--color-caution", "--color-warning", "--color-unknown", "--color-focus"):
        assert f"var({token}" in css, token


def test_token_copy_and_fonts_ship_offline() -> None:
    tokens = (STATIC / "tokens.css").read_text(encoding="utf-8")
    assert "Instrument-panel foundation tokens" in tokens
    assert "light-dark(" in tokens and "--color-paper" in tokens
    fonts = (STATIC / "fonts.css").read_text(encoding="utf-8")
    assert "http" not in fonts, "fonts.css must name only files served by this server"
    urls = re.findall(r"url\(([^)]+)\)", fonts)
    assert urls
    for url in urls:
        assert (STATIC / url).is_file(), url
    assert (STATIC / "fonts" / "OFL.txt").is_file()


def test_pyproject_ships_the_static_folder() -> None:
    data = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    globs = data["tool"]["setuptools"]["package-data"]["anomaly_metric_creator"]
    assert "debug_static/*" in globs and "debug_static/fonts/*" in globs


def _get(base_url: str, path: str, headers=None):
    request = urllib.request.Request(base_url + path, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


@pytest.fixture
def served(amc, tmp_path):
    from test_server_eval_mode import _build_state

    def start(*, eval_mode=False, security=None):
        state = _build_state(amc, tmp_path, eval_mode=eval_mode)
        return server.start_test_server(state, security=security)

    return start


def test_static_route_serves_each_asset_with_its_type(served) -> None:
    httpd, base = served()
    try:
        expected = {
            "tokens.css": "text/css",
            "fonts.css": "text/css",
            "debug.css": "text/css",
            "theme.js": "text/javascript",
            "fonts/ibm-plex-sans-var.woff2": "font/woff2",
        }
        for name, content_type in expected.items():
            status, headers, body = _get(base, f"/debug/static/{name}")
            assert status == 200, name
            assert headers["content-type"].startswith(content_type), name
            assert body == (STATIC / name).read_bytes(), name  # resource-lint: allow
            assert headers["x-content-type-options"] == "nosniff"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_static_route_refuses_paths_outside_the_asset_list(served) -> None:
    httpd, base = served()
    try:
        for path in (
            "/debug/static/",
            "/debug/static/fonts",
            "/debug/static/nope.css",
            "/debug/static/../server.py",
            "/debug/static/%2e%2e/server.py",
            "/debug/static/fonts/../../server.py",
            "/debug/static/fonts/OFL.txt",
        ):
            status, _headers, _body = _get(base, path)
            assert status == 404, path
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_static_route_needs_no_token_like_the_shell(served) -> None:
    httpd, base = served(security=server.ServerSecurityConfig(auth_token="test-token"))
    try:
        assert _get(base, "/debug/static/tokens.css")[0] == 200
        assert _get(base, "/v1/state")[0] == 401
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_eval_mode_hides_the_static_route(served) -> None:
    httpd, base = served(eval_mode=True)
    try:
        assert _get(base, "/debug")[0] == 404
        assert _get(base, "/debug/static/tokens.css")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
