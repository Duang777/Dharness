"""Evidence-gated Harbor agent for Terminal-Bench 2.0."""

from typing import TYPE_CHECKING

__all__ = ["EvidenceHarnessAgent"]
__version__ = "0.1.0"

if TYPE_CHECKING:
    from evidence_harness.harbor_agent import EvidenceHarnessAgent


def __getattr__(name: str) -> object:
    if name == "EvidenceHarnessAgent":
        from evidence_harness.harbor_agent import EvidenceHarnessAgent

        return EvidenceHarnessAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
