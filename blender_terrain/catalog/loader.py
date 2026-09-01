"""Strict TOML loader for the bundled geographic product catalog."""

from __future__ import annotations

import tomllib
from collections.abc import Collection, Iterable, Mapping
from importlib.resources import files
from typing import Any

from ..core.roi import BBoxWGS84
from .models import (
    AcquisitionMode,
    Catalog,
    Coverage,
    DatasetKind,
    ImplementationStatus,
    LicensePolicy,
    ProductCapabilities,
    ProductRecord,
    SemanticConfidence,
    WMSContract,
)


def load_bundled_catalog() -> Catalog:
    """Load every catalog document shipped with the Python package."""

    directory = files(f"{__package__}.data")
    documents = (
        (entry.name, entry.read_bytes())
        for entry in sorted(directory.iterdir(), key=lambda item: item.name)
        if entry.name.endswith(".toml")
    )
    return load_catalog_documents(documents)


def load_catalog_documents(documents: Iterable[tuple[str, bytes]]) -> Catalog:
    products: list[ProductRecord] = []
    for source, payload in documents:
        try:
            document = tomllib.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"Invalid catalog document {source}: {exc}") from exc
        _exact_keys(document, {"products"}, source)
        raw_products = document.get("products")
        if not isinstance(raw_products, list):
            raise ValueError(f"Catalog document {source} must contain a products array")
        products.extend(_product(value, source) for value in raw_products)
    return Catalog(tuple(products))


def _product(value: object, source: str) -> ProductRecord:
    record = _table(value, f"product in {source}")
    _exact_keys(
        record,
        {
            "id",
            "provider_id",
            "name",
            "jurisdiction",
            "implementation_status",
            "version",
            "last_verified",
            "endpoint",
            "evidence_urls",
            "reliability_score",
            "network_cost_score",
            "capabilities",
            "coverage",
            "license",
        },
        f"product in {source}",
        {"wms"},
    )
    capabilities = _table(record["capabilities"], f"capabilities in {source}")
    coverage = _table(record["coverage"], f"coverage in {source}")
    license_policy = _table(record["license"], f"license in {source}")
    wms = _optional_table(record, "wms", source)
    _exact_keys(
        capabilities,
        {
            "kind",
            "native_resolution_m",
            "semantics",
            "authoritative",
            "acquisition_mode",
            "supports_roi_window",
            "supports_http_range",
            "requires_auth",
            "uncertainty_available",
            "temporal",
        },
        f"capabilities in {source}",
    )
    if wms is not None:
        _exact_keys(
            wms,
            {
                "version",
                "layer",
                "style",
                "format",
                "crs_epsg",
                "maximum_dimension",
            },
            f"wms in {source}",
            {"sample_dtype", "nodata"},
        )
    _exact_keys(coverage, {"bounds", "requires_discovery", "limitations"}, f"coverage in {source}")
    _exact_keys(
        license_policy,
        {
            "identifier",
            "commercial_use",
            "derivatives",
            "redistribution",
            "share_alike",
            "attribution_required",
            "attribution_text",
        },
        f"license in {source}",
    )
    return ProductRecord(
        id=_string(record, "id"),
        provider_id=_string(record, "provider_id"),
        name=_string(record, "name"),
        jurisdiction=_string(record, "jurisdiction"),
        implementation_status=ImplementationStatus(_string(record, "implementation_status")),
        version=_optional_string(record, "version"),
        last_verified=_string(record, "last_verified"),
        endpoint=_string(record, "endpoint"),
        evidence_urls=_strings(record, "evidence_urls"),
        reliability_score=_integer(record, "reliability_score"),
        network_cost_score=_integer(record, "network_cost_score"),
        capabilities=ProductCapabilities(
            kind=DatasetKind(_string(capabilities, "kind")),
            native_resolution_m=_number(capabilities, "native_resolution_m"),
            semantics=SemanticConfidence(_string(capabilities, "semantics")),
            authoritative=_boolean(capabilities, "authoritative"),
            acquisition_mode=AcquisitionMode(_string(capabilities, "acquisition_mode")),
            supports_roi_window=_boolean(capabilities, "supports_roi_window"),
            supports_http_range=_boolean(capabilities, "supports_http_range"),
            requires_auth=_boolean(capabilities, "requires_auth"),
            uncertainty_available=_boolean(capabilities, "uncertainty_available"),
            temporal=_boolean(capabilities, "temporal"),
        ),
        coverage=Coverage(
            bounds=tuple(_bounds(item, source) for item in _array(coverage, "bounds")),
            requires_discovery=_boolean(coverage, "requires_discovery"),
            limitations=_strings(coverage, "limitations"),
        ),
        license=LicensePolicy(
            identifier=_string(license_policy, "identifier"),
            commercial_use=_boolean(license_policy, "commercial_use"),
            derivatives=_boolean(license_policy, "derivatives"),
            redistribution=_optional_boolean(license_policy, "redistribution"),
            share_alike=_boolean(license_policy, "share_alike"),
            attribution_required=_boolean(license_policy, "attribution_required"),
            attribution_text=_string(license_policy, "attribution_text"),
        ),
        wms=(
            None
            if wms is None
            else WMSContract(
                version=_string(wms, "version"),
                layer=_string(wms, "layer"),
                style=_string_allow_empty(wms, "style"),
                format=_string(wms, "format"),
                crs_epsg=_integer(wms, "crs_epsg"),
                maximum_dimension=_integer(wms, "maximum_dimension"),
                sample_dtype=_optional_string(wms, "sample_dtype"),
                nodata=_optional_number(wms, "nodata"),
            )
        ),
    )


