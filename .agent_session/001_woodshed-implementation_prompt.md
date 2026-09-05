# Original Prompt: Woodshed — full build
Plan: 001 | Name: woodshed-implementation | Created: 2026-09-05 | GitRef: 289b0c7

## Related Files
- **Context**: `.agent_session/001_woodshed-implementation_context.md` — research findings
- **Plan**: `.agent_session/001_woodshed-implementation_plan.md` — implementation steps

---

## Original User Request

> look at this repo, I want to create a comprehensive plan to implement the app as per
> docs/specs. the specs may reference stuff that is already implemented in
> "C:\Users\paolo\prj\rambass-live" and "C:\Users\paolo\prj\gx100". Lift what is required
> but do not create a dependency on the other repos.

> ensure the plan is going to implement based on these artifacts
> https://claude.ai/code/artifact/618bb99d-abcd-4827-a11d-413c6dc3a6c8

## Direction given during planning

On plan depth:

> All four phases at equal depth, with notes to review the plan at implementation based on
> the previous phases. design with dynamic workloads in mind so that implementation can run
> as unattended as possible with multiple sub agents. plan with a high model (opus/fable)
> and implementation with sonnet or opus to reduce token consumption. Do the hard thinking
> first, with simpler execution of the plan.

On the audio engine and licensing:

> which one gives the best results? I dont mind this repo being public

→ Rubber Band on both sides (CLI for renders, WASM for the real-time engine), accepting
GPLv2+. Rationale in the plan's "Decisions taken".

Also, during the same session: *"make main the default branch"* — done,
`paolodona/guitar-practice` now defaults to `main`.
