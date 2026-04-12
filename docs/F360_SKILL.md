# Artifex360 -- Skill Reference

> **Purpose:** This document is loaded into the AI agent's system prompt as a complete
> reference for controlling Autodesk Fusion 360 through the MCP (Model Context Protocol)
> tool bridge. Every section is written for machine consumption -- precise, unambiguous,
> and structured for rapid lookup.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Coordinate System and Units](#2-coordinate-system-and-units)
3. [Design Hierarchy](#3-design-hierarchy)
4. [Available MCP Tools Reference](#4-available-mcp-tools-reference)
5. [Core Workflow Patterns](#5-core-workflow-patterns)
6. [Fusion 360 Python API Reference](#6-fusion-360-python-api-reference)
7. [Common Recipes / Cookbook](#7-common-recipes--cookbook)
8. [Best Practices for AI-Driven Design](#8-best-practices-for-ai-driven-design)
9. [Error Handling and Troubleshooting](#9-error-handling-and-troubleshooting)
10. [Limitations and Workarounds](#10-limitations-and-workarounds)

---

## 1. Overview

### 1.1 What Is Fusion 360

Autodesk Fusion 360 is a parametric 3D CAD/CAM/CAE application. Designs are built
through a timeline of operations: sketch geometry on construction planes, then apply
features (extrude, revolve, fillet, chamfer, etc.) to turn 2D profiles into 3D
solids. The timeline records every operation and allows non-destructive editing of
earlier steps.

### 1.2 How This System Works

```
User (browser) --> Flask + SocketIO --> Claude Agent --> MCP Tool Registry
                                                              |
                                                        FusionBridge
                                                              |
                                                     TCP 127.0.0.1:9876
                                                              |
                                                     Fusion 360 Add-in
                                                              |
                                                      Fusion 360 API
```

1. The user sends a natural-language request through the web UI.
2. Claude receives the request along with this skill document as context.
3. Claude decides which MCP tools to call (or writes a Python script).
4. The MCP tool registry validates inputs and dispatches to `FusionBridge`.
5. `FusionBridge` sends a JSON command over TCP to the Fusion 360 add-in.
6. The add-in marshals the command onto Fusion's UI thread and executes it.
7. The result (success/error, plus any data) flows back the same path.
8. Claude observes the result and decides the next action (agentic loop).

### 1.3 Communication Protocol

Newline-delimited JSON over TCP on `127.0.0.1:9876`.

**Request format:**
```json
{"id": "uuid", "command": "create_cylinder", "parameters": {"radius": 5.0, "height": 10.0}}
```

**Response format:**
```json
{"id": "uuid", "status": "success", "message": "Created cylinder r=5.0 h=10.0"}
```

Status values: `"success"`, `"error"`, `"simulation"`.

### 1.4 Simulation Mode

When Fusion 360 is not running or unreachable, the bridge enters simulation mode.
All tool calls return `"status": "simulation"` with descriptive messages. Results
are synthetic -- no real geometry is created. Inform the user when operating in
simulation mode so they understand results are not real.

---

## 2. Coordinate System and Units

### 2.1 Internal Unit: Centimeters

**All Fusion 360 API values are in centimeters (cm).** This is non-negotiable --
every numeric parameter passed to MCP tools or the Python API represents centimeters.

| User Says | Convert To (cm) | Formula |
|-----------|-----------------|---------|
| 1 inch | 2.54 cm | `inches * 2.54` |
| 1 mm | 0.1 cm | `mm * 0.1` |
| 1 m | 100 cm | `m * 100` |
| 1 foot | 30.48 cm | `feet * 30.48` |

**Quick reference conversion table:**

| Inches | cm | mm | cm |
|--------|-------|------|-------|
| 0.25 | 0.635 | 1 | 0.1 |
| 0.5 | 1.27 | 5 | 0.5 |
| 1 | 2.54 | 10 | 1.0 |
| 2 | 5.08 | 25 | 2.5 |
| 6 | 15.24 | 50 | 5.0 |
| 12 | 30.48 | 100 | 10.0 |

When the user does not specify units, **assume millimeters** for mechanical/
engineering contexts and convert accordingly. For vague requests like "a small box,"
use sensible defaults (e.g., 5 x 5 x 5 cm).

### 2.2 Coordinate Axes

Fusion 360 uses a **right-hand coordinate system**:

- **X axis** -- points right (red)
- **Y axis** -- points back/deep into screen (green)
- **Z axis** -- points up (blue)

The origin is at `(0, 0, 0)`.

> **Important context note:** In the Fusion 360 API, when sketching on the XY
> construction plane, the sketch's local coordinates map X to the global X axis and
> Y to the global Y axis. The extrusion direction from an XY-plane sketch is along
> the Z axis (up). When sketching on XZ, the sketch Y maps to global Z.

### 2.3 Construction Planes

| Plane | Normal Direction | Sketch X maps to | Sketch Y maps to | Extrude direction |
|-------|-----------------|-------------------|-------------------|-------------------|
| XY | Z (up) | Global X | Global Y | Z (up/down) |
| XZ | Y (front/back) | Global X | Global Z | Y (front/back) |
| YZ | X (left/right) | Global Y | Global Z | X (left/right) |

Access in the API:
```python
rootComp.xYConstructionPlane  # XY plane
rootComp.xZConstructionPlane  # XZ plane
rootComp.yZConstructionPlane  # YZ plane
```

### 2.4 Construction Axes

```python
rootComp.xConstructionAxis  # X axis
rootComp.yConstructionAxis  # Y axis
rootComp.zConstructionAxis  # Z axis
```

---

## 3. Design Hierarchy

### 3.1 Object Model

```
Document
  +-- Design (the parametric model)
        +-- Root Component
        |     +-- Sketches (collection of Sketch objects)
        |     +-- Features (extrude, revolve, fillet, chamfer, etc.)
        |     +-- Bodies (BRep solid bodies -- the visible geometry)
        |     +-- Construction Planes / Axes / Points
        |     +-- Occurrences (instances of sub-components)
        |     +-- Joints (constraints between components)
        +-- Sub-Components (nested components via occurrences)
        +-- Timeline (ordered list of all operations)
        +-- User Parameters (named dimensions)
```

### 3.2 Root Component vs Sub-Components

- **Root Component:** The top-level container. All geometry lives here unless
  explicitly placed in a sub-component.
- **Sub-Components:** Independent containers for separate parts. Each has its own
  sketches, features, bodies, and coordinate origin. Used for assemblies where
  distinct parts must move independently or be reused.

**When to use components:**
- Single-part designs: work in the root component.
- Multi-part assemblies: create a component for each distinct part.
- Repeated elements: create one component and use occurrences (instances).

### 3.3 Bodies vs Components

| Aspect | Body | Component |
|--------|------|-----------|
| Independence | Exists within a component | Has its own origin, sketches, features |
| Movement | Cannot move independently | Can be jointed/constrained to move |
| Reuse | Cannot be instanced | Can have multiple occurrences |
| Use case | Single solid shape | Distinct mechanical part |

### 3.4 Timeline

Every operation (sketch creation, extrusion, fillet, etc.) is recorded in the
timeline. The timeline enables:
- Non-destructive editing: go back and change a dimension.
- Parametric relationships: downstream features update when upstream changes.
- Undo/redo history.

### 3.5 User Parameters

Named dimensions that can be referenced by features. Changing a parameter
automatically updates all features that reference it.

```python
# Create a parameter
userParams = design.userParameters
valInput = adsk.core.ValueInput.createByString("25 mm")
userParams.add("wall_thickness", valInput, "mm", "Wall thickness")

# Use in a feature
dist = adsk.core.ValueInput.createByString("wall_thickness")
```

---

## 4. Available MCP Tools Reference

### 4.1 Implemented Tools

These tools are currently registered and functional in the MCP server.

#### `get_document_info`

Get information about the currently open Fusion 360 document.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| *(none)* | | | |

**Returns:** `name` (string), `save_path` (string), `is_dirty` (boolean).

**Example call:**
```json
{"name": "get_document_info", "input": {}}
```

---

#### `create_cylinder`

Create a solid cylinder body in the active design. Internally creates a circle
sketch on the XY plane and extrudes it along Z.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `radius` | number | yes | -- | Radius in cm |
| `height` | number | yes | -- | Height (extrusion distance) in cm |
| `position` | number[3] | no | [0,0,0] | [x, y, z] base center in cm |

**Example call:**
```json
{"name": "create_cylinder", "input": {"radius": 2.5, "height": 10.0}}
```

**Implementation detail:** The cylinder base circle is sketched at `(position[0],
position[1])` on the XY plane. The extrusion goes in the +Z direction by `height`.
The `position[2]` (Z) value shifts the sketch plane offset but the current
implementation places the sketch on the default XY plane.

---

#### `create_box`

Create a solid rectangular box body. Internally creates a center-point rectangle
sketch on the XY plane and extrudes along Z.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `length` | number | yes | -- | Length along X in cm |
| `width` | number | yes | -- | Width along Y in cm |
| `height` | number | yes | -- | Height along Z in cm |
| `position` | number[3] | no | [0,0,0] | [x, y, z] center of base rectangle in cm |

**Example call:**
```json
{"name": "create_box", "input": {"length": 10, "width": 5, "height": 3}}
```

**Implementation detail:** Uses `addCenterPointRectangle` centered at
`(position[0], position[1])` with the corner at `(px + length/2, py + width/2)`.
The box is centered on XY at the given position.

---

#### `create_sphere`

Create a solid sphere body. Internally creates a semicircular arc and line on the
XZ plane and revolves 360 degrees around the Z axis.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `radius` | number | yes | -- | Radius in cm |
| `position` | number[3] | no | [0,0,0] | [x, y, z] center of sphere in cm |

**Example call:**
```json
{"name": "create_sphere", "input": {"radius": 5.0}}
```

**Implementation detail:** The half-profile is sketched on the XZ plane (arc from
`(px, 0, pz+r)` sweeping pi radians, closed by a vertical line). Revolves around
the Z construction axis by 2*pi.

---

#### `get_body_list`

List all solid bodies in the active design.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| *(none)* | | | |

**Returns:** `bodies` (array of `{name, is_visible}`), `count` (integer).

---

#### `undo`

Undo the last operation in Fusion 360.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| *(none)* | | | |

**Implementation detail:** Executes `Commands.Undo` text command internally.

---

#### `save_document`

Save the currently active Fusion 360 document.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| *(none)* | | | |

---

### 4.2 Planned Tools

These tools follow the same dispatch pattern and are planned for implementation.
They are documented here so the agent understands the intended API surface. Until
implemented, use `execute_script` (once available) to achieve equivalent
functionality via the Python API.

#### Sketching Tools

| Tool | Parameters | Description |
|------|-----------|-------------|
| `create_sketch` | `plane`: string (`"XY"`, `"XZ"`, `"YZ"` or face index), `offset?`: number | Create a new sketch on a construction plane or planar face |
| `add_sketch_line` | `sketch_name`: string, `start`: number[2], `end`: number[2] | Add a line segment to an existing sketch |
| `add_sketch_circle` | `sketch_name`: string, `center`: number[2], `radius`: number | Add a circle to an existing sketch |
| `add_sketch_arc` | `sketch_name`: string, `center`: number[2], `start_angle`: number, `end_angle`: number, `radius`: number | Add an arc to an existing sketch |
| `add_sketch_rectangle` | `sketch_name`: string, `corner1`: number[2], `corner2`: number[2] | Add a two-point rectangle to an existing sketch |

#### Feature Tools

| Tool | Parameters | Description |
|------|-----------|-------------|
| `extrude` | `sketch_name`: string, `profile_index`: integer, `distance`: number, `operation?`: string (`"new_body"`, `"join"`, `"cut"`, `"intersect"`) | Extrude a sketch profile |
| `revolve` | `sketch_name`: string, `profile_index`: integer, `axis`: string, `angle`: number (radians) | Revolve a sketch profile around an axis |
| `add_fillet` | `body_name`: string, `edge_indices`: integer[], `radius`: number | Add fillet to specified edges |
| `add_chamfer` | `body_name`: string, `edge_indices`: integer[], `distance`: number | Add chamfer to specified edges |

#### Body and Component Tools

| Tool | Parameters | Description |
|------|-----------|-------------|
| `mirror_body` | `body_name`: string, `mirror_plane`: string (`"XY"`, `"XZ"`, `"YZ"`) | Mirror a body across a construction plane |
| `pattern_body` | `body_name`: string, `pattern_type`: string (`"rectangular"`, `"circular"`), `axis`: string, `count`: integer, `spacing`: number | Create a pattern of a body |
| `select_body` | `body_name`: string | Select a body by name for subsequent operations |
| `apply_material` | `body_name`: string, `material_name`: string | Apply a material/appearance to a body |
| `create_component` | `name`: string, `parent?`: string | Create a new component |
| `create_joint` | `component1`: string, `component2`: string, `joint_type`: string (`"rigid"`, `"revolute"`, `"slider"`, `"cylindrical"`, `"pin_slot"`, `"planar"`, `"ball"`) | Create a joint between two components |

#### Export Tools

| Tool | Parameters | Description |
|------|-----------|-------------|
| `export_stl` | `body_name?`: string, `filename`: string, `refinement?`: string (`"low"`, `"medium"`, `"high"`) | Export body or component as STL mesh |
| `export_step` | `filename`: string | Export design as STEP file |
| `export_f3d` | `filename`: string | Export design as F3D archive |

#### Utility Tools

| Tool | Parameters | Description |
|------|-----------|-------------|
| `take_screenshot` | `width?`: integer (default 1920), `height?`: integer (default 1080) | Capture current viewport as base64 PNG |
| `execute_script` | `script`: string, `timeout?`: integer (default 30) | Execute arbitrary Python script inside Fusion 360 |
| `redo` | *(none)* | Redo last undone operation |
| `get_timeline` | *(none)* | Get list of timeline entries with feature names and types |
| `set_parameter` | `name`: string, `value`: number, `unit?`: string | Set a named user parameter value |

### 4.3 Tool Result Schema Convention

All tool results follow this structure:

```json
{
  "status": "success | error | simulation",
  "message": "Human-readable description of what happened",

  "bodies": [],              // get_body_list
  "count": 0,                // get_body_list
  "name": "",                // get_document_info
  "save_path": "",           // get_document_info
  "is_dirty": false,         // get_document_info
  "image_base64": "...",     // take_screenshot
  "stdout": "...",           // execute_script
  "stderr": "...",           // execute_script
  "return_value": null,      // execute_script
  "timeline_entries": [],    // get_timeline
  "file_path": "..."         // export_* tools
}
```

Only fields relevant to the specific tool are included in the response.

---

## 5. Core Workflow Patterns

### Pattern 1: Sketch-Profile-Feature (Fundamental F360 Workflow)

This is the core pattern of all Fusion 360 modeling. Every 3D shape starts as a 2D
sketch that is then transformed into a 3D feature.

```
Step 1: Create a sketch on a plane (XY, XZ, or YZ)
Step 2: Add 2D geometry to the sketch (lines, circles, arcs, rectangles)
Step 3: Close the geometry to form a profile (Fusion auto-detects closed regions)
Step 4: Apply a 3D feature to the profile (extrude, revolve, loft, sweep)
Step 5: Optionally modify the result (fillet, chamfer, shell, pattern)
```

**Critical rule:** Sketch geometry must form **closed profiles** for features to
work. A gap of even 0.001 cm between endpoints will prevent profile detection. Use
coincident constraints or exact endpoint coordinates.

**Python example:**
```python
# Create a sketch on the XY plane
sketches = rootComp.sketches
sketch = sketches.add(rootComp.xYConstructionPlane)

# Draw a closed rectangle
lines = sketch.sketchCurves.sketchLines
lines.addTwoPointRectangle(
    adsk.core.Point3D.create(0, 0, 0),
    adsk.core.Point3D.create(5, 3, 0)
)

# The rectangle auto-creates a profile
profile = sketch.profiles.item(0)

# Extrude the profile into a solid body
extrudes = rootComp.features.extrudeFeatures
extInput = extrudes.createInput(
    profile,
    adsk.fusion.FeatureOperations.NewBodyFeatureOperation
)
distance = adsk.core.ValueInput.createByReal(2.0)  # 2 cm tall
extInput.setDistanceExtent(False, distance)
extrudes.add(extInput)
```

### Pattern 2: Primitive Creation (Simplified)

Use the built-in `create_box`, `create_cylinder`, and `create_sphere` MCP tools for
quick prototyping. These handle the sketch-and-feature workflow internally.

```
Step 1: Call create_box / create_cylinder / create_sphere with dimensions
Step 2: The tool internally creates a sketch and applies the appropriate feature
Step 3: A new body appears in the design
```

**Advantages:** Fast, single tool call, no sketch management needed.
**Limitations:** Limited positioning options, always axis-aligned, no direct control
over sketch or feature parameters.

### Pattern 3: Multi-Body Design (Boolean Operations)

Build complex shapes by combining or subtracting multiple bodies.

```
Step 1: Create the base body (e.g., a box)
Step 2: Create a sketch for the cutting or joining shape
Step 3: Extrude with operation = "cut" to subtract, "join" to add
Step 4: Repeat as needed for additional features
Step 5: Add fillets and chamfers to finish edges
```

**Feature operation types:**

| Operation | Enum Value | Effect |
|-----------|-----------|--------|
| New Body | `NewBodyFeatureOperation` | Creates a separate new body |
| Join | `JoinFeatureOperation` | Adds material to an existing body |
| Cut | `CutFeatureOperation` | Removes material from an existing body |
| Intersect | `IntersectFeatureOperation` | Keeps only the overlapping volume |

**Python example -- cutting a hole:**
```python
# Assume a box body already exists
# Create a sketch for the hole on the top face (or XY plane)
sketch = rootComp.sketches.add(rootComp.xYConstructionPlane)
circles = sketch.sketchCurves.sketchCircles
circles.addByCenterRadius(
    adsk.core.Point3D.create(2.5, 1.5, 0),  # center of hole
    0.5  # hole radius in cm
)

profile = sketch.profiles.item(0)
extInput = rootComp.features.extrudeFeatures.createInput(
    profile,
    adsk.fusion.FeatureOperations.CutFeatureOperation
)
# Cut through all -- use "Through All" extent
extInput.setAllExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection)
rootComp.features.extrudeFeatures.add(extInput)
```

### Pattern 4: Component-Based Assembly

For designs with multiple distinct parts that relate to each other.

```
Step 1: Create a component for each distinct part
Step 2: Within each component, create geometry (sketches, features)
Step 3: Position components using joints
Step 4: Define motion constraints (revolute, slider, rigid, etc.)
```

**Python example:**
```python
# Create a new component
occ = rootComp.occurrences.addNewComponent(adsk.core.Matrix3D.create())
newComp = occ.component
newComp.name = "Bracket"

# Work within the new component
sketch = newComp.sketches.add(newComp.xYConstructionPlane)
# ... add geometry in the component's local coordinate system
```

### Pattern 5: Revolve for Rotational Symmetry

For parts with circular cross-sections (bottles, vases, shafts, knobs).

```
Step 1: Create a sketch on XZ or YZ plane (profile must be on one side of the axis)
Step 2: Draw half the cross-section profile as a closed shape
Step 3: Define the revolution axis (typically a construction axis)
Step 4: Revolve the profile 360 degrees (or partial angle for cuts/features)
```

**Critical rule:** The sketch profile must be entirely on one side of the
revolution axis. Geometry crossing the axis will cause the revolve to fail.

### Pattern 6: Custom Script for Complex Operations

When predefined tools are insufficient, write a Python script using `execute_script`.

```
Step 1: Assess whether existing MCP tools can accomplish the task
Step 2: If not, write a Python script using the Fusion 360 API
Step 3: Call execute_script with the script string
Step 4: Parse stdout/stderr from the result
Step 5: Handle errors -- adjust script and retry if needed
```

**When to use scripts vs tools:**

| Scenario | Use |
|----------|-----|
| Create a simple box/cylinder/sphere | MCP tool |
| Create geometry at a specific position | MCP tool (with position param) |
| Complex sketch with multiple constraints | Script |
| Parametric pattern with calculated positions | Script |
| Operations not covered by MCP tools | Script |
| Querying detailed model information | Script |
| Applying constraints and dimensions | Script |

---

## 6. Fusion 360 Python API Reference

### Common Import Mistakes -- AVOID THESE

| WRONG | CORRECT |
|-------|---------|
| `from adsk.fusion import Point3D` | `Point3D` (pre-loaded) or `adsk.core.Point3D` |
| `from adsk.fusion import Vector3D` | `Vector3D` (pre-loaded) or `adsk.core.Vector3D` |
| `from adsk.fusion import ValueInput` | `ValueInput` (pre-loaded) or `adsk.core.ValueInput` |
| `import Point3D` | Already available as `Point3D` in script scope |

> **Point3D, Vector3D, Matrix3D, ObjectCollection, and ValueInput are in `adsk.core`, NOT `adsk.fusion`.**
> In the `execute_script` environment, they are pre-loaded as shortcuts -- do NOT import them.

### Pre-loaded Variables in execute_script
The script execution environment provides these variables automatically:
- `adsk` -- the full adsk module
- `app` -- `adsk.core.Application.get()`
- `design` -- the active Design
- `rootComp` -- `design.rootComponent`
- `ui` -- `app.userInterface`
- `Point3D` -- `adsk.core.Point3D` (shortcut)
- `Vector3D` -- `adsk.core.Vector3D` (shortcut)
- `Matrix3D` -- `adsk.core.Matrix3D` (shortcut)
- `ObjectCollection` -- `adsk.core.ObjectCollection` (shortcut)
- `ValueInput` -- `adsk.core.ValueInput` (shortcut)
- `FeatureOperations` -- `adsk.fusion.FeatureOperations` (shortcut)
- `math` -- Python math module

You do NOT need to import any of these. Use them directly:
```python
# CORRECT -- use shortcuts directly
p1 = Point3D.create(0, 0, 0)
p2 = Point3D.create(5, 0, 0)
dist = ValueInput.createByReal(3.0)
```

### 6.1 Core Application Classes

#### `adsk.core.Application`

The top-level application singleton.

```python
app = adsk.core.Application.get()
app.activeProduct       # -> Design (the current parametric design)
app.activeDocument      # -> Document (file info, save state)
app.userInterface       # -> UserInterface (dialogs, palettes)
app.activeViewport      # -> Viewport (camera, rendering)
app.executeTextCommand("Commands.Undo")  # execute named commands
```

#### `adsk.fusion.Design`

The parametric design object.

```python
design = adsk.fusion.Design.cast(app.activeProduct)
design.rootComponent    # -> Component (top-level component)
design.allComponents    # -> ComponentList
design.timeline         # -> Timeline (feature history)
design.designType       # -> DesignTypes (parametric vs direct)
design.userParameters   # -> UserParameters (named dimensions)
design.fusionUnitsManager  # -> FusionUnitsManager
```

#### `adsk.fusion.Component`

A component containing geometry and features.

```python
comp = design.rootComponent
comp.name                    # -> str
comp.sketches                # -> Sketches collection
comp.features                # -> Features (all feature types)
comp.bRepBodies              # -> BRepBodies (solid bodies)
comp.occurrences             # -> Occurrences (child component instances)
comp.joints                  # -> Joints
comp.asBuiltJoints           # -> AsBuiltJoints
comp.constructionPlanes      # -> ConstructionPlanes
comp.constructionAxes        # -> ConstructionAxes
comp.xYConstructionPlane     # -> ConstructionPlane
comp.xZConstructionPlane     # -> ConstructionPlane
comp.yZConstructionPlane     # -> ConstructionPlane
comp.xConstructionAxis       # -> ConstructionAxis
comp.yConstructionAxis       # -> ConstructionAxis
comp.zConstructionAxis       # -> ConstructionAxis
```

### 6.2 Sketch Classes

#### `adsk.fusion.Sketch`

A 2D sketch on a plane.

```python
sketch = comp.sketches.add(comp.xYConstructionPlane)
sketch.name                   # -> str (auto-assigned, e.g., "Sketch1")
sketch.sketchCurves           # -> SketchCurves
sketch.sketchPoints           # -> SketchPoints
sketch.profiles               # -> Profiles (auto-detected closed regions)
sketch.constraints            # -> GeometricConstraints
sketch.sketchDimensions       # -> SketchDimensions
sketch.isVisible              # -> bool
sketch.isComputeDeferred      # -> bool (set True to batch-add geometry faster)
```

#### `adsk.fusion.SketchCurves`

Container for all curve types in a sketch.

```python
curves = sketch.sketchCurves
curves.sketchLines            # -> SketchLines
curves.sketchCircles          # -> SketchCircles
curves.sketchArcs             # -> SketchArcs
curves.sketchFittedSplines    # -> SketchFittedSplines
curves.sketchConicCurves      # -> SketchConicCurves
```

#### `adsk.fusion.SketchLines`

Line creation methods.

```python
lines = sketch.sketchCurves.sketchLines

# Single line
line = lines.addByTwoPoints(
    adsk.core.Point3D.create(0, 0, 0),
    adsk.core.Point3D.create(5, 0, 0)
)

# Two-point rectangle (opposite corners)
rectLines = lines.addTwoPointRectangle(
    adsk.core.Point3D.create(0, 0, 0),
    adsk.core.Point3D.create(10, 5, 0)
)

# Center-point rectangle (center + corner)
rectLines = lines.addCenterPointRectangle(
    adsk.core.Point3D.create(0, 0, 0),    # center
    adsk.core.Point3D.create(5, 2.5, 0)   # corner
)
```

#### `adsk.fusion.SketchCircles`

Circle creation methods.

```python
circles = sketch.sketchCurves.sketchCircles

# By center and radius
circle = circles.addByCenterRadius(
    adsk.core.Point3D.create(0, 0, 0),  # center
    2.5                                   # radius in cm
)

# By three points
circle = circles.addByThreePoints(pt1, pt2, pt3)
```

#### `adsk.fusion.SketchArcs`

Arc creation methods.

```python
arcs = sketch.sketchCurves.sketchArcs

# By center, start point, and sweep angle (radians)
arc = arcs.addByCenterStartSweep(
    adsk.core.Point3D.create(0, 0, 0),   # center
    adsk.core.Point3D.create(2, 0, 0),   # start point
    3.14159                                # sweep angle in radians
)

# By three points
arc = arcs.addByThreePoints(startPt, midPt, endPt)
```

### 6.3 Feature Classes

#### `adsk.fusion.ExtrudeFeatures`

```python
extrudes = comp.features.extrudeFeatures

# Create input
extInput = extrudes.createInput(
    profile,                                            # Sketch profile
    adsk.fusion.FeatureOperations.NewBodyFeatureOperation  # Operation type
)

# Set distance extent
distance = adsk.core.ValueInput.createByReal(5.0)  # 5 cm
extInput.setDistanceExtent(
    False,       # isSymmetric (True = extrude both directions equally)
    distance     # distance value
)

# Or set symmetric extent (both directions)
extInput.setDistanceExtent(True, adsk.core.ValueInput.createByReal(2.5))

# Or set "through all" extent
extInput.setAllExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection)

# Execute
feature = extrudes.add(extInput)
```

#### `adsk.fusion.RevolveFeatures`

```python
revolves = comp.features.revolveFeatures

revInput = revolves.createInput(
    profile,                                              # Sketch profile
    comp.zConstructionAxis,                               # Revolution axis
    adsk.fusion.FeatureOperations.NewBodyFeatureOperation  # Operation
)

# Full revolution (360 degrees = 2*pi radians)
angle = adsk.core.ValueInput.createByReal(2 * 3.14159265358979)
revInput.setAngleExtent(False, angle)

feature = revolves.add(revInput)
```

#### `adsk.fusion.FilletFeatures`

```python
fillets = comp.features.filletFeatures
filletInput = fillets.createInput()

# Get edges to fillet (from a body)
body = comp.bRepBodies.item(0)
edges = adsk.core.ObjectCollection.create()
for i in range(body.edges.count):
    edges.add(body.edges.item(i))

# Add edge set with radius
filletInput.addConstantRadiusEdgeSet(
    edges,
    adsk.core.ValueInput.createByReal(0.2),  # radius in cm
    True                                       # isTangentChain
)

feature = fillets.add(filletInput)
```

#### `adsk.fusion.ChamferFeatures`

```python
chamfers = comp.features.chamferFeatures
chamferInput = chamfers.createInput2()

edges = adsk.core.ObjectCollection.create()
edges.add(body.edges.item(0))

# Equal distance chamfer
chamferInput.chamferEdgeSets.addEqualDistanceChamferEdgeSet(
    edges,
    adsk.core.ValueInput.createByReal(0.1),  # distance in cm
    True                                       # isTangentChain
)

feature = chamfers.add(chamferInput)
```

#### `adsk.fusion.ShellFeatures`

```python
shells = comp.features.shellFeatures

# Get faces to remove (open faces)
facesToRemove = adsk.core.ObjectCollection.create()
facesToRemove.add(body.faces.item(0))  # e.g., top face

shellInput = shells.createInput(facesToRemove)
shellInput.insideThickness = adsk.core.ValueInput.createByReal(0.2)  # wall thickness

feature = shells.add(shellInput)
```

### 6.4 Geometry Primitives

#### `adsk.core.Point3D`

```python
pt = adsk.core.Point3D.create(x, y, z)  # all values in cm
pt.x  # -> float
pt.y  # -> float
pt.z  # -> float
```

#### `adsk.core.Vector3D`

```python
vec = adsk.core.Vector3D.create(x, y, z)
vec.normalize()         # normalize to unit length
vec.length              # -> float
vec.crossProduct(other) # -> Vector3D
vec.dotProduct(other)   # -> float
```

#### `adsk.core.Matrix3D`

```python
matrix = adsk.core.Matrix3D.create()  # identity matrix
matrix.translation = adsk.core.Vector3D.create(5, 0, 0)  # translate 5 cm in X
matrix.setToRotation(angle, axis, origin)  # rotation
```

#### `adsk.core.ValueInput`

Two creation methods -- choose based on context:

```python
# By real number (always in cm for distances, radians for angles)
val = adsk.core.ValueInput.createByReal(5.0)

# By string expression (can include units, parameter names, math)
val = adsk.core.ValueInput.createByString("25 mm")
val = adsk.core.ValueInput.createByString("1 in")
val = adsk.core.ValueInput.createByString("wall_thickness * 2")
```

#### `adsk.core.ObjectCollection`

Generic collection for passing multiple objects to features.

```python
collection = adsk.core.ObjectCollection.create()
collection.add(someObject)
collection.add(anotherObject)
# Pass to feature inputs (e.g., edges for fillet, faces for shell)
```

### 6.5 Feature Operations Enum

```python
ops = adsk.fusion.FeatureOperations
ops.NewBodyFeatureOperation      # Create a new separate body
ops.JoinFeatureOperation         # Add material to existing body
ops.CutFeatureOperation          # Subtract material from existing body
ops.IntersectFeatureOperation    # Keep only overlapping volume
ops.NewComponentFeatureOperation # Create a new component with the body
```

### 6.6 Export Manager

```python
exportMgr = design.exportManager

# STL export
stlOptions = exportMgr.createSTLExportOptions(body, filepath)
stlOptions.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementMedium
exportMgr.execute(stlOptions)

# STEP export
stepOptions = exportMgr.createSTEPExportOptions(filepath, comp)
exportMgr.execute(stepOptions)

# F3D export
f3dOptions = exportMgr.createFusionArchiveExportOptions(filepath)
exportMgr.execute(f3dOptions)
```

### 6.7 Key Patterns for Script Writing

**Deferred compute for performance:**
```python
sketch.isComputeDeferred = True
# ... add many sketch entities ...
sketch.isComputeDeferred = False  # triggers recompute once
```

**Getting the correct profile from a sketch with multiple regions:**
```python
# Sketches with multiple closed regions have multiple profiles
# Profile index 0 is typically the outermost/first region
for i in range(sketch.profiles.count):
    profile = sketch.profiles.item(i)
    # Check area or position to pick the right one
    area = profile.areaProperties().area  # in cm^2
```

**Accessing bodies created by a feature:**
```python
feature = extrudes.add(extInput)
for i in range(feature.bodies.count):
    body = feature.bodies.item(i)
    body.name = "MyBody"
```

---

## 7. Common Recipes / Cookbook

### Recipe 1: Rounded Box (Box with Filleted Edges)

Create a box and fillet all edges for a smooth appearance.

**Using MCP tools then script:**
```
1. create_box(length=10, width=6, height=4)
2. execute_script to fillet edges
```

**Full script:**
```python
root = rootComp

# Create box sketch
sketch = root.sketches.add(root.xYConstructionPlane)
sketch.sketchCurves.sketchLines.addTwoPointRectangle(
    Point3D.create(0, 0, 0),
    Point3D.create(10, 6, 0)
)

# Extrude
prof = sketch.profiles.item(0)
extInput = root.features.extrudeFeatures.createInput(
    prof, FeatureOperations.NewBodyFeatureOperation
)
extInput.setDistanceExtent(False, ValueInput.createByReal(4.0))
ext = root.features.extrudeFeatures.add(extInput)

# Fillet all edges
body = ext.bodies.item(0)
edges = adsk.core.ObjectCollection.create()
for i in range(body.edges.count):
    edges.add(body.edges.item(i))

filletInput = root.features.filletFeatures.createInput()
filletInput.addConstantRadiusEdgeSet(edges, ValueInput.createByReal(0.5), True)
root.features.filletFeatures.add(filletInput)

print(f"Created rounded box with {body.edges.count} filleted edges")
```

### Recipe 2: Tube / Pipe

Create a hollow cylinder -- two concentric circles, extrude the ring profile.

```python
root = rootComp
outer_radius = 3.0  # cm
inner_radius = 2.5  # cm
tube_height = 10.0   # cm

sketch = root.sketches.add(root.xYConstructionPlane)
circles = sketch.sketchCurves.sketchCircles
circles.addByCenterRadius(Point3D.create(0, 0, 0), outer_radius)
circles.addByCenterRadius(Point3D.create(0, 0, 0), inner_radius)

# The area between the two circles forms a ring profile
# Find the ring profile (not the inner circle profile)
ringProfile = None
for i in range(sketch.profiles.count):
    p = sketch.profiles.item(i)
    area = p.areaProperties().area
    # Ring area = pi*(R^2 - r^2)
    expectedArea = 3.14159 * (outer_radius**2 - inner_radius**2)
    if abs(area - expectedArea) < 0.1:
        ringProfile = p
        break

if ringProfile:
    extInput = root.features.extrudeFeatures.createInput(
        ringProfile, FeatureOperations.NewBodyFeatureOperation
    )
    extInput.setDistanceExtent(False, ValueInput.createByReal(tube_height))
    root.features.extrudeFeatures.add(extInput)
    print(f"Created tube: outer_r={outer_radius}, inner_r={inner_radius}, h={tube_height}")
else:
    print("ERROR: Could not find ring profile")
```

### Recipe 3: Plate with Holes

A rectangular plate with circular holes cut through it.

```python
root = rootComp
plate_l, plate_w, plate_h = 20.0, 10.0, 0.5  # cm
hole_radius = 0.5  # cm
hole_positions = [(5, 2.5), (5, 7.5), (15, 2.5), (15, 7.5)]

# Create the plate
sketch1 = root.sketches.add(root.xYConstructionPlane)
sketch1.sketchCurves.sketchLines.addTwoPointRectangle(
    Point3D.create(0, 0, 0),
    Point3D.create(plate_l, plate_w, 0)
)
prof = sketch1.profiles.item(0)
extInput = root.features.extrudeFeatures.createInput(
    prof, FeatureOperations.NewBodyFeatureOperation
)
extInput.setDistanceExtent(False, ValueInput.createByReal(plate_h))
plateFeature = root.features.extrudeFeatures.add(extInput)

# Cut holes
sketch2 = root.sketches.add(root.xYConstructionPlane)
for (hx, hy) in hole_positions:
    sketch2.sketchCurves.sketchCircles.addByCenterRadius(
        Point3D.create(hx, hy, 0), hole_radius
    )

# Each circle creates its own profile -- extrude all as cuts
for i in range(sketch2.profiles.count):
    cutInput = root.features.extrudeFeatures.createInput(
        sketch2.profiles.item(i),
        FeatureOperations.CutFeatureOperation
    )
    cutInput.setAllExtent(adsk.fusion.ExtentDirections.PositiveExtentDirection)
    root.features.extrudeFeatures.add(cutInput)

print(f"Created plate with {len(hole_positions)} holes")
```

### Recipe 4: Gear-Like Shape

A simplified spur gear using script-based tooth generation.

```python
import math

root = rootComp
num_teeth = 20
module_val = 0.2      # cm (2 mm module)
pressure_angle = math.radians(20)
pitch_radius = num_teeth * module_val / 2
addendum = module_val
dedendum = 1.25 * module_val
outer_radius = pitch_radius + addendum
inner_radius = pitch_radius - dedendum
tooth_angle = 2 * math.pi / num_teeth
gear_thickness = 1.0  # cm

sketch = root.sketches.add(root.xYConstructionPlane)
lines = sketch.sketchCurves.sketchLines
arcs = sketch.sketchCurves.sketchArcs

sketch.isComputeDeferred = True

# Simplified gear profile -- trapezoidal teeth approximation
for i in range(num_teeth):
    a0 = i * tooth_angle
    a1 = a0 + tooth_angle * 0.15
    a2 = a0 + tooth_angle * 0.35
    a3 = a0 + tooth_angle * 0.50
    a4 = a0 + tooth_angle * 0.65
    a5 = a0 + tooth_angle * 0.85
    a6 = (i + 1) * tooth_angle

    points = [
        (inner_radius * math.cos(a0), inner_radius * math.sin(a0)),
        (inner_radius * math.cos(a1), inner_radius * math.sin(a1)),
        (outer_radius * math.cos(a2), outer_radius * math.sin(a2)),
        (outer_radius * math.cos(a3), outer_radius * math.sin(a3)),
        (inner_radius * math.cos(a4), inner_radius * math.sin(a4)),
        (inner_radius * math.cos(a5), inner_radius * math.sin(a5)),
    ]

    for j in range(len(points) - 1):
        lines.addByTwoPoints(
            Point3D.create(points[j][0], points[j][1], 0),
            Point3D.create(points[j+1][0], points[j+1][1], 0)
        )

    # Connect to next tooth
    next_a = (i + 1) * tooth_angle
    lines.addByTwoPoints(
        Point3D.create(points[-1][0], points[-1][1], 0),
        Point3D.create(inner_radius * math.cos(next_a),
                       inner_radius * math.sin(next_a), 0)
    )

sketch.isComputeDeferred = False

# Find and extrude the gear profile
if sketch.profiles.count > 0:
    prof = sketch.profiles.item(0)
    extInput = root.features.extrudeFeatures.createInput(
        prof, FeatureOperations.NewBodyFeatureOperation
    )
    extInput.setDistanceExtent(False, ValueInput.createByReal(gear_thickness))
    root.features.extrudeFeatures.add(extInput)
    print(f"Created gear: {num_teeth} teeth, pitch_r={pitch_radius:.2f} cm")
```

### Recipe 5: Bottle Shape (Revolve Profile)

Create a bottle by revolving a half-profile.

```python
root = rootComp

# Sketch on XZ plane -- profile is in X-Z space
sketch = root.sketches.add(root.xZConstructionPlane)
lines = sketch.sketchCurves.sketchLines
arcs = sketch.sketchCurves.sketchArcs

# Bottle profile (right side only, will revolve around Z axis)
# Bottom: flat base
p1 = Point3D.create(0, 0, 0)
p2 = Point3D.create(3, 0, 0)       # base radius = 3 cm

# Side: straight wall
p3 = Point3D.create(3, 0, 8)       # wall height = 8 cm

# Shoulder: taper inward
p4 = Point3D.create(1.5, 0, 12)    # neck start

# Neck: straight
p5 = Point3D.create(1.5, 0, 14)    # neck top

# Top rim
p6 = Point3D.create(0, 0, 14)      # center axis

# Draw the profile
lines.addByTwoPoints(p1, p2)
lines.addByTwoPoints(p2, p3)
lines.addByTwoPoints(p3, p4)
lines.addByTwoPoints(p4, p5)
lines.addByTwoPoints(p5, p6)
lines.addByTwoPoints(p6, p1)  # close the profile along the axis

# Revolve around Z axis (full 360 degrees)
prof = sketch.profiles.item(0)
revInput = root.features.revolveFeatures.createInput(
    prof,
    root.zConstructionAxis,
    FeatureOperations.NewBodyFeatureOperation
)
revInput.setAngleExtent(False, ValueInput.createByReal(2 * 3.14159265358979))
root.features.revolveFeatures.add(revInput)

print("Created bottle shape via revolve")
```

### Recipe 6: Phone Case (Hollow Box with Fillets)

A rectangular shell with rounded edges -- uses shell feature.

```python
root = rootComp
case_l, case_w, case_h = 15.0, 7.5, 1.0  # cm
wall = 0.15  # wall thickness in cm
fillet_r = 0.3  # edge fillet radius in cm

# Create the outer box
sketch = root.sketches.add(root.xYConstructionPlane)
sketch.sketchCurves.sketchLines.addTwoPointRectangle(
    Point3D.create(0, 0, 0),
    Point3D.create(case_l, case_w, 0)
)
prof = sketch.profiles.item(0)
extInput = root.features.extrudeFeatures.createInput(
    prof, FeatureOperations.NewBodyFeatureOperation
)
extInput.setDistanceExtent(False, ValueInput.createByReal(case_h))
ext = root.features.extrudeFeatures.add(extInput)

# Fillet all edges
body = ext.bodies.item(0)
edges = adsk.core.ObjectCollection.create()
for i in range(body.edges.count):
    edges.add(body.edges.item(i))
filletInput = root.features.filletFeatures.createInput()
filletInput.addConstantRadiusEdgeSet(edges, ValueInput.createByReal(fillet_r), True)
root.features.filletFeatures.add(filletInput)

# Shell -- remove the top face to hollow it out
body = root.bRepBodies.item(0)
# Find the top face (highest Z)
topFace = None
maxZ = -999
for i in range(body.faces.count):
    face = body.faces.item(i)
    bb = face.boundingBox
    centerZ = (bb.minPoint.z + bb.maxPoint.z) / 2
    if centerZ > maxZ:
        maxZ = centerZ
        topFace = face

if topFace:
    faces = adsk.core.ObjectCollection.create()
    faces.add(topFace)
    shellInput = root.features.shellFeatures.createInput(faces)
    shellInput.insideThickness = ValueInput.createByReal(wall)
    root.features.shellFeatures.add(shellInput)
    print("Created phone case shell")
```

### Recipe 7: L-Bracket with Holes

An L-shaped bracket with mounting holes and fillets.

```python
root = rootComp
thickness = 0.3  # cm (3 mm)
leg_a = 5.0      # horizontal leg length
leg_b = 4.0      # vertical leg height
width = 3.0      # bracket width (depth)
hole_r = 0.25    # hole radius
fillet_inner = 0.3

# Sketch L-profile on XZ plane
sketch = root.sketches.add(root.xZConstructionPlane)
lines = sketch.sketchCurves.sketchLines
p1 = Point3D.create(0, 0, 0)
p2 = Point3D.create(leg_a, 0, 0)
p3 = Point3D.create(leg_a, 0, thickness)
p4 = Point3D.create(thickness, 0, thickness)
p5 = Point3D.create(thickness, 0, leg_b)
p6 = Point3D.create(0, 0, leg_b)

lines.addByTwoPoints(p1, p2)
lines.addByTwoPoints(p2, p3)
lines.addByTwoPoints(p3, p4)
lines.addByTwoPoints(p4, p5)
lines.addByTwoPoints(p5, p6)
lines.addByTwoPoints(p6, p1)

# Extrude the L-profile along Y
prof = sketch.profiles.item(0)
extInput = root.features.extrudeFeatures.createInput(
    prof, FeatureOperations.NewBodyFeatureOperation
)
extInput.setDistanceExtent(False, ValueInput.createByReal(width))
ext = root.features.extrudeFeatures.add(extInput)

# Add mounting holes on the horizontal leg
holeSketch = root.sketches.add(root.xYConstructionPlane)
# Two holes along the horizontal leg
holeSketch.sketchCurves.sketchCircles.addByCenterRadius(
    Point3D.create(leg_a * 0.3, width / 2, 0), hole_r
)
holeSketch.sketchCurves.sketchCircles.addByCenterRadius(
    Point3D.create(leg_a * 0.7, width / 2, 0), hole_r
)
for i in range(holeSketch.profiles.count):
    cutInput = root.features.extrudeFeatures.createInput(
        holeSketch.profiles.item(i),
        FeatureOperations.CutFeatureOperation
    )
    cutInput.setAllExtent(adsk.fusion.ExtentDirections.PositiveExtentDirection)
    root.features.extrudeFeatures.add(cutInput)

print("Created L-bracket with mounting holes")
```

### Recipe 8: Threaded Fastener (Coil Feature)

Threads require the coil feature or thread feature in the API.

```python
root = rootComp
shaft_radius = 0.3   # M6 ~ 3mm radius
shaft_height = 2.0    # 20 mm
thread_pitch = 0.1    # 1 mm pitch
head_radius = 0.5     # 5 mm
head_height = 0.4     # 4 mm

# Create shaft
sketch1 = root.sketches.add(root.xYConstructionPlane)
sketch1.sketchCurves.sketchCircles.addByCenterRadius(
    Point3D.create(0, 0, 0), shaft_radius
)
prof1 = sketch1.profiles.item(0)
ext1 = root.features.extrudeFeatures.createInput(
    prof1, FeatureOperations.NewBodyFeatureOperation
)
ext1.setDistanceExtent(False, ValueInput.createByReal(shaft_height))
shaftFeature = root.features.extrudeFeatures.add(ext1)

# Create hex head
sketch2 = root.sketches.add(root.xYConstructionPlane)
# Hexagonal head -- approximate with 6-sided polygon
import math
hex_pts = []
for i in range(6):
    angle = i * math.pi / 3
    hex_pts.append(Point3D.create(
        head_radius * math.cos(angle),
        head_radius * math.sin(angle),
        0
    ))
hexLines = sketch2.sketchCurves.sketchLines
for i in range(6):
    hexLines.addByTwoPoints(hex_pts[i], hex_pts[(i + 1) % 6])

prof2 = sketch2.profiles.item(0)
ext2 = root.features.extrudeFeatures.createInput(
    prof2, FeatureOperations.JoinFeatureOperation
)
ext2.setDistanceExtent(False, ValueInput.createByReal(-head_height))  # negative = downward
root.features.extrudeFeatures.add(ext2)

# Add thread to shaft
body = root.bRepBodies.item(0)
# Find cylindrical face of shaft
cylFace = None
for i in range(body.faces.count):
    face = body.faces.item(i)
    if face.geometry.surfaceType == adsk.core.SurfaceTypes.CylinderSurfaceType:
        if abs(face.geometry.radius - shaft_radius) < 0.01:
            cylFace = face
            break

if cylFace:
    threads = root.features.threadFeatures
    threadDataQuery = threads.threadDataQuery
    # Get thread data for metric M6
    threadTypes = threadDataQuery.allThreadTypes
    # Use addThread to create external thread
    threadInfo = threads.createInput(cylFace, threadDataQuery)
    # Configure thread parameters as needed
    print("Thread face found -- apply thread data")

print("Created bolt with hex head")
```

### Recipe 9: Parametric Design with Named Parameters

Use user parameters so dimensions can be easily changed later.

```python
root = rootComp

# Define parameters
params = design.userParameters
params.add("box_length", ValueInput.createByString("100 mm"), "mm", "Box length")
params.add("box_width", ValueInput.createByString("60 mm"), "mm", "Box width")
params.add("box_height", ValueInput.createByString("40 mm"), "mm", "Box height")
params.add("corner_radius", ValueInput.createByString("5 mm"), "mm", "Corner fillet radius")
params.add("wall_thickness", ValueInput.createByString("2 mm"), "mm", "Shell wall thickness")

# Use parameters in features (by string expression)
sketch = root.sketches.add(root.xYConstructionPlane)
sketch.sketchCurves.sketchLines.addTwoPointRectangle(
    Point3D.create(0, 0, 0),
    Point3D.create(
        design.userParameters.itemByName("box_length").value,  # returns cm
        design.userParameters.itemByName("box_width").value,
        0
    )
)

prof = sketch.profiles.item(0)
extInput = root.features.extrudeFeatures.createInput(
    prof, FeatureOperations.NewBodyFeatureOperation
)
# Reference parameter by name for parametric updates
extInput.setDistanceExtent(
    False,
    ValueInput.createByString("box_height")
)
root.features.extrudeFeatures.add(extInput)

print("Created parametric box -- change parameters to update dimensions")
```

---

## 8. Best Practices for AI-Driven Design

### 8.1 Workflow Discipline

1. **Always check state before acting.** Call `get_document_info` and
   `get_body_list` before making changes to understand the current design state.

2. **Take screenshots after major operations** (when `take_screenshot` is
   available). Visual verification catches problems that text results miss.

3. **Save frequently.** Call `save_document` after completing significant
   operations, especially before attempting complex or risky changes.

4. **Use undo for recovery.** When an operation fails or produces wrong results,
   call `undo` to revert, then retry with corrected parameters.

### 8.2 Naming Conventions

- Give bodies descriptive names: `"base_plate"`, `"mounting_bracket"`, not `"Body1"`.
- Name components by their function: `"hinge_pin"`, `"left_panel"`.
- Name sketches by purpose when possible (set `sketch.name` in scripts).

### 8.3 Unit Handling

- When the user specifies inches, millimeters, or other units, **always convert
  to centimeters** before passing values to MCP tools or the API.
- State the conversion in the response so the user can verify: *"Converting 2
  inches to 5.08 cm for the API."*
- Use `ValueInput.createByString("25 mm")` in scripts when the original unit
  should be preserved in the timeline for readability.

### 8.4 Design Intent

- **Use parameters** for key dimensions that the user might want to change later.
- **Use components** when the design has distinct mechanical parts.
- **Use the sketch-profile-feature pattern** for precise control over geometry.
- **Use primitives** (`create_box`, etc.) only for quick prototypes or simple shapes.

### 8.5 Script vs Tool Selection

| Complexity | Approach |
|-----------|----------|
| Single primitive at origin | MCP tool (`create_box`, etc.) |
| Primitive at specific position | MCP tool with `position` parameter |
| Simple sketch + extrude | Planned MCP tools (`create_sketch` + `extrude`) or script |
| Complex sketch with constraints | Script |
| Multiple boolean operations | Script |
| Parametric design with named parameters | Script |
| Anything requiring loops or calculations | Script |

### 8.6 Efficiency

- For complex geometry, **prefer one script over many tool calls.** A single
  `execute_script` call is faster than 20 sequential MCP tool calls.
- Use `sketch.isComputeDeferred = True` in scripts when adding many sketch
  entities, then set it to `False` to trigger a single recompute.
- Batch-create features in scripts rather than making individual tool calls.

### 8.7 Communication with the User

- After each operation, confirm what was created with dimensions and position.
- If operating in simulation mode, clearly state that results are simulated.
- When converting units, show the conversion.
- When a design choice is ambiguous, ask for clarification rather than guessing.
- Describe the design strategy before executing: *"I will create an L-bracket by
  sketching the profile on the XZ plane, extruding it, then cutting mounting holes."*

---

## 9. Error Handling and Troubleshooting

### 9.1 Common API Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `"Active product is not a Fusion 360 Design"` | No design is open, or the active tab is a drawing/CAM workspace | Ensure a parametric design document is open and active |
| `"No active document"` | No file is open in Fusion 360 | Ask the user to open or create a new design |
| `"Timeout waiting for Fusion UI thread"` | Operation took longer than 30 seconds, or Fusion is busy with another operation | Retry the operation; if it persists, simplify the geometry |
| `"Unknown command"` | A tool was called that is not registered in the add-in | Check available tools; use `execute_script` for unregistered operations |
| `"Socket error"` / `"Connection closed"` | TCP connection to the add-in was lost | The bridge will fall back to simulation mode; ask the user to restart the add-in |

### 9.2 Sketch Profile Not Detected

**Symptom:** `sketch.profiles.count` is 0 after adding geometry.

**Causes and fixes:**

1. **Gaps in geometry.** Endpoints do not coincide exactly. Fix: Use the exact
   same `Point3D` object or identical coordinates for connected endpoints.

2. **Self-intersecting curves.** Lines cross each other in invalid ways. Fix:
   Simplify the profile or break it into non-intersecting segments.

3. **Open profile.** The sketch curves do not form a closed loop. Fix: Add the
   missing closing segment.

4. **Deferred compute still active.** If `isComputeDeferred` is `True`, profiles
   are not computed. Fix: Set `sketch.isComputeDeferred = False` before accessing
   profiles.

**Diagnostic script:**
```python
sketch = rootComp.sketches.itemByName("Sketch1")
if sketch:
    print(f"Profiles found: {sketch.profiles.count}")
    print(f"Curves: {sketch.sketchCurves.count}")
    print(f"isComputeDeferred: {sketch.isComputeDeferred}")

    # Check for open endpoints
    for i in range(sketch.sketchCurves.count):
        curve = sketch.sketchCurves.item(i)
        print(f"  Curve {i}: {curve.objectType} -- "
              f"start=({curve.startSketchPoint.geometry.x:.4f}, "
              f"{curve.startSketchPoint.geometry.y:.4f}) "
              f"end=({curve.endSketchPoint.geometry.x:.4f}, "
              f"{curve.endSketchPoint.geometry.y:.4f})")
else:
    print("Sketch not found")
```

### 9.3 Feature Failed Errors

**Symptom:** `extrudes.add(extInput)` or similar raises an exception or returns
a failed feature.

**Common causes:**

1. **Zero-thickness geometry.** The extrusion distance is 0 or the profile has
   zero area. Fix: Verify the distance value is positive and in centimeters.

2. **Self-intersecting result.** The feature would create invalid geometry (e.g.,
   a fillet radius larger than the edge length). Fix: Reduce the fillet/chamfer
   radius or simplify the geometry.

3. **No intersecting body for Cut/Join/Intersect.** The extruded volume does not
   overlap with any existing body. Fix: Verify the sketch is positioned correctly
   relative to existing bodies, or use `NewBodyFeatureOperation` instead.

4. **Profile is consumed.** A sketch profile can only be used by one feature. If
   you try to use the same profile for a second extrusion, it will fail. Fix:
   Create a new sketch or reference a different profile.

5. **Wrong extent direction.** Extruding in the wrong direction (positive vs
   negative) misses the target body. Fix: Try the opposite direction or use
   symmetric extent.

**Recovery pattern:**
```
1. Call undo to revert the failed operation
2. Diagnose the issue (check dimensions, positions, profile validity)
3. Adjust parameters
4. Retry the operation
```

### 9.4 Connection and Timeout Issues

| Issue | Symptom | Resolution |
|-------|---------|------------|
| Add-in not running | `"Connection refused"` on connect | Ask user to enable the add-in in Fusion 360: Tools > Add-Ins > Fusion360MCP |
| Fusion 360 busy | `"Timeout waiting for Fusion UI thread"` | Wait and retry; Fusion may be processing a heavy operation |
| Add-in crashed | `"Connection closed by add-in"` | Bridge falls back to simulation; ask user to restart add-in |
| Multiple clients | Unexpected responses | Only one client should connect to the add-in at a time |

### 9.5 Simulation Mode Awareness

When `status` is `"simulation"` in any tool result:

- No real geometry was created or modified.
- The response message is prefixed with `[SIM]`.
- Inform the user clearly: *"I am currently in simulation mode. No real changes
  were made in Fusion 360. To work with real geometry, please ensure Fusion 360
  is running with the MCP add-in enabled."*
- Continue the conversation normally -- the simulated responses allow testing
  workflow logic even without Fusion 360.

---

## 10. Limitations and Workarounds

### 10.1 Current Tool Limitations

| Limitation | Workaround |
|-----------|------------|
| Primitives created at or near origin only | Use `position` parameter or `execute_script` for arbitrary placement |
| No direct mesh/sculpt/T-spline support | Use `execute_script` with the Fusion 360 T-spline API |
| No loft or sweep tools registered | Use `execute_script` to access `loftFeatures` or `sweepFeatures` |
| No construction plane/axis creation tools | Use `execute_script` to create offset or angled construction planes |
| No sketch constraint tools (coincident, tangent, etc.) | Use `execute_script` to add geometric constraints |
| No sketch dimension tools | Use `execute_script` to add dimensional constraints |
| Material library access is limited | Use `execute_script` to enumerate and apply materials from the library |
| No direct face/edge selection by geometric query | Use `execute_script` with bounding box or surface type filtering |
| Cannot create new documents through tools | Ask the user to create a new document manually |

### 10.2 Performance Considerations

- **Large assemblies** (100+ components) may cause slow responses through the
  TCP bridge. Simplify operations or work on sub-assemblies.
- **Complex sketches** (1000+ entities) slow down profile detection. Use
  `isComputeDeferred` and minimize sketch entity count.
- **Script timeout** is 30 seconds by default. Very complex scripts may need
  the timeout increased, or should be split into multiple calls.
- **Screenshot capture** can take 1-3 seconds depending on scene complexity.

### 10.3 API Version Notes

- The Fusion 360 Python API evolves with application updates. Some methods may
  be deprecated or have changed signatures in newer versions.
- The `adsk.core`, `adsk.fusion`, and `adsk.cam` modules are always available
  inside the add-in environment.
- `math` and `json` are always available in `execute_script` globals.
- File I/O (`open`, `os.path`, etc.) is restricted in the script sandbox for
  security.

### 10.4 Geometry That Requires Special Approaches

| Shape | Approach |
|-------|----------|
| Helical/spiral features | Use `coilFeatures` API via script |
| Sheet metal | Use `sheetMetalRules` and bend features via script |
| Surface bodies (non-solid) | Use `patchFeatures` via script |
| Text/engraving | Use `sketchTexts` API via script, then extrude as cut |
| Imported geometry (STL, STEP) | Use `importManager` via script |
| Assembly motion simulation | Use `motionStudies` via script |
| Rendering/appearance | Use `design.appearances` via script |

### 10.5 Coordinate System Pitfalls

- **Sketch coordinates are 2D** within the sketch plane. When sketching on the
  XY plane, provide `Point3D.create(x, y, 0)` -- the Z value is ignored but
  must be 0.
- **When sketching on XZ plane**, the sketch's "Y" axis maps to global Z.
  Points are created as `Point3D.create(x, 0, z)` -- the Y value must be 0.
- **Body positions** are always in the global 3D coordinate system. After
  extrusion, the body exists at the absolute position defined by the sketch
  plane and extrusion direction.
- **Component transforms:** When working in a sub-component, geometry is
  defined in the component's local coordinate system. Use
  `occurrence.transform` to convert between local and global coordinates.

---

## Appendix A: Script Globals Reference

When using `execute_script`, the following variables are pre-populated in the
script's global scope:

| Variable | Type | Description |
|----------|------|-------------|
| `adsk` | module | The `adsk` top-level module |
| `app` | `adsk.core.Application` | The Fusion 360 application instance |
| `ui` | `adsk.core.UserInterface` | The user interface object |
| `design` | `adsk.fusion.Design` | The active parametric design |
| `rootComp` | `adsk.fusion.Component` | The root component of the design |
| `Point3D` | class | Shortcut for `adsk.core.Point3D` |
| `Vector3D` | class | Shortcut for `adsk.core.Vector3D` |
| `Matrix3D` | class | Shortcut for `adsk.core.Matrix3D` |
| `ValueInput` | class | Shortcut for `adsk.core.ValueInput` |
| `FeatureOperations` | enum | Shortcut for `adsk.fusion.FeatureOperations` |
| `math` | module | Python `math` module |
| `json` | module | Python `json` module |
| `print` | function | Captured print -- output goes to `stdout` in the result |

## Appendix B: Dimension Defaults for Vague Requests

When the user does not specify exact dimensions, use these sensible defaults:

| Description | Default Dimensions (cm) |
|-------------|------------------------|
| "a box" / "a cube" | 5 x 5 x 5 |
| "a small box" | 2 x 2 x 2 |
| "a large box" | 20 x 20 x 20 |
| "a cylinder" | radius=2.5, height=5 |
| "a sphere" | radius=2.5 |
| "a plate" | 10 x 10 x 0.5 |
| "a thin plate" | 10 x 10 x 0.1 |
| "a tube" / "a pipe" | outer_r=2.5, inner_r=2.0, height=10 |
| "a bracket" | 5 x 3 x 0.3 thickness |
| "a washer" | outer_r=1.0, inner_r=0.5, height=0.2 |
| "a bolt" / "a screw" | shaft_r=0.3, height=2.0, head_r=0.5 |

Always state the assumed dimensions to the user and offer to adjust.

## Appendix C: Quick Unit Conversion Functions

For use inside `execute_script`:

```python
def inches_to_cm(inches):
    """Convert inches to centimeters (Fusion 360 internal unit)."""
    return inches * 2.54

def mm_to_cm(mm):
    """Convert millimeters to centimeters (Fusion 360 internal unit)."""
    return mm * 0.1

def cm_to_inches(cm):
    """Convert centimeters to inches for display."""
    return cm / 2.54

def cm_to_mm(cm):
    """Convert centimeters to millimeters for display."""
    return cm * 10.0

def degrees_to_radians(degrees):
    """Convert degrees to radians (Fusion 360 angle unit)."""
    return degrees * 3.14159265358979 / 180.0
```

---

*End of Artifex360 Skill Reference.*