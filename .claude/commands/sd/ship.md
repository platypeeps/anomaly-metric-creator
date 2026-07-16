# SD Ship

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the Software Delivery (SD) ship workflow. Take the current feature branch through the standard publish-to-merge chain — create or reuse the pull request, run the review loop, watch the pull request until it settles, then merge through the housekeeping gate — stopping at the `until=pr|review|merge` stop-point (default `merge`) or immediately with a failed or blocked stage's report.

1. Resolve the `sd-ship` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, or requires unavailable tools, stop and report the exact blocker.
3. Use the skill as the primary instructions. It defines the fixed four-stage chain: the sd-create-pr flow, the sd-review-pr loop, the sd-watch-pr flow, and the sd-housekeeping merge gate, with each stage's own preconditions, gates, and reports staying authoritative, stop-points between stages, and a failed or blocked stage stopping the chain with that stage's report. Pass the `until=pr|review|merge` stop-point and pass-through stage arguments such as `timeout-minutes=N` through to the skill.
4. This command adds no new gate logic; every stage's own gates remain authoritative. Any merge happens only through the `sd-housekeeping` gate, which remains the only merge authority. Never bypass or weaken a stage's behavior and never force-push.
5. If any stage flow, delegated skill step, git command, pull-request operation, or final validation fails, stop and report the command, exit status, and complete stdout/stderr output.
6. End with the ship report in the skill's mandatory final-report format, with every mandatory section present.
