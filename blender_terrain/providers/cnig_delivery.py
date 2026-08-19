"""Validation policy for temporary download capabilities issued by CNIG."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit
from xml.etree import ElementTree

from blender_terrain.errors import DownloadIntegrityError
from blender_terrain.models import CatalogItem


DELIVERY_HOST = "cnig-desarrollo-cdd-s3.s3.eu-south-2.amazonaws.com"
MAXIMUM_URL_LIFETIME_SECONDS = 7_200
REQUIRED_QUERY_FIELDS = {
    "X-Amz-Algorithm",
    "X-Amz-Credential",
    "X-Amz-Date",
    "X-Amz-Expires",
    "X-Amz-Signature",
    "X-Amz-SignedHeaders",
}


def normalized_resource_name(filename: str) -> str:
    """Normalize the separator and case differences observed between CNIG systems."""

    return filename.replace("_", "-").casefold()


def validate_presigned_download_url(
    url: str,
    item: CatalogItem,
    now: datetime | None = None,
) -> str:
    """Validate a short-lived S3 URL without storing or logging its secret query."""

    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != DELIVERY_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise DownloadIntegrityError("CNIG supplied an unauthorized delivery destination")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DownloadIntegrityError("CNIG supplied an invalid delivery port") from exc
    if port not in (None, 443):
        raise DownloadIntegrityError("CNIG supplied an unauthorized delivery port")

    delivered_name = unquote(PurePosixPath(parsed.path).name)
    if normalized_resource_name(delivered_name) != normalized_resource_name(item.filename):
        raise DownloadIntegrityError("Delivery object does not match the catalog filename")

    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise DownloadIntegrityError("CNIG supplied an invalid delivery query") from exc
    if set(query) != REQUIRED_QUERY_FIELDS or any(len(values) != 1 for values in query.values()):
        raise DownloadIntegrityError("CNIG supplied an unexpected delivery authorization")
    if query["X-Amz-Algorithm"][0] != "AWS4-HMAC-SHA256":
        raise DownloadIntegrityError("CNIG supplied an unsupported delivery algorithm")
    if query["X-Amz-SignedHeaders"][0] != "host":
        raise DownloadIntegrityError("CNIG supplied unexpected signed delivery headers")
    if not query["X-Amz-Credential"][0] or not query["X-Amz-Signature"][0]:
        raise DownloadIntegrityError("CNIG supplied an incomplete delivery authorization")

    try:
        issued_at = datetime.strptime(query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=UTC
        )
        lifetime_seconds = int(query["X-Amz-Expires"][0])
    except ValueError as exc:
        raise DownloadIntegrityError("CNIG supplied invalid delivery timing") from exc
    if not 1 <= lifetime_seconds <= MAXIMUM_URL_LIFETIME_SECONDS:
        raise DownloadIntegrityError("CNIG supplied an unsafe delivery lifetime")

    current_time = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=lifetime_seconds)
    if current_time < issued_at - timedelta(minutes=5) or current_time >= expires_at:
        raise DownloadIntegrityError("CNIG supplied an expired or not-yet-valid delivery URL")
    return url


def parse_storage_error_code(body: bytes) -> str | None:
    """Return a safe object-storage error code without exposing request details."""

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None
    code = root.findtext("Code")
    if not code or len(code) > 64 or not all(character.isalnum() for character in code):
        return None
    return code
