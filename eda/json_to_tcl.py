"""
TCL placement exporter.

Output format
=============
Regular transistor
    M1 0.490 0.668 R0 l=0.014 nf=1 nfin=4.0 m=1 left_abut=1 right_abut=1

Dummy transistor
    DUMMY D1 nfet 0.42 0.000 R0 l=0.014 nf=1 nfin=4.0 m=1 left_abut=1 right_abut=0 D=gnd! G=vdd! S=gnd! B=gnd!

Tap cell
    TAP GND Ptap -2.07 2.115 R0
    TAP VDD Ntap  1.23 2.115 R0
"""

import os


# Param keys to always exclude from the output (internal metadata)
_SKIP_KEYS = frozenset({
    "parent", "multiplier_index", "finger_index", "array_index",
    # net names are formatted separately
    "net_d", "net_g", "net_s", "net_b",
})

# Preferred output order for PCell parameters
_PARAM_ORDER = ["l", "nf", "nfin", "m", "w", "left_abut", "right_abut"]


def _fmt_param_val(k, v):
    """Format a single parameter value for the output line."""
    if k == "l" and isinstance(v, (float, int)):
        if v < 1e-4:          # in metres → convert to µm
            v = v * 1e6
        v = round(v, 3)
    if isinstance(v, float) and v == int(v):
        return str(v)          # e.g. 4.0 stays as "4.0"
    return str(v)


def _net_suffix(net):
    """Append '!' for global nets that don't already have it."""
    if net and not net.endswith("!"):
        return net + "!"
    return net or ""


