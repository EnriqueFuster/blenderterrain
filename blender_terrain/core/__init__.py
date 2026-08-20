"""Portable geographic domain logic."""

from .crs import CRSInfo, UTMWorkArea, split_bbox_by_utm_zone
from .estimates import ROIEstimate, estimate_bbox
from .roi import BBoxWGS84

__all__ = [
    "BBoxWGS84",
    "CRSInfo",
    "ROIEstimate",
    "UTMWorkArea",
    "estimate_bbox",
    "split_bbox_by_utm_zone",
]
