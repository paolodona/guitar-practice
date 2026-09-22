# Plan Summary

Last updated: 2026-09-22

## Active Plans

| # | Name | Description | Status | GitRef | Last Updated |
|---|------|-------------|--------|--------|--------------|
| 001 | woodshed-implementation | Build the Woodshed practice tool end to end across the roadmap's four phases, lifting code from `rambass-live` and `gx100` without depending on either. | IN PROGRESS | 289b0c7 | 2026-09-10 (Phase 2 gate closed) |
| 003 | instant-practice-playback | Play instantly on the real-time engine whenever a practice render is cold, and hard-cut to the sample-exact cached render at the next loop boundary once it lands. | IMPLEMENTED (manual verification pending) | c6f9261 | 2026-09-21 |
| 004 | loudness-matching | Measure each song's loudness automatically on bind (pure-numpy K-weighted integrated loudness, no new dependency) and apply a corrective gain at playback time only, so all songs sound similarly loud without manual volume riding. | IMPLEMENTED (manual listening verification pending) | 9f46103 | 2026-09-22 |

## Archived

Files in [`archive/`](archive/).

| # | Name | Description | Status | GitRef | Last Updated |
|---|------|-------------|--------|--------|--------------|
