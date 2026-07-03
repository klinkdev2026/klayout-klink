"""Photonics domain helpers.

This package owns photonics-specific PDK port conventions, circuit/net-intent
helpers, and planned waveguide DRC rules.  Routing backends remain in
``klink.routing``.
"""

from .blackbox import harvest_instance_ports, mark_ports, stub_template

__all__ = ["harvest_instance_ports", "mark_ports", "stub_template"]
