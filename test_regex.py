"""Quick regex test for _parse_mirror_groups - no ai_agent imports."""
import re

def test_regex():
    mirror_line_re = re.compile(
        r'MIRROR\s*\(\s*(\w+)\s*,\s*gate=(\w+)\s*\)\s*:\s*(.+)',
        re.IGNORECASE
    )
    dev_id_re = re.compile(r'(\w+)(?:\[REF\])?(?:\(nf=\d+\))?')

    test_lines = [
        "MIRROR (NMOS, gate=C): MM2(nf=4) <-> MM1(nf=4) <-> MM0[REF](nf=8)",
        "  MIRROR (NMOS, gate=C): MM2(nf=4) <-> MM1(nf=4) <-> MM0[REF](nf=8)",
        "1. NMOS Mirror: MM0[REF] (nf=8) <-> MM1 (nf=4) <-> MM2 (nf=4) gate net: C",
    ]

    for line in test_lines:
        m = mirror_line_re.search(line)
        if m:
            devs_str = m.group(3)
            dev_ids = []
            for part in devs_str.split("<->"):
                part = part.strip()
                dm = dev_id_re.match(part)
                if dm:
                    dev_ids.append(dm.group(1))
            print(f"MATCH: type={m.group(1)} gate={m.group(2)} devs={dev_ids}")
        else:
            print(f"NO MATCH: {line}")

    # Test full constraint blob
    constraint_text = """=== CURRENT MIRRORS ===
MIRROR (NMOS, gate=C): MM2(nf=4) <-> MM1(nf=4) <-> MM0[REF](nf=8)
  MM0: D=C G=C S=gnd nf=8 nfin=2 l=0.014u
  ** LAYOUT: Use COMMON-CENTROID interdigitation
"""
    print("\nFull blob test:")
    for line in constraint_text.splitlines():
        m = mirror_line_re.search(line.strip())
        if m:
            devs_str = m.group(3)
            dev_ids = []
            for part in devs_str.split("<->"):
                dm = dev_id_re.match(part.strip())
                if dm:
                    dev_ids.append(dm.group(1))
            print(f"  FOUND: devs={dev_ids}")

if __name__ == "__main__":
    test_regex()
