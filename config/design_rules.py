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
# Metal layer pitches (SAED 14nm — from PDK table)
# width + spacing = pitch
# ---------------------------------------------------------------------------
METAL_M1_PITCH_UM  = 0.130   # M1: width 0.100 + spacing 0.030
METAL_PITCH_UM     = 0.134   # M2–M8: width 0.100 + spacing 0.034 (primary routing)
METAL_M9_PITCH_UM  = 0.314   # M9: width 0.280 + spacing 0.034
METAL_MRDL_PITCH_UM = 0.384  # MRDL: width 0.350 + spacing 0.034

# ---------------------------------------------------------------------------
# Routing channel planner sizing
# ---------------------------------------------------------------------------
CHANNEL_MARGIN_TRACKS = 1     # extra slack tracks per channel (safety margin)
MAX_CHANNEL_WIDTH_UM  = 5.0   # cap — prevent runaway expansion for dense nets
