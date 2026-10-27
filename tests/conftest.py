from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest
from pydantic import BaseModel

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class FakeProvider:
    """Answers by schema type, so pipeline logic can be tested without a running model."""

    name = "fake"

    def __init__(self, answers: dict[type, Callable[[str], BaseModel]]):
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system, prompt, schema):
        self.calls.append((schema.__name__, prompt))
        return self.answers[schema](prompt)


@pytest.fixture
def sample_dir() -> Path:
    return SAMPLES / "client_feedback"