class LayoutExporter:
    def __init__(self, filename="ai_placement.txt"):
        self.filename = filename
        self.instances = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_instance(self, name, x, y, orient="R0", params=None, *,
                     is_dummy=False, is_tap=False,
                     device_type=None, tap_rail=None, tap_cell=None,
                     net_d=None, net_g=None, net_s=None, net_b=None):
        """
        Add one instance to the export list.

        Args:
            name        Instance name (e.g. 'M1', 'D1', 'TAP_VDD_0').
            x, y        Coordinates in microns.
            orient      Orientation string (R0, MY, …).
            params      dict of PCell parameters.
            is_dummy    True  → emit DUMMY prefix line.
            is_tap      True  → emit TAP prefix line.
            device_type Device family string: 'nfet', 'pfet', 'nmos', 'pmos', …
            tap_rail    'VDD' or 'GND' (used for TAP lines).
            tap_cell    Cell name from taps.oas, e.g. 'Ntap', 'Ptap'.
            net_d/g/s/b Terminal net names (used for DUMMY lines).
        """
        # Strip leading double-M from Virtuoso names (MM1 → M1)
        clean_name = name[1:] if name.upper().startswith("MM") else name
        
        # Convert _fX and _mX suffixes to .fX and .mX (e.g. M1_f1 -> M1.f1)
        import re
        clean_name = re.sub(r'_[fF](\d+)', r'.f\1', clean_name)
        clean_name = re.sub(r'_[mM](\d+)', r'.m\1', clean_name)

        self.instances.append({
            "name":        clean_name,
            "x":           float(x),
            "y":           float(y),
            "orient":      orient or "R0",
            "params":      dict(params or {}),
            "is_dummy":    bool(is_dummy),
            "is_tap":      bool(is_tap),
            "device_type": device_type or "",
            "tap_rail":    tap_rail or "",
            "tap_cell":    tap_cell or "",
            "net_d":       net_d or "",
            "net_g":       net_g or "",
            "net_s":       net_s or "",
            "net_b":       net_b or "",
        })

    def add_multi_finger_device(self, base_name, m_index, total_fingers,
                                start_x, y, cpp=0.070,
                                external_left_abut=0, external_right_abut=0):
        """
        Generate explicitly named unit-cell entries for a multi-finger device.
        """
        clean_name = base_name[1:] if base_name.upper().startswith("MM") else base_name

        for f_index in range(1, total_fingers + 1):
            inst_name = f"{clean_name}_m{m_index}_f{f_index}"
            current_x = start_x + (f_index - 1) * cpp

            params = {"l": 0.014, "nf": 1, "nfin": 4.0, "m": 1}

            if f_index == 1:
                if external_left_abut:
                    params["left_abut"] = 1
            else:
                params["left_abut"] = 1

            if f_index == total_fingers:
                if external_right_abut:
                    params["right_abut"] = 1
            else:
                params["right_abut"] = 1

            self.add_instance(inst_name, current_x, y, "R0", params)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_for_tcl(self):
        """Write the placement text file and return True on success."""
        try:
            with open(self.filename, "w") as f:
                exported_names = set()
                count = 0

                for inst in self.instances:
                    if inst["name"] in exported_names:
                        continue

                    line = self._format_line(inst)
                    if line:
                        f.write(line + "\n")
                        exported_names.add(inst["name"])
                        count += 1

            print(f"Successfully exported {count} unique instances to {self.filename}")
            return True
        except Exception as e:
            print(f"Error exporting placement file: {e}")
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _format_params(self, params):
        """Return an ordered list of 'key=value' strings."""
        parts = []
        used = set()

        # Preferred order first
        for k in _PARAM_ORDER:
            if k in params and k not in _SKIP_KEYS:
                v = params[k]
                if v is None:
                    continue
                if k == "w" and v == 0:
                    continue
                parts.append(f"{k}={_fmt_param_val(k, v)}")
                used.add(k)

        # Remaining keys in insertion order
        for k, v in params.items():
            if k in used or k in _SKIP_KEYS:
                continue
            if v is None:
                continue
            if k == "w" and v == 0:
                continue
            parts.append(f"{k}={_fmt_param_val(k, v)}")

        return parts

    def _format_line(self, inst):
        """Produce one output line for the given instance dict."""
        x_str = f"{inst['x']:.3f}"
        y_str = f"{inst['y']:.3f}"
        orient = inst["orient"]
        name = inst["name"]

        # ── TAP ──────────────────────────────────────────────────────
        if inst["is_tap"]:
            rail     = inst["tap_rail"] or ("VDD" if "ntap" in inst["device_type"].lower() else "GND")
            tap_cell = inst["tap_cell"] or ("Ntap" if rail == "VDD" else "Ptap")
            return f"TAP {rail} {tap_cell} {x_str} {y_str} {orient}"

        param_parts = self._format_params(inst["params"])
        param_str = " ".join(param_parts)

        # ── DUMMY ─────────────────────────────────────────────────────
        if inst["is_dummy"]:
            dev_type = inst["device_type"] or "nfet"
            # Normalise to nfet/pfet naming
            dt_lower = dev_type.lower()
            if "p" in dt_lower:
                dev_type_out = "pfet"
            else:
                dev_type_out = "nfet"

            net_d = _net_suffix(inst["net_d"])
            net_g = _net_suffix(inst["net_g"])
            net_s = _net_suffix(inst["net_s"])
            net_b = _net_suffix(inst["net_b"])

            parts = ["DUMMY", name, dev_type_out, x_str, y_str, orient]
            if param_str:
                parts.append(param_str)
            # Terminal nets
            if net_d:
                parts.append(f"D={net_d}")
            if net_g:
                parts.append(f"G={net_g}")
            if net_s:
                parts.append(f"S={net_s}")
            if net_b:
                parts.append(f"B={net_b}")
            return " ".join(parts)

        # ── Regular transistor ────────────────────────────────────────
        parts = [name, x_str, y_str, orient]
        if param_str:
            parts.append(param_str)
        return " ".join(parts)


# ---------------------------------------------------------------------------
# CLI usage example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    exporter = LayoutExporter("ai_placement.txt")
    exporter.add_instance("M1_f1", 0.490, 0.668, "R0",
                          params={"l": 0.014, "nf": 1, "nfin": 4.0, "m": 1,
                                  "left_abut": 1, "right_abut": 1})
    exporter.add_instance("MM2_m2_f3", 0.700, 0.668, "R0",
                          params={"l": 0.014, "nf": 1, "nfin": 4.0, "m": 1,
                                  "left_abut": 1, "right_abut": 1})
    exporter.add_instance("D1_f2", 0.42, 0.000, "R0",
                          params={"l": 0.014, "nf": 1, "nfin": 4.0, "m": 1,
                                  "left_abut": 1, "right_abut": 0},
                          is_dummy=True, device_type="nfet",
                          net_d="gnd!", net_g="vdd!", net_s="gnd!", net_b="gnd!")
    exporter.add_instance("TAP_GND_0", -2.07, 2.115, "R0",
                          is_tap=True, tap_rail="GND", tap_cell="Ptap")
    exporter.export_for_tcl()
