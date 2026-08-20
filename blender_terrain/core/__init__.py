"""Portable geographic domain logic."""

from .crs import CRSInfo, UTMWorkArea, split_bbox_by_utm_zone
from .roi import BBoxWGS84

__all__ = ["BBoxWGS84", "CRSInfo", "UTMWorkArea", "split_bbox_by_utm_zone"]
