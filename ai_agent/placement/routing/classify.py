"""
Net Criticality Classifier
===========================
Classifies net names into one of four categories using word-boundary regex.

Replaces the old single-letter `endswith` heuristics that caused false
positives (e.g. 'data' → critical because of 'a', 'gnda' → power).

Usage:
    from ai_agent.placement.routing.classify import NetClassifier, Criticality
    clf = NetClassifier()
    clf.classify("VOUTN")   # → 'critical'
    clf.classify("NBIAS")   # → 'bias'
    clf.classify("VDD")     # → 'power'
    clf.classify("net_23")  # → 'signal'
"""

import re
from dataclasses import dataclass, field
from typing import Literal

Criticality = Literal["power", "critical", "bias", "signal"]

# ---------------------------------------------------------------------------
# Default regex patterns — anchored, case-insensitive, word-boundary safe
# ---------------------------------------------------------------------------

_DEFAULT_POWER_RE = re.compile(
    r"^(VDD|VSS|GND|VCC|VEE|AVDD|AVSS|VDDA|VDDH|VDDL|VDDQ|"
    r"VBN|VBP|VPP|VPWR|VGND|GND1|VSS_ANA|VSSA|VDDE)"
    r"([0-9_A-Z].*)?$",
    re.IGNORECASE,
)

_DEFAULT_BIAS_RE = re.compile(
    r"^(NBIAS|PBIAS|VBIAS|IBIAS|VTAIL|V_VTAIL|NTAIL|PTAIL|"
    r"VCM|VCMFB|CMFB|VCAS|NCAS|PCAS)"
    r"([0-9_].*)?$",
    re.IGNORECASE,
)

# Critical = explicit signal-name patterns, NO single-letter fallback
_DEFAULT_CRITICAL_RE = re.compile(
    r"^(VOUT|VOUTN|VOUTP|OUT|OUTP|OUTN|VOP|VON|"
    r"VINP|VINN|VIN|INP|INN|VIP|VIM|IN[0-9]?|"
    r"CK|CLK|CLKP|CLKN|CLKB|"
    r"DATA|DIN|DOUT|QB?[0-9_]?|"
    r"6ND|6NP)"                   # circuit-specific internal differential nodes
    r"([0-9_].*)?$",
    re.IGNORECASE,
)


@dataclass
class NetClassifier:
    """
    Configurable net criticality classifier.

    Override any regex to customise classification for non-standard naming
    conventions without touching the default patterns.
    """
    power_re:    re.Pattern = field(default_factory=lambda: _DEFAULT_POWER_RE)
    bias_re:     re.Pattern = field(default_factory=lambda: _DEFAULT_BIAS_RE)
    critical_re: re.Pattern = field(default_factory=lambda: _DEFAULT_CRITICAL_RE)

    def classify(self, net_name: str) -> Criticality:
        """Return the criticality of a net given its name."""
        if self.power_re.match(net_name):
            return "power"
        if self.critical_re.match(net_name):
            return "critical"
        if self.bias_re.match(net_name):
            return "bias"
        return "signal"


# Module-level singleton for convenience
_DEFAULT_CLASSIFIER = NetClassifier()


def classify_net(net_name: str) -> Criticality:
    """Classify a single net name using the default classifier."""
    return _DEFAULT_CLASSIFIER.classify(net_name)
