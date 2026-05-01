"""
placement.routing
=================
Deterministic routing analysis sub-package for the analog layout pipeline.

Modules:
  classify  — regex-based net criticality classifier
  nets      — net→logical-device map builder with finger aggregation
  hpwl      — Manhattan HPWL (|Δx|+|Δy|) per net and total
  crossings — sweep-line H×V crossing estimator
  density   — per-channel track-count estimator
  cost      — weighted cost function
  report    — RoutingReport / NetReport / ChannelReport dataclasses
"""
