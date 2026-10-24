"""Energy Cost Predictor.

A Python tool that estimates residential heating energy costs for a house based
on its region (Massachusetts as the primary target) and compares the annual cost
of a conventional heating system against a heat-pump system.

The package is organized around a clean separation between a pure, substitutable
-data core algorithm and pluggable data providers. See the design document under
``.kiro/specs/energy-cost-predictor/design.md`` for details.
"""

__version__ = "0.1.0"
