"""Ferramenta de espelhamento de dados onpremise (MySQL) -> Aurora MySQL (AWS).

Fluxo unidirecional, onprem e fonte de verdade absoluta. Ver README.md.
"""

__all__ = [
    "config",
    "schema",
    "onprem",
    "aurora",
    "planner",
    "applier",
    "backup",
    "verify",
]
