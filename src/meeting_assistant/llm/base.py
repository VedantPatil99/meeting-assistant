"""The provider interface every pipeline step talks to.

Swapping the local model for a hosted one later means adding one class here,
not touching the pipeline.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str

    def complete_json(self, system: str, prompt: str, schema: type[T]) -> T:
        """Return the model's answer parsed and validated against `schema`."""
        ...
