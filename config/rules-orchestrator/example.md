# Orchestrator Mode Rules

## Task Decomposition Guidelines

When decomposing a design request:
- Each step should be independently verifiable
- Prefer smaller, atomic operations over large compound steps
- Always include verification steps after critical geometry operations
- Consider the natural Fusion 360 workflow order: sketch -> features -> assembly -> analysis -> export

## Mode Selection Heuristics

| Task Type | Recommended Mode | Examples |
|-----------|-----------------|----------|
| 2D geometry | sketch | Base profiles, cross-sections, construction geometry |
| 3D features | modeling | Extrusions, revolves, fillets, patterns, shells |
| Multi-body | assembly | Component placement, joints, grounding |
| Validation | analysis | Stress checks, interference, mass properties |
| Output | export | STL for 3D printing, STEP for interchange |
| Automation | scripting | Parametric patterns, batch operations |

## Quality Gates

Before advancing past a step:
1. Query the design state to confirm the operation succeeded
2. Verify dimensional accuracy matches the plan
3. Check for unintended geometry (extra bodies, broken sketches)
