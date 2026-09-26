"""A long operation's progress, turned into events for the browser.

The terminal draws a bar per phase, but a browser gets one bar per job, and
the work behind it usually comes in parts of very different length: a
download, then a transfer, then whatever the device does on its own once it
has the file.  Each part gets a slice of the one bar, so it keeps moving
instead of sitting at 100% for three minutes.

The slices are guesses about duration and nothing depends on them being
right.  A program with only one phase leaves :class:`Phases` alone and the
transfer fills the bar.
"""

from __future__ import annotations

from dataclasses import dataclass

from devicectl.report import Reporter, Wait
from devicectl.web.worker import Job


@dataclass(frozen=True)
class Phases:
    """Where each part of a long job ends, on the one bar the page draws."""

    sending_ends: float = 1.0
    """Where the transfer finishes.  Below 1.0 leaves room for a wait."""

    waiting_ends: float = 1.0
    """Where the wait for the device finishes, leaving the last sliver for
    whatever confirms the operation actually took."""


ONE_PHASE = Phases()


class JobReporter(Reporter):
    """Report a long operation's progress onto a :class:`Job`.

    ``start`` is where the transfer begins on the bar -- 0 when the file was
    uploaded, further along when it had to be fetched first.  Warnings are
    kept as well as shown, because the job's result is what the browser has
    left to look at once the run is over.
    """

    def __init__(
        self,
        job: Job,
        *,
        start: float = 0.0,
        phases: Phases = ONE_PHASE,
        unit: str = "",
    ) -> None:
        """Report onto ``job``, with the transfer starting at ``start``.

        ``unit`` names what is being counted -- "block", "page" -- and turns
        the transfer into a countable line ("block 12 of 340") beside the
        bar.  Left empty, the bar moves and says nothing, which is right
        where the count is bytes and the bar already says it.
        """
        self.job = job
        self.start = start
        self.phases = phases
        self.unit = unit
        self.warnings: list[str] = []

    def step(self, message: str) -> None:
        """Show the new phase as the job's message."""
        self.job.report(message=message, force=True)

    def detail(self, message: str) -> None:
        """Show a detail as the job's message; the browser has one line."""
        self.job.report(message=message, force=True)

    def warn(self, message: str) -> None:
        """Show a warning and keep it for the job's result."""
        self.warnings.append(message)
        self.job.report(message=f"Warning: {message}", force=True)

    def sending(self, sent: int, total: int, elapsed_s: float, label: str = "") -> None:
        """Move the bar across the transfer's slice."""
        span = self.phases.sending_ends - self.start
        counted = f"{self.unit} {sent} of {total}" if self.unit else None
        self.job.report(self.start + span * (sent / total if total else 1.0), counted)

    def waiting(self, wait: Wait) -> None:
        """Move the bar across the waiting slice, by elapsed time."""
        self.job.report(self._waiting_progress(wait))

    def polled(self, wait: Wait) -> None:
        """Move the bar, and say what the device last answered."""
        self.job.report(self._waiting_progress(wait), wait.label)

    def _waiting_progress(self, wait: Wait) -> float:
        """Map elapsed waiting time onto the waiting slice of the bar."""
        span = self.phases.waiting_ends - self.phases.sending_ends
        done = min(wait.elapsed_s / wait.deadline_s, 1.0) if wait.deadline_s else 1.0
        return self.phases.sending_ends + span * done


__all__ = ["ONE_PHASE", "JobReporter", "Phases"]
