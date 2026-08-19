"""Strict parsing of the CNIG temporary download hand-off page."""

from __future__ import annotations

from html.parser import HTMLParser

from blender_terrain.errors import CatalogContractChanged


class _PresignedURLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        if tag != "input":
            return
        attrs = {name: value or "" for name, value in attributes}
        if attrs.get("id") == "urlPregsigned":
            self.urls.append(attrs.get("value", ""))


def parse_presigned_download_url(html: str) -> str:
    """Extract exactly one non-empty delivery URL from a CNIG hand-off page."""

    parser = _PresignedURLParser()
    parser.feed(html)
    if len(parser.urls) != 1 or not parser.urls[0]:
        raise CatalogContractChanged(
            "CNIG download hand-off must contain exactly one temporary URL"
        )
    return parser.urls[0]
