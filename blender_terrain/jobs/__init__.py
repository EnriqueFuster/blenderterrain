"""Portable background-job protocol and execution."""

from .acquisition_job import AcquisitionJob
from .legacy_discovery import run_discovery_job
from .models import DiscoveryJob, JobState, ProgressEvent
from .terrain import (
    PreparedElevation,
    acquire_confirmed_sources,
    prepare_confirmed_elevation,
)
from .worker import run_confirmed_acquisition_job

__all__ = [
    "AcquisitionJob",
    "DiscoveryJob",
    "JobState",
    "PreparedElevation",
    "ProgressEvent",
    "acquire_confirmed_sources",
    "prepare_confirmed_elevation",
    "run_confirmed_acquisition_job",
    "run_discovery_job",
]
