"""Keep the README scenario catalog synchronized with ``SCENARIOS``."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
_HEADER = "| Slug | Signal | Days | Time / Day | Duration | Components touched | Description |"


def _plain(cell: str) -> str:
    return cell.replace("`", "").replace("**", "").strip()


def _parse_scenario_rows(readme: str) -> dict[str, list[str]]:
    section = readme.split("### Scenario catalog", 1)[1].split("\n## ", 1)[0]
    table = section.split(_HEADER, 1)[1]
    rows: dict[str, list[str]] = {}
    for line in table.splitlines()[2:]:
        if not line.startswith("|"):
            break
        cells = [_plain(cell) for cell in line.strip("|").split("|")]
        assert len(cells) == 7, f"malformed scenario catalog row: {line}"
        assert cells[0], f"scenario catalog row has an empty slug: {line}"
        assert cells[0] not in rows, f"duplicate scenario catalog slug: {cells[0]}"
        rows[cells[0]] = cells
    assert rows, "README scenario catalog must contain at least one row"
    return rows


def _scenario_rows() -> dict[str, list[str]]:
    return _parse_scenario_rows((REPO_ROOT / "README.md").read_text(encoding="utf-8"))


def test_readme_scenario_catalog_rejects_duplicate_slugs() -> None:
    separator = "| ---- | ------ | ---- | ---------- | -------- | ------------------ | ----------- |"
    row = "| `duplicate` | low | 1 | 00:00 | instant | `authservice` | Example. |"
    readme = f"### Scenario catalog\n\n{_HEADER}\n{separator}\n{row}\n{row}\n\n## Tests\n"

    try:
        _parse_scenario_rows(readme)
    except AssertionError as exc:
        assert "duplicate scenario catalog slug: duplicate" in str(exc)
    else:
        raise AssertionError("duplicate scenario catalog slug was accepted")


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
