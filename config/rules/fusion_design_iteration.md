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
