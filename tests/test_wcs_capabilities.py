from __future__ import annotations

from pathlib import Path

import pytest

from blender_terrain.errors import ProviderContractChanged
from blender_terrain.io.wcs_capabilities import parse_wcs_capabilities

FIXTURE = Path(__file__).parent / "fixtures" / "wcs" / "environment_agency_dtm_capabilities.xml"
COVERAGE_ID = "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
FORMAT = "image/tiff;application=geotiff"


def test_parses_environment_agency_wcs_contract() -> None:
    capabilities = parse_wcs_capabilities(FIXTURE.read_bytes(), COVERAGE_ID, FORMAT)

    assert capabilities.version == "2.0.1"
    assert capabilities.coverage_ids == (COVERAGE_ID,)
    assert FORMAT in capabilities.formats


@pytest.mark.parametrize(
    ("coverage_id", "output_format"),
    [("missing", FORMAT), (COVERAGE_ID, "application/x-missing")],
)
def test_rejects_missing_wcs_contract_values(coverage_id: str, output_format: str) -> None:
    with pytest.raises(ProviderContractChanged):
        parse_wcs_capabilities(FIXTURE.read_bytes(), coverage_id, output_format)
