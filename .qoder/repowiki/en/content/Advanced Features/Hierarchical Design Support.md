# Hierarchical Design Support

<cite>
**Referenced Files in This Document**
- [hierarchy.py](file://parser/hierarchy.py)
- [netlist_reader.py](file://parser/netlist_reader.py)
- [circuit_graph.py](file://parser/circuit_graph.py)
- [hierarchy_group_item.py](file://symbolic_editor/hierarchy_group_item.py)
- [block_item.py](file://symbolic_editor/block_item.py)
- [layout_reader.py](file://parser/layout_reader.py)
- [device_matcher.py](file://parser/device_matcher.py)
- [export_json.py](file://export/export_json.py)
- [SYMBOLIC_HIERARCHY.md](file://docs/SYMBOLIC_HIERARCHY.md)
- [Miller_OTA_graph_compressed.json](file://examples/Miller_OTA/Miller_OTA_graph_compressed.json)
- [Current_Mirror_CM.json](file://examples/current_mirror/Current_Mirror_CM.json)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
This document explains the hierarchical design support system that enables complex multi-level analog circuits with block-level management. It covers:
- Hierarchical device organization via array, multiplier (m), and finger (nf) parameters
- Visual hierarchical grouping for symbolic editing and navigation
- Hierarchical netlist parsing and circuit graph construction that preserves parent-child relationships
- Practical examples for operational amplifiers and current mirrors
- Benefits for reusability, modularity, and maintainability
- Performance considerations and best practices for deep hierarchies

## Project Structure
The hierarchical design system spans parsing, visualization, and export layers:
- Parser: netlist parsing, hierarchy reconstruction, circuit graph construction, layout extraction
- Symbolic Editor: visual grouping and navigation of hierarchical devices
- Examples and Docs: representative designs and documentation for symbolic hierarchy

```mermaid
graph TB
subgraph "Parser"
NR["netlist_reader.py"]
H["hierarchy.py"]
CG["circuit_graph.py"]
LR["layout_reader.py"]
DM["device_matcher.py"]
end
subgraph "Symbolic Editor"
HGI["hierarchy_group_item.py"]
BI["block_item.py"]
end
subgraph "Export"
EJ["export_json.py"]
end
NR --> H
NR --> CG
NR --> DM
H --> CG
LR --> DM
CG --> EJ
HGI --> BI
```

**Diagram sources**
- [netlist_reader.py](file://parser/netlist_reader.py)
- [hierarchy.py](file://parser/hierarchy.py)
- [circuit_graph.py](file://parser/circuit_graph.py)
- [layout_reader.py](file://parser/layout_reader.py)
- [device_matcher.py](file://parser/device_matcher.py)
- [hierarchy_group_item.py](file://symbolic_editor/hierarchy_group_item.py)
- [block_item.py](file://symbolic_editor/block_item.py)
- [export_json.py](file://export/export_json.py)

**Section sources**
- [netlist_reader.py](file://parser/netlist_reader.py)
- [hierarchy.py](file://parser/hierarchy.py)
- [circuit_graph.py](file://parser/circuit_graph.py)
- [layout_reader.py](file://parser/layout_reader.py)
- [device_matcher.py](file://parser/device_matcher.py)
- [hierarchy_group_item.py](file://symbolic_editor/hierarchy_group_item.py)
- [block_item.py](file://symbolic_editor/block_item.py)
- [export_json.py](file://export/export_json.py)

## Core Components
- Device hierarchy modeling and expansion: array/multiplier/finger parameters are parsed and expanded into a tree of nodes with parent-child relationships.
- Netlist parsing and flattening: hierarchical SPICE/CDL subcircuits are flattened into leaf device statements with hierarchical prefixes and block membership tracking.
- Circuit graph construction: electrical connectivity is transformed into a NetworkX graph with behavioral edge classification and optional geometry merging.
- Visual hierarchical grouping: symbolic rectangles represent parent devices and can be navigated to reveal children (multipliers or fingers).
- Layout extraction and device matching: hierarchical layout instances are extracted and mapped to netlist devices, collapsing expanded multi-finger devices onto shared layout instances.

**Section sources**
- [hierarchy.py](file://parser/hierarchy.py)
- [netlist_reader.py](file://parser/netlist_reader.py)
- [circuit_graph.py](file://parser/circuit_graph.py)
- [hierarchy_group_item.py](file://symbolic_editor/hierarchy_group_item.py)
- [layout_reader.py](file://parser/layout_reader.py)
- [device_matcher.py](file://parser/device_matcher.py)

## Architecture Overview
The system integrates parsing, visualization, and export to support hierarchical analog design:
- Parsing phase: flatten hierarchical netlists, expand array/multiplier/finger devices, and reconstruct hierarchy from expanded devices
- Graph phase: build electrical connectivity graph and optionally merge with layout geometry
- Visualization phase: present hierarchical groups as symbolic rectangles with navigation controls
- Matching phase: map netlist devices to layout instances, collapsing multi-finger expansions onto shared layout instances
- Export phase: produce JSON consumable by AI placement agents

```mermaid
sequenceDiagram
participant User as "User"
participant Parser as "netlist_reader.py"
participant H as "hierarchy.py"
participant CG as "circuit_graph.py"
participant SE as "hierarchy_group_item.py"
participant LM as "layout_reader.py"
participant DM as "device_matcher.py"
participant EXP as "export_json.py"
User->>Parser : "read_netlist_with_blocks(file)"
Parser->>Parser : "flatten_netlist_with_blocks()"
Parser->>H : "build_device_hierarchy(devices)"
H-->>Parser : "DeviceHierarchy per parent"
Parser->>CG : "build_circuit_graph(netlist)"
CG-->>Parser : "NetworkX graph"
Parser->>LM : "extract_layout_instances(file)"
LM-->>Parser : "layout_devices"
Parser->>DM : "match_devices(netlist, layout_devices)"
DM-->>Parser : "mapping {device : layout_idx}"
Parser->>CG : "build_merged_graph(netlist, layout_devices, mapping)"
CG-->>Parser : "merged graph"
Parser->>EXP : "graph_to_json(merged_graph, output)"
EXP-->>User : "JSON exported"
User->>SE : "Navigate symbolic hierarchy"
SE-->>User : "Descend/ascend groups"
```

**Diagram sources**
- [netlist_reader.py](file://parser/netlist_reader.py)
- [hierarchy.py](file://parser/hierarchy.py)
- [circuit_graph.py](file://parser/circuit_graph.py)
- [layout_reader.py](file://parser/layout_reader.py)
- [device_matcher.py](file://parser/device_matcher.py)
- [export_json.py](file://export/export_json.py)
- [hierarchy_group_item.py](file://symbolic_editor/hierarchy_group_item.py)

## Detailed Component Analysis

### Hierarchical Device Expansion and Grouping
The hierarchy module defines:
- Array suffix parsing for devices and nets
- Integer parameter extraction with robust defaults and clamping
- HierarchyNode and DeviceHierarchy data structures
- Functions to build DeviceHierarchy from parameters and to reconstruct hierarchies from expanded devices
- Generation of leaf Device objects with parent-child metadata

Key behaviors:
- Effective multiplier computation considers array_count and m/nf combinations
- Two-level hierarchies (multiplier + fingers) and single-level expansions are supported
- Expanded devices are attached to leaf nodes with parent pointers and index metadata

```mermaid
classDiagram
class HierarchyNode {
+string name
+int level
+HierarchyNode[] children
+int multiplier_index
+int finger_index
+Device device
+is_leaf() bool
+leaf_count() int
+all_leaves() HierarchyNode[]
}
class DeviceHierarchy {
+HierarchyNode root
+int multiplier
+int fingers
+bool is_array
+int total_leaves
+needs_expansion() bool
}
HierarchyNode --> HierarchyNode : "children"
DeviceHierarchy --> HierarchyNode : "root"
```

**Diagram sources**
- [hierarchy.py](file://parser/hierarchy.py)

**Section sources**
- [hierarchy.py](file://parser/hierarchy.py)

### Netlist Parsing and Hierarchical Flattening
The netlist reader:
- Parses SPICE/CDL lines into Device objects with type, pins, and parameters
- Supports array suffixes (<N>) and expands m/nf into child devices
- Flattens hierarchical subcircuits (X-instances) with hierarchical prefixes
- Tracks block membership for top-level instances and subckt types
- Builds connectivity mapping from nets to devices and pins

```mermaid
flowchart TD
Start(["Start"]) --> Read["Read netlist file"]
Read --> Flatten["Flatten hierarchical subcircuits"]
Flatten --> Parse["Parse leaf lines into Device objects"]
Parse --> Expand["Expand array/multiplier/finger devices"]
Expand --> Group["Group by parent and reconstruct hierarchy"]
Group --> Connect["Build connectivity nets -> devices"]
Connect --> End(["Netlist ready"])
```

**Diagram sources**
- [netlist_reader.py](file://parser/netlist_reader.py)

**Section sources**
- [netlist_reader.py](file://parser/netlist_reader.py)

### Circuit Graph Construction and Merging
The circuit graph module:
- Adds device nodes with type, width, length, and nf
- Classifies nets by electrical role (bias, signal, gate) and adds edges accordingly
- Builds a merged graph by incorporating layout geometry (x, y, width, height, orientation)
- Excludes global supplies to preserve meaningful connectivity

```mermaid
flowchart TD
A["Add device nodes"] --> B["Iterate nets and connections"]
B --> C{"Net is global supply?"}
C --> |Yes| D["Skip net"]
C --> |No| E["Classify net role"]
E --> F["Compare all device pairs"]
F --> G["Assign relation (shared_bias/shared_source/shared_gate/shared_drain/connection)"]
G --> H["Add edge with attributes"]
H --> I["Return graph"]
```

**Diagram sources**
- [circuit_graph.py](file://parser/circuit_graph.py)

**Section sources**
- [circuit_graph.py](file://parser/circuit_graph.py)

### Visual Hierarchical Grouping
The symbolic editor provides:
- HierarchyGroupItem: a draggable bounding rectangle representing a parent device
- BlockItem: a movable block grouping multiple devices at the block level
- Navigation: double-click header to descend/ascend; drag to move children together
- Visibility management: when not descended, shows the group; when descended, shows children

```mermaid
classDiagram
class HierarchyGroupItem {
+descend() void
+ascend() void
+set_child_groups(child_groups) void
+get_all_descendant_devices() List
-_update_child_visibility() void
-_is_in_header(pos) bool
}
class BlockItem {
+set_snap_grid(grid_x, grid_y) void
+itemChange(change, value) QPointF
+mousePressEvent(event) void
+mouseMoveEvent(event) void
+mouseReleaseEvent(event) void
}
HierarchyGroupItem --> HierarchyGroupItem : "child groups"
BlockItem --> BlockItem : "child devices"
```

**Diagram sources**
- [hierarchy_group_item.py](file://symbolic_editor/hierarchy_group_item.py)
- [block_item.py](file://symbolic_editor/block_item.py)

**Section sources**
- [hierarchy_group_item.py](file://symbolic_editor/hierarchy_group_item.py)
- [block_item.py](file://symbolic_editor/block_item.py)
- [SYMBOLIC_HIERARCHY.md](file://docs/SYMBOLIC_HIERARCHY.md)

### Layout Extraction and Device Matching
The layout reader:
- Extracts device instances from OAS/GDS libraries, handling both flat and hierarchical layouts
- Recursively walks references to find leaf transistor instances and passive devices
- Preserves hierarchical prefixes and orientation information

The device matcher:
- Splits layout and netlist devices by type and logical parents
- Matches devices deterministically, collapsing expanded multi-finger netlists onto shared layout instances when counts differ

```mermaid
sequenceDiagram
participant LR as "layout_reader.py"
participant DM as "device_matcher.py"
participant NL as "Netlist"
participant LD as "Layout Devices"
LR->>LR : "extract_layout_instances(file)"
LR-->>LD : "list of device entries"
NL->>NL : "split_netlist_by_logical_device()"
LD->>LD : "split_layout_by_type()"
DM->>DM : "match_devices(netlist, layout_devices)"
DM-->>NL : "mapping {device_name : layout_index}"
```

**Diagram sources**
- [layout_reader.py](file://parser/layout_reader.py)
- [device_matcher.py](file://parser/device_matcher.py)

**Section sources**
- [layout_reader.py](file://parser/layout_reader.py)
- [device_matcher.py](file://parser/device_matcher.py)

### Practical Examples: Operational Amplifiers and Current Mirrors
- Miller OTA: demonstrates multi-stage amplifier with differential input, cascode load, and compensation capacitor; includes device types, terminal nets, and connectivity
- Current mirror: shows multi-finger NMOS/PMOS devices grouped under logical parents with geometry and electrical parameters

These examples illustrate:
- Multi-level hierarchies (multipliers and fingers) represented in JSON
- Terminal nets and connectivity for graph construction
- Geometry and orientation for merged graph building

**Section sources**
- [Miller_OTA_graph_compressed.json](file://examples/Miller_OTA/Miller_OTA_graph_compressed.json)
- [Current_Mirror_CM.json](file://examples/current_mirror/Current_Mirror_CM.json)

## Dependency Analysis
The system exhibits layered dependencies:
- Parser depends on hierarchy for device expansion and on netlist_reader for flattening and connectivity
- Symbolic editor depends on hierarchy_group_item for visualization and block_item for block-level grouping
- Layout extraction feeds into device matching, which informs merged graph construction
- Export consumes merged graphs for downstream AI placement

```mermaid
graph TB
NR["netlist_reader.py"] --> H["hierarchy.py"]
NR --> CG["circuit_graph.py"]
NR --> DM["device_matcher.py"]
H --> CG
LR["layout_reader.py"] --> DM
CG --> EJ["export_json.py"]
HGI["hierarchy_group_item.py"] --> BI["block_item.py"]
```

**Diagram sources**
- [netlist_reader.py](file://parser/netlist_reader.py)
- [hierarchy.py](file://parser/hierarchy.py)
- [circuit_graph.py](file://parser/circuit_graph.py)
- [layout_reader.py](file://parser/layout_reader.py)
- [device_matcher.py](file://parser/device_matcher.py)
- [export_json.py](file://export/export_json.py)
- [hierarchy_group_item.py](file://symbolic_editor/hierarchy_group_item.py)
- [block_item.py](file://symbolic_editor/block_item.py)

**Section sources**
- [netlist_reader.py](file://parser/netlist_reader.py)
- [hierarchy.py](file://parser/hierarchy.py)
- [circuit_graph.py](file://parser/circuit_graph.py)
- [layout_reader.py](file://parser/layout_reader.py)
- [device_matcher.py](file://parser/device_matcher.py)
- [export_json.py](file://export/export_json.py)
- [hierarchy_group_item.py](file://symbolic_editor/hierarchy_group_item.py)
- [block_item.py](file://symbolic_editor/block_item.py)

## Performance Considerations
- Hierarchical visualization: symbolic view reduces visible items, improving rendering performance at higher zoom levels
- Graph construction: excluding global supplies avoids dense edges and improves traversal speed
- Matching collapse: collapsing expanded multi-finger devices onto shared layout instances reduces mapping complexity
- Deep hierarchies: prefer descending only when needed; avoid unnecessary traversal of hidden children
- Layout extraction: recursive traversal is efficient but should be scoped to known device types to minimize overhead

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Devices visible when parent not descended: ensure child groups are set and visibility is updated
- Cannot descend into hierarchy: verify child groups or devices are added to the parent
- Selection not blocked for hidden devices: confirm the editor uses the hierarchy-aware scene
- Count mismatches in matching: the matcher collapses expanded multi-finger devices onto shared instances with warnings
- Global supplies affecting graph: ensure global nets are excluded from edge classification

**Section sources**
- [SYMBOLIC_HIERARCHY.md](file://docs/SYMBOLIC_HIERARCHY.md)
- [device_matcher.py](file://parser/device_matcher.py)
- [circuit_graph.py](file://parser/circuit_graph.py)

## Conclusion
The hierarchical design support system provides a robust framework for managing complex analog circuits:
- Hierarchical organization enables modular, reusable blocks with clear parent-child relationships
- Visual grouping and navigation improve usability and reduce cognitive load
- Hierarchical netlist parsing and circuit graph construction preserve design semantics
- Practical examples demonstrate real-world applicability for operational amplifiers and current mirrors
- Performance and best practices ensure scalability with deep hierarchies

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Best Practices for Deeply Nested Hierarchies
- Prefer symbolic view for high-level navigation; descend only when interacting with specific levels
- Use block-level grouping for functional regions (e.g., input stages, load networks)
- Maintain consistent naming conventions for parent, multiplier, and finger indices
- Collapse multi-finger expansions during matching to reduce mapping complexity
- Validate connectivity and geometry before exporting for AI placement

[No sources needed since this section provides general guidance]