---
id: mirror_biasing
name: Mirror Biasing Sequencing
description: Construct mirror-safe symmetric placement for bias/current-mirror groups, preserving ratios and left-right symmetry while keeping all devices/fingers present.
keywords:
	- mirror bias
	- mirror biasing
	- current mirror
	- matched pair
	- bias pair
	- mirror
	- bias
	- mb
---

# Mirror Biasing Sequencing

Description: Construct mirror-safe symmetric placement for bias/current-mirror groups, preserving ratios and left-right symmetry while keeping all devices/fingers present.

When to apply:
- User intent includes mirror biasing, current mirror, bias pair, or matched mirror devices.
- Strategy text requires symmetric mirror-friendly sequencing.

Core guidance:
- Build half-sequence targets, then mirror deterministically.
- Preserve exact device and finger counts in final sequence.
- Keep symmetry explicit in slot assignment and center handling.
- Place dummy devices only at row boundaries when required.
