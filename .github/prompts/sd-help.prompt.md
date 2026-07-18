---
# description is shown by GitHub prompt pickers; mode: agent means the prompt can use tools and run an interactive workflow.
description: Discover, compare, and understand Software Delivery commands without running the selected workflow.
mode: agent
---

# SD Help

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the read-only Software Delivery help workflow for the user's complete
request.

1. Resolve the `sd-help` skill by name using the agent's trusted skill discovery
   mechanism for installed skills.
2. Verify that its `references/command-catalog.md` and
   `references/examples.md` files are readable. If the skill or required
   references are missing, unreadable, empty, duplicated, malformed, fail
   validation, define contradictory safety rules, or require unavailable tools,
   stop and report the exact blocker.
3. Use the skill as the primary instructions. Pass the user's help text through
   unchanged; it defines list, explain, compare, recommend, examples, tour,
   availability, version, and response-shape behavior.
4. Treat inspected command skills as documentation only. Do not execute a
   selected workflow, modify repository state, create tasks, run checks, call
   GitHub, or install/update the pack during this help request.
5. End with the skill's response shape and one copy-ready platform-native
   invocation when a responsible recommendation is possible. Execution always
   requires a separate explicit request.
