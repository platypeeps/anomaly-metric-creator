# `make check` is the merge gate: `sd-ship merge` runs it and posts
# `sd/local-gate`, the one required check. `scripts/check.sh` holds the steps.

.PHONY: check

check:
	bash scripts/check.sh
