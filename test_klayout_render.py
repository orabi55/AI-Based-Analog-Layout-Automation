"""Quick test: render Xor_Automation.oas to a PNG using KLayout API."""
import klayout.db as db
import klayout.lay as lay

view = lay.LayoutView()
view.load_layout("Xor_Automation.oas")
view.max_hier()
view.zoom_fit()
view.save_image("klayout_test_render.png", 800, 600)
print("Rendered to klayout_test_render.png")
