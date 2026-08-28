# Aegis Workspace

This directory records durable product, architecture, and implementation
decisions for the local account, wallet, and task manager being designed
alongside this repository.

- `baseline/` contains point-in-time evidence about the existing project.
- `specs/` contains user-reviewable requirements and design specifications.
- `adr/` is reserved for accepted architecture decisions made during
  implementation planning or delivery.
- `INDEX.md` is the navigation entry point.

The existing `twitter-cli` command-line package remains the current X client
substrate. The management application described in the design specs is a new
application layer; it must not silently change existing CLI behavior.
