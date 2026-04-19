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
