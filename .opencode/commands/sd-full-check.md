---
description: Run the Software Delivery (SD) full-check gate for deterministic checks, local review, and readiness reporting.
---

# Software Delivery Full Check

Run the Software Delivery (SD) full-check gate for the current repository. This typically includes configured deterministic checks, package-script checks when available, install audit, review preflight, and optional local review providers.

1. Resolve the `sd-full-check` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, or requires unavailable tools, stop and report the exact blocker.
3. Use that skill as the primary instructions for this workflow. The skill is the source of truth for the exact checks, scripts, and report format.
4. If any check or command fails, stop and report the command, exit status, and complete stdout/stderr output.
5. If all checks pass, report that the full-check gate passed and summarize the checks or providers that ran.
6. Do not edit, stage, commit, or push files unless the user separately asks for fixes.
