"""Keep the README scenario catalog synchronized with ``SCENARIOS``."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _plain(cell: str) -> str:
    return cell.replace("`", "").replace("**", "").strip()


def _scenario_rows() -> dict[str, list[str]]:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Scenario catalog", 1)[1].split("\n## ", 1)[0]
    header = "| Slug | Signal | Days | Time / Day | Duration | Components touched | Description |"
    table = section.split(header, 1)[1]
    rows: dict[str, list[str]] = {}
    for line in table.splitlines()[2:]:
        if not line.startswith("|"):
            break
        cells = [_plain(cell) for cell in line.strip("|").split("|")]
        assert len(cells) == 7, f"malformed scenario catalog row: {line}"
        assert cells[0], f"scenario catalog row has an empty slug: {line}"
        rows[cells[0]] = cells
    assert rows, "README scenario catalog must contain at least one row"
    return rows


def test_readme_scenario_catalog_matches_registry(amc) -> None:
    rows = _scenario_rows()
    assert set(rows) == set(amc.SCENARIOS)

    for slug, scenario in amc.SCENARIOS.items():
        cells = rows[slug]
        assert cells[1] == scenario.severity
        assert int(cells[2]) == scenario.days_required
        documented_components = tuple(
            component.strip() for component in cells[5].split(",") if component.strip()
        )
        assert documented_components
        assert documented_components == scenario.components_touched
