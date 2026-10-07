# Proving Ground

A trust layer for AI-generated game tests. An LLM writes UI tests for a Unity mobile game, and a test is admitted to the regression suite only after it proves three things: it runs, it is deterministic, and it catches real (seeded) bugs. When the game's UI changes, failures are classified and broken locators are repaired without ever weakening what a test checks.

**Status:** Phase 0 (foundation and feasibility) is in progress. There are no results yet. Every number this project reports will be generated from stored run IDs, never typed by hand.

- Problem statement, with a source for every fact: [docs/PROBLEM_STATEMENT.md](docs/PROBLEM_STATEMENT.md)
- Build plan: [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md)
- Progress: [docs/PROGRESS.md](docs/PROGRESS.md)
- Decisions: [docs/adr/](docs/adr/)

## Scope and limits

- One game: TrashCat, Unity's Endless Runner sample, driven through [AltTester](https://alttester.com/). It is a stand-in, not an EA title.
- Seeded bugs are synthetic, and tests run on a single Android device.
- Running the device side needs your own AltTester subscription; the AltTester Python driver is installed from PyPI under AltTester's license and is not part of this repository.
- Not affiliated with Electronic Arts, Unity or AltTester.

## Development

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pg --help
uv run pytest tests/unit -q
```

## License

[MIT](LICENSE), for this repository's own code and docs (see [ADR-0003](docs/adr/0003-license.md)). The AltTester driver and SDK and Unity's Endless Runner sample are not part of this repository and keep their own licenses.
