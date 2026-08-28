# Baseline Governance

## 1. Baseline Roles

- Product / Requirement Baseline: confirmed requirements, target users,
  workflows, acceptance criteria, non-goals, and approved specifications.
- Architecture / Runtime Boundary Baseline: canonical owners, persistence
  boundaries, contracts, dependency direction, and compatibility constraints.

## 2. Design Defect

A confirmed mistake, gap, or contradiction in an approved requirement or
architecture baseline. Correct the baseline before aligning implementation.

## 3. Implementation Drift

Code, plans, or documentation deviating from a confirmed unchanged baseline.
Return to the baseline through the smallest stable correction.

## 4. Compatibility Aliases

- Architecture Defect = architecture-scoped Design Defect.
- Architecture Drift = architecture-scoped Implementation Drift.

Report findings with `scope: requirements`, `architecture`, or `both`.

## 5. Baseline Check Protocol

Before a non-trivial change:

1. Read the latest requirement baseline and architecture baseline.
2. Compare the proposed change with acceptance criteria and owner boundaries.
3. Identify unrecorded risks or assumptions.
4. Report `aligned`, `Design Defect`, `Implementation Drift`,
   `missing-authority`, or `needs-clarification`.

## 6. Architecture Review

Review ownership integrity, module boundaries, contract changes, dependency
direction, compatibility, retirement completeness, and net complexity after
each non-trivial implementation slice.

## 7. Hard Boundaries

- This file changes only through explicit user review.
- Baseline snapshots are evidence, not a replacement for approved specs.
- ADRs record accepted decisions; they do not replace this governance file.
