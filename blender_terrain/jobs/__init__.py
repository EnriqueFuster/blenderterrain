"""Portable background-job protocol and execution."""

from .models import DiscoveryJob, JobState, ProgressEvent
from .worker import (
    PreparedElevation,
    acquire_confirmed_sources,
    prepare_confirmed_elevation,
    run_discovery_job,
)

__all__ = [
    "DiscoveryJob",
    "JobState",
    "PreparedElevation",
    "ProgressEvent",
    "acquire_confirmed_sources",
    "prepare_confirmed_elevation",
    "run_discovery_job",
]
