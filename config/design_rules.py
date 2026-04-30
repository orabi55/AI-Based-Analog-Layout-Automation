"""
Design Rules — SAED14nm PDK constants.

All magic numbers related to the process design rules are centralized
here so that PDK changes only require editing this single file.

Usage:
    from config.design_rules import PITCH_UM, ROW_PITCH, FINGER_PITCH
"""

# ---------------------------------------------------------------------------
# Device geometry (SAED 14nm)
# ---------------------------------------------------------------------------
PITCH_UM       = 0.294    # µm — standard non-abutted device pitch (diffusion break)
ROW_PITCH      = 0.668    # µm — standard row-to-row pitch
ROW_HEIGHT_UM  = 0.668    # µm — height of one NMOS/PMOS row
ROW_GAP_UM     = 0.000    # µm — no extra vertical gap between adjacent active rows
FINGER_PITCH   = 0.070    # µm — abutted finger-to-finger pitch
PMOS_Y         = 0.668    # µm — default initial PMOS row Y
NMOS_Y         = 0.000    # µm — default initial NMOS row Y

# ---------------------------------------------------------------------------
# Rendering scale (editor canvas)
# ---------------------------------------------------------------------------
PIXELS_PER_UM  = 34.0     # conversion factor for editor display

# ---------------------------------------------------------------------------
# Derived constants
# ---------------------------------------------------------------------------
BLOCK_GAP_UM        = PITCH_UM * 2    # gap between hierarchy blocks
PASSIVE_ROW_GAP_UM  = PITCH_UM        # gap for passive component rows

# ---------------------------------------------------------------------------
# Passive component defaults (Tunable)
# ---------------------------------------------------------------------------
PASSIVE_WIDTH_UM        = 1.5     # Default width for R/C items (Resistor baseline)
PASSIVE_HEIGHT_UM       = 0.6     # Default height for R/C items (Resistor baseline)
PASSIVE_CAP_WIDTH_UM    = 3.0     # Default width for Capacitor items
PASSIVE_CAP_HEIGHT_UM   = 1.0     # Default height for Capacitor items
PASSIVE_CAP_DEFAULT     = 1.0e-12 # 1pF
PASSIVE_RES_DEFAULT     = 1000.0  # 1kΩ
