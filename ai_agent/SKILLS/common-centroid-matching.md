---
id: common_centroid
name: Common-Centroid Matching
description: Apply centroid-balanced ordering for matched devices to reduce linear process-gradient mismatch while preserving exact finger counts and device conservation.
keywords:
	- common centroid
	- common-centroid
	- centroid
	- cc
	- gradient cancellation
---

# Common-Centroid Matching

Description: Apply centroid-balanced ordering for matched devices to reduce linear process-gradient mismatch while preserving exact finger counts and device conservation.

When to apply:
- User intent includes common centroid, centroid, or strict matching.
- Strategy text requests centroid balancing or gradient cancellation.

Core guidance:
- Build one centroid-safe sequence for all CC devices in the same row group.
- Keep all original device IDs and all fingers exactly once.
- Preserve row assignment constraints and deterministic slot ordering.
- Reject layouts that break centroid symmetry or conservation.
