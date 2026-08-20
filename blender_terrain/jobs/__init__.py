"""Portable background-job protocol and execution."""

from .models import DiscoveryJob, JobState, ProgressEvent
from .worker import run_discovery_job

__all__ = ["DiscoveryJob", "JobState", "ProgressEvent", "run_discovery_job"]
