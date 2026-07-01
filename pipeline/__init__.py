"""Campgrounds ingestion pipeline (stdlib-only, no third-party dependencies).

Stages: fetch -> normalize -> merge -> validate -> compact -> publish.
Each stage is an independently runnable module with a CLI subcommand
(see pipeline/cli.py). Sources are declared in the top-level registry.json.
"""

__all__ = ["common", "registry"]
