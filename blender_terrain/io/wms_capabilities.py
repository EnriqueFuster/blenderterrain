"""Parse the small portion of WMS 1.3 capabilities required by PNOA."""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree

from ..errors import ProviderContractChanged

_WMS_NAMESPACE = "http://www.opengis.net/wms"


@dataclass(frozen=True, slots=True)
class WMSCapabilities:
    """Verified limits and inherited properties of one named WMS layer."""

    version: str
    layer_name: str
    formats: tuple[str, ...]
    crs: tuple[str, ...]
    max_width: int
    max_height: int


def parse_wms_capabilities(xml: bytes, expected_layer: str) -> WMSCapabilities:
    """Parse WMS capabilities and reject a changed or incomplete contract."""

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ProviderContractChanged("WMS capabilities response is not valid XML") from exc
    namespace = {"wms": _WMS_NAMESPACE}
    if root.tag != f"{{{_WMS_NAMESPACE}}}WMS_Capabilities":
        raise ProviderContractChanged("WMS capabilities root element is unsupported")
    version = root.attrib.get("version")
    if version != "1.3.0":
        raise ProviderContractChanged("PNOA requires the verified WMS 1.3.0 contract")

    get_map = root.find("wms:Capability/wms:Request/wms:GetMap", namespace)
    formats = tuple(
        value
        for node in (() if get_map is None else get_map.findall("wms:Format", namespace))
        if (value := (node.text or "").strip())
    )
    if "image/png" not in formats:
        raise ProviderContractChanged("PNOA WMS no longer advertises PNG maps")

    service = root.find("wms:Service", namespace)
    max_width = _positive_int(service, "MaxWidth", namespace)
    max_height = _positive_int(service, "MaxHeight", namespace)
    root_layer = root.find("wms:Capability/wms:Layer", namespace)
    if root_layer is None:
        raise ProviderContractChanged("WMS capabilities has no root layer")
    crs = _find_layer_crs(root_layer, expected_layer, (), namespace)
    if crs is None:
        raise ProviderContractChanged(f"WMS layer {expected_layer} is missing")
    return WMSCapabilities(version, expected_layer, formats, crs, max_width, max_height)


def _positive_int(
    parent: ElementTree.Element | None,
    name: str,
    namespace: dict[str, str],
) -> int:
    text = None if parent is None else parent.findtext(f"wms:{name}", namespaces=namespace)
    try:
        value = int(text or "")
    except ValueError as exc:
        raise ProviderContractChanged(f"WMS {name} is missing or invalid") from exc
    if value <= 0:
        raise ProviderContractChanged(f"WMS {name} must be positive")
    return value


def _find_layer_crs(
    layer: ElementTree.Element,
    expected_name: str,
    inherited_crs: tuple[str, ...],
    namespace: dict[str, str],
) -> tuple[str, ...] | None:
    direct_crs = tuple(
        value
        for node in layer.findall("wms:CRS", namespace)
        if (value := (node.text or "").strip())
    )
    effective_crs = tuple(dict.fromkeys((*inherited_crs, *direct_crs)))
    if layer.findtext("wms:Name", namespaces=namespace) == expected_name:
        return effective_crs
    for child in layer.findall("wms:Layer", namespace):
        result = _find_layer_crs(child, expected_name, effective_crs, namespace)
        if result is not None:
            return result
    return None
