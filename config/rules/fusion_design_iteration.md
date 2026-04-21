---
name: Fusion Design Iteration
version: 1.0
mode: design
autonomous: true
---

# Fusion Design Iteration Protocol

## Setup

1. Read the current design state from Fusion 360
2. Verify all required components are present
3. Initialize iteration tracking

## Constraints

- Do NOT modify locked components
- Each iteration must complete within the time budget
- Maintain dimensional constraints from the original specification
- Prefer simpler solutions over complex ones

## Execution

1. Analyze the current design for improvement opportunities
2. Propose a single modification
3. Apply the modification via Fusion 360 API
4. Verify the result meets constraints
5. If improved: keep the change and log it
6. If worse or failed: revert and try a different approach
7. Repeat from step 1

## Output Format

Each iteration should produce:
- `status`: success | failure | reverted
- `modification`: description of what was changed
- `metrics`: relevant measurements (dimensions, mass, etc.)

## Reliable Operation Patterns

> **TASK-156:** Proven patterns from real design sessions.

### Boolean Cuts
- Always specify `participantBodies = [target_body]` as a Python list
- Use `ThroughAllExtentDefinition` when possible -- avoids distance calculation errors
- Verify the target body exists before attempting the cut
- If a cut fails, check: (1) sketch plane orientation, (2) cut direction, (3) participant bodies

### Script Patterns
- Calling `execute_script` multiple times with variations is normal iterative workflow
- Each script should be self-contained (get fresh references to bodies/components)
- Always check return values from `.add()` calls -- they return None on failure

## Operation Sequencing Rules

> **TASK-158:** Operation ordering matters for geometry validity.

### Recommended Order
1. **Base geometry** -- boxes, cylinders, main body shapes
2. **Boolean operations** -- cuts, joins, intersections
3. **Structural features** -- ribs, bosses, walls, standoffs
4. **Fillets and chamfers** -- smooth edges BEFORE adding detail features
5. **Detail features** -- snap clips, text, cosmetic details
6. **Pattern/mirror operations** -- last, after all source features are complete

### Why Order Matters
- **Fillets before clips**: Snap clips that share edges with filleted surfaces create non-manifold geometry. Apply fillets first, then add clips.
- **Cuts before patterns**: Patterning a feature that includes a cut can create unexpected results if the cut target doesn't exist at all pattern positions.
- **Standoffs before mounting holes**: Create the boss first, then cut the hole through it.

## Timeline Surgical Editing Rules

> **TASK-218:** Use timeline editing tools instead of rebuilding from scratch.

- When a feature produces zero volume change or unexpected results, suppress or delete it before attempting a corrected version -- do not leave dead features in the timeline.
- Use `get_timeline` to inspect current timeline state before making surgical edits.
- Prefer `suppress_feature` over `delete_feature` when you may want to re-enable the feature later.
- After suppressing or deleting failed features, verify the design state with `get_body_list` or `validate_design` before proceeding.
- Use `reorder_feature` to fix sequencing problems instead of deleting and recreating features.
- Use `edit_feature` to modify parameters of existing features rather than creating duplicates.

## Coordinate System Reference

> **TASK-219:** Sketch coordinate mappings vary by construction plane.

### Standard Plane Mappings

| Plane | Sketch X -> World | Sketch Y -> World | Notes |
|-------|-------------------|-------------------|-------|
| XY    | World X           | World Y           | Standard, intuitive mapping |
| XZ    | World X           | World **-Z** (negated!) | Sketch Y is inverted relative to world Z |
| YZ    | World Y           | World Z           | Standard mapping |

### Offset Plane Mappings
- Offset planes **inherit the base plane's coordinate mapping**.
- An XZ plane offset at Y=-0.5 still maps sketch Y to world -Z (negated).
- Always verify sketch coordinates after creating sketches on non-XY planes by using `get_sketch_info` or placing test geometry and calling `take_screenshot`.

## Coincident Plane Rules

> **TASK-220:** Extrusions from coincident planes fail silently.

- **NEVER** sketch on a plane coincident with a body face for cut operations. Use an offset plane (even 0.01 mm / 0.001 cm offset) instead.
- If a cut extrusion produces zero volume change, check if the sketch plane is coincident with a body face.
- When in doubt, offset the sketch plane by a tiny amount (0.001 cm) from the target face.

## Script Variable Rules

> **TASK-222:** Each execute_script call has an isolated namespace.

- Always use `rootComp` directly in scripts. Never alias it to `root` or other variable names, as each `execute_script` call has an isolated namespace.
- Variables defined in one `execute_script` call do NOT carry over to subsequent calls.
- Use entity names (strings) to pass references between scripts, not object variables.
