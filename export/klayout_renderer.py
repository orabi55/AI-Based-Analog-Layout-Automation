"""
KLayout Renderer — headless layout rendering using KLayout's Python API.

Renders OAS/GDS layout files to PNG images or QPixmap objects
for embedding in the symbolic editor preview panel.
"""

import os
import sys
import tempfile

import klayout.db as kdb
import klayout.lay as klay


def render_oas_to_file(oas_path, output_png, width=800, height=600):
    """Render an OAS/GDS layout to a PNG image file.

    Args:
        oas_path:   Path to the .oas or .gds layout file.
        output_png: Path for the output PNG image.
        width:      Image width in pixels.
        height:     Image height in pixels.

    Returns:
        output_png path on success.
    """
    if not os.path.isfile(oas_path):
        raise FileNotFoundError(f"Layout file not found: {oas_path}")

    view = klay.LayoutView()
    view.load_layout(oas_path)
    view.max_hier()
    view.zoom_fit()
    view.save_image(output_png, width, height)
    return output_png


def render_oas_to_pixmap(oas_path, width=800, height=600):
    """Render an OAS/GDS layout and return a QPixmap.

    Uses a temporary PNG file internally, then loads it as QPixmap.

    Args:
        oas_path: Path to the .oas or .gds layout file.
        width:    Image width in pixels.
        height:   Image height in pixels.

    Returns:
        A PySide6 QPixmap object, or None on failure.
    """
    from PySide6.QtGui import QPixmap

    if not os.path.isfile(oas_path):
        return None

    # Use a temp file for the intermediate PNG
    fd, tmp_png = tempfile.mkstemp(suffix=".png", prefix="klayout_preview_")
    os.close(fd)

    try:
        render_oas_to_file(oas_path, tmp_png, width, height)
        pixmap = QPixmap(tmp_png)
        return pixmap
    except Exception as e:
        print(f"[KLayout Renderer] Error: {e}")
        return None
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_png)
        except OSError:
            pass
