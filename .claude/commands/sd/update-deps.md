# SD Update Deps

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the Software Delivery (SD) update-deps workflow. Enumerate the open dependency-bot pull requests, classify each one by ecosystem, semver delta, and security advisories, merge the auto-merge classes sequentially under the housekeeping gate criteria, and park everything else with a one-line recommendation.

1. Resolve the `sd-update-deps` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, or requires unavailable tools, stop and report the exact blocker.
3. Use the skill as the primary instructions. It defines the fixed triage pipeline: dependency-PR enumeration, per-PR classification, the auto-merge classes of patch and minor dev-dependencies, GitHub Actions SHA bumps, and security patches, sequential gated merges with re-verification after each merge, and parked recommendations. Pass the `include-runtime-minor` and `dry-run` arguments through to the skill.
4. Merge sequentially, one pull request at a time, and only pull requests that are green, comment-clean, and current with their base under the housekeeping gate criteria, re-verifying after every prior merge. Majors are always manual: never auto-merge a major version bump.
5. If any PR enumeration, classification read, gate verification, merge, post-merge default-branch check, git command, or final validation fails, stop and report the command, exit status, and complete stdout/stderr output.
6. End with the dependency report in the skill's mandatory final-report format, with every mandatory section present.