def _bounds(value: object, source: str) -> BBoxWGS84:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"Coverage bounds in {source} must contain four numbers")
    if not all(isinstance(item, int | float) and not isinstance(item, bool) for item in value):
        raise ValueError(f"Coverage bounds in {source} must contain four numbers")
    return BBoxWGS84(*(float(item) for item in value))


def _table(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a table")
    return value


def _optional_table(
    table: Mapping[str, Any], key: str, source: str
) -> Mapping[str, Any] | None:
    value = table.get(key)
    if value is None:
        return None
    return _table(value, f"{key} in {source}")


def _exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    label: str,
    optional: Collection[str] = (),
) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected - set(optional)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"unknown {', '.join(sorted(unknown))}")
        raise ValueError(f"Invalid {label}: {'; '.join(details)}")


def _string(table: Mapping[str, Any], key: str) -> str:
    value = table[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"Catalog field {key} must be a non-empty string")
    return value


def _string_allow_empty(table: Mapping[str, Any], key: str) -> str:
    value = table[key]
    if not isinstance(value, str):
        raise ValueError(f"Catalog field {key} must be a string")
    return value


def _optional_string(table: Mapping[str, Any], key: str) -> str | None:
    value = table.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"Catalog field {key} must be a string")
    return value


def _boolean(table: Mapping[str, Any], key: str) -> bool:
    value = table[key]
    if not isinstance(value, bool):
        raise ValueError(f"Catalog field {key} must be a boolean")
    return value


def _optional_boolean(table: Mapping[str, Any], key: str) -> bool | None:
    value = table[key]
    if value == "unknown":
        return None
    if not isinstance(value, bool):
        raise ValueError(f"Catalog field {key} must be a boolean or 'unknown'")
    return value


def _integer(table: Mapping[str, Any], key: str) -> int:
    value = table[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Catalog field {key} must be an integer")
    return value


def _number(table: Mapping[str, Any], key: str) -> float:
    value = table[key]
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"Catalog field {key} must be a number")
    return float(value)


def _optional_number(table: Mapping[str, Any], key: str) -> float | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"Catalog field {key} must be a number")
    return float(value)


def _array(table: Mapping[str, Any], key: str) -> list[object]:
    value = table[key]
    if not isinstance(value, list) or not value:
        raise ValueError(f"Catalog field {key} must be a non-empty array")
    return value


def _strings(table: Mapping[str, Any], key: str) -> tuple[str, ...]:
    values = _array(table, key)
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError(f"Catalog field {key} must contain non-empty strings")
    return tuple(str(value) for value in values)
