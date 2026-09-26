"""The shape of a health report: findings, weights, and what could not be read.

Every program here grows a ``doctor`` command that reads the device over
once and says what looks wrong.  What it *checks* is entirely the program's
own -- cell voltages mean nothing to a wallbox -- but the shape of the answer
is not, and both the terminal renderer and the browser one are written
against that shape rather than against the checks.

A finding is one of three weights.  ``error`` is the device telling us
something is wrong, or two of its own readings disagreeing; ``warning`` is a
reading outside the band the program believes is healthy; ``note`` is a
setting somebody chose that is worth being reminded of -- a disabled switch
is not a fault, and a doctor that called it one would be ignored.

:attr:`Report.unavailable` is deliberately not a finding.  A register the
device does not carry produced no answer, which is a different sentence from
"healthy", and a report that blurred the two would be worth less than one
that admitted the gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ERROR = "error"
WARNING = "warning"
NOTE = "note"

# Worst first, so a report reads top-down.
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, NOTE: 2}

# Where an unrecognised weight sorts: after all the real ones, rather than
# raising in the middle of rendering a report.
UNKNOWN_SEVERITY_RANK = 9


@dataclass(frozen=True)
class Finding:
    """One thing worth telling somebody about this device."""

    severity: str
    area: str
    detail: str
    fix: str | None = None
    """The command that would deal with it, when there is one."""


@dataclass
class Report:
    """Everything one pass found, and everything it could not look at.

    Programs subclass this to carry the readings the checks were made from;
    every field here has a default, so a subclass may add its own.
    """

    findings: list[Finding] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    """Areas whose check could not run, with why."""

    @property
    def worst(self) -> str | None:
        """The highest severity present, or None on a clean report."""
        if not self.findings:
            return None
        return min((f.severity for f in self.findings), key=_rank)

    @property
    def ok(self) -> bool:
        """Whether nothing of ``error`` weight was found."""
        return not any(f.severity == ERROR for f in self.findings)

    def sorted(self) -> list[Finding]:
        """Return the findings worst first, then by area.

        Ordered by weight and area rather than by the order the checks ran,
        so the same device produces the same report however the checks are
        arranged.
        """
        return sorted(self.findings, key=lambda f: (_rank(f.severity), f.area))


def _rank(severity: str) -> int:
    """Where one weight sorts among the others."""
    return SEVERITY_ORDER.get(severity, UNKNOWN_SEVERITY_RANK)


def finding_json(finding: Finding) -> dict[str, str | None]:
    """One finding, in the shape the shared ``HealthCard`` reads.

    The card wants exactly these four keys and does the rest itself -- it
    counts the weights and picks the worst client-side -- so this is the
    finding and nothing more.  Both programs' doctor endpoints serialise
    through it, so the one component is handed one shape rather than two
    that have drifted apart.
    """
    return {
        "severity": finding.severity,
        "area": finding.area,
        "detail": finding.detail,
        "fix": finding.fix,
    }


__all__ = [
    "ERROR",
    "NOTE",
    "SEVERITY_ORDER",
    "WARNING",
    "Finding",
    "Report",
    "finding_json",
]
