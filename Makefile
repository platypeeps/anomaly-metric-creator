# `make check` runs every step of `.github/workflows/ci.yml` that feeds the
# required `CI Result`, plus the local pre-commit and review preflight gate.
# `scripts/check.sh` holds the steps and says which CI steps it cannot run.

.PHONY: check

check:
	bash scripts/check.sh
