"""Shared TRIAD core: configuration, database, storage, audit, policy, errors.

Imported by backend (API), agents (orchestrator) and python_ml (ML worker) so the three
never duplicate state handling. See docs/adr/ADR-0004-shared-core-package.md.
"""

__version__ = "0.1.0"
