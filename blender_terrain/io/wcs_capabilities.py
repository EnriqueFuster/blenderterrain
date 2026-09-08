"""Parse the WCS 2.0 metadata needed by coverage providers."""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree

from ..errors import ProviderContractChanged

_WCS_NAMESPACE = "http://www.opengis.net/wcs/2.0"


@dataclass(frozen=True, slots=True)
class WCSCapabilities:
    version: str
    coverage_ids: tuple[str, ...]
    formats: tuple[str, ...]


def parse_wcs_capabilities(
    xml: bytes,
    expected_coverage_id: str,
    required_format: str,
) -> WCSCapabilities:
    """Validate the advertised coverage and output format."""

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ProviderContractChanged("WCS capabilities response is not valid XML") from exc
    if root.tag != f"{{{_WCS_NAMESPACE}}}Capabilities" or root.attrib.get("version") != "2.0.1":
        raise ProviderContractChanged("WCS requires the verified 2.0.1 contract")

    namespace = {"wcs": _WCS_NAMESPACE}
    coverage_ids = _texts(root, ".//wcs:CoverageId", namespace)
    formats = _texts(root, ".//wcs:formatSupported", namespace)
    if expected_coverage_id not in coverage_ids:
        raise ProviderContractChanged(f"WCS coverage {expected_coverage_id} is missing")
    if required_format not in formats:
        raise ProviderContractChanged(f"WCS format {required_format} is missing")
    return WCSCapabilities("2.0.1", coverage_ids, formats)


def _texts(
    root: ElementTree.Element,
    path: str,
    namespace: dict[str, str],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            value
            for node in root.findall(path, namespace)
            if (value := (node.text or "").strip())
        )
    )
