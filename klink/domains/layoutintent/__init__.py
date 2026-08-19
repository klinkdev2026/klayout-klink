"""layoutintent — Executable Layout Intent (Region -> deterministic executor).

Design: docs/REGION_INTENT_DESIGN.md. This package is the FUNCTIONAL layer:
pure planning (planner.py), sidecar persistence (store.py), and the
prepare/apply/regenerate orchestration (orchestrator.py). It holds ZERO
process facts — every layer, pitch, size, and numbering scheme is a
required input from the caller's project/example.
"""
