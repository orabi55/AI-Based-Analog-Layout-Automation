import sys
import os

# Mock QDialog for testing the logic
class MockDialog:
    def _parse_spice_value(self, value: str) -> float:
        value = value.strip().lower()
        scale = {
            'f': 1e-15, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6,
            'm': 1e-3, 'k': 1e3, 'meg': 1e6, 'g': 1e9
        }
        for suffix in sorted(scale.keys(), key=len, reverse=True):
            if value.endswith(suffix):
                number_part = value[:-len(suffix)]
                if not number_part: break
                try:
                    return float(number_part) * scale[suffix]
                except ValueError:
                    continue
        return float(value)

tester = MockDialog()
test_cases = {
    "10k": 10000.0,
    "2.2u": 2.2e-06,
    "470p": 470e-12,
    "1.5meg": 1500000.0,
    "0.1n": 1e-10,
    "1": 1.0
}

for inp, expected in test_cases.items():
    res = tester._parse_spice_value(inp)
    assert abs(res - expected) < 1e-18, f"Failed for {inp}: {res} != {expected}"

print("All parsing tests passed!")
