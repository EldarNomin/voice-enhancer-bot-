from uuid import uuid4

import pytest

from voice_enhancer.domain.job import JobStatus, ProcessingJob
from voice_enhancer.domain.profile import Preset, profile_for


def test_all_presets_have_bounded_profiles() -> None:
    for preset in Preset:
        profile = profile_for(preset)
        assert profile.preset is preset
        assert 0 <= profile.noise_reduction <= 1


def test_job_rejects_invalid_transition() -> None:
    job = ProcessingJob(user_id=uuid4(), source_asset_id=uuid4(), preset="reels")
    with pytest.raises(ValueError, match="Invalid job transition"):
        job.transition(JobStatus.COMPLETED)


def test_job_accepts_happy_path_transitions() -> None:
    job = ProcessingJob(user_id=uuid4(), source_asset_id=uuid4(), preset="studio")
    for status in (
        JobStatus.VALIDATING,
        JobStatus.QUEUED,
        JobStatus.PROCESSING,
        JobStatus.COMPLETED,
    ):
        job.transition(status)
    assert job.status is JobStatus.COMPLETED
