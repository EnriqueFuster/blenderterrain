"""Portable background-job protocol and execution."""

from .models import DiscoveryJob, JobState, ProgressEvent
from .worker import acquire_confirmed_sources, run_discovery_job

__all__ = [
    "DiscoveryJob",
    "JobState",
    "ProgressEvent",
    "acquire_confirmed_sources",
    "run_discovery_job",
]
