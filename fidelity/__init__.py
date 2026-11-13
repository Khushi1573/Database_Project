"""Fidelity Auditor and statistical analysis for COMP.8157 Team 2.

Modules (interfaces follow D.4.2 section 6.4: audit, sample, aggregate):
    auditor     - Structural Fidelity (Fs) from information_schema.statistics
                  cross-checked against SHOW INDEX  (FR-4, FR-5)
    ahi         - Effective Hash Coverage (Uh) from InnoDB AHI counters (FR-6)
    crossval    - Cell A vs Cell C structural cross-validation (FR-7)
    adaptation  - AHI adaptation-time detector (FR-14)
    aggregate   - medians with bootstrap 95% CIs and summary CSV (FR-15, FR-16)
    plots       - summary figures (FR-17)
"""

__all__ = ["auditor", "ahi", "crossval", "adaptation", "aggregate", "plots"]
