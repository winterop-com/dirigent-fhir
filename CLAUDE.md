# Claude Code Instructions

This repository is the FHIR pack, a sibling of dirigent in the same workspace, and follows dirigent's
instructions in full:

@../dirigent/CLAUDE.md

## Here

- The pack is FHIR as a system of its own. It depends on the FHIR engine and on dirigent's packages,
  and imports no DHIS2 code: a FHIR to DHIS2 conversion belongs in `dirigent-dhis2`.
- The REST client in `src/dirigent_fhir/client.py` is the pack's own.
- A change that adds a pack, or makes a block reach an outside system the architecture model does
  not show yet, updates `architecture/workspace.dsl` in dirigent in a PR there, and regenerates its
  docs page with `make architecture-export-docs`.
- Run this repository's gates with `env -u VIRTUAL_ENV` when a dirigent venv is active.
