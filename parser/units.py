def parse_value(value: str) -> float:
    """
    Convert SPICE values like:
    1u, 60n, 2p, 10k, 1meg -> float
    """

    value = value.strip().lower()

    scale = {
        'f': 1e-15,
        'p': 1e-12,
        'n': 1e-9,
        'u': 1e-6,
        'm': 1e-3,
        'k': 1e3,
        'meg': 1e6,
        'g': 1e9
    }

    # try long suffix first (meg)
    for suffix in sorted(scale.keys(), key=len, reverse=True):
        if value.endswith(suffix):
            number = value[:-len(suffix)]
            return float(number) * scale[suffix]

    # pure number
    return float(value)
