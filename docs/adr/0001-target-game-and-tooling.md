# ADR-0001: Target game and tooling: TrashCat + AltTester + Python on an Android phone

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Dheeru (owner)

## Context

Proving Ground needs a game it can test end to end. The requirements:

1. **Object-level UI automation.** Tests must find UI elements by name or path, not by pixels, so that locators can be versioned per build and repaired as data (Phase 3). Menu and front-end flows are a real source of defects (`PROBLEM_STATEMENT.md` F9).
2. **A game whose code we can change locally,** to add bug-flag hooks (Phase 1) and UI-drift hooks (Phase 3) without rebuilding per mutant.
3. **Python tests.** The target role lists a scripting language such as Python (F4), and the rest of the platform is Python.
4. **Public documentation and examples,** so every API call can be confirmed rather than guessed (`CLAUDE.md` rule 1).
5. **Affordable for one person,** with licences that allow a public portfolio repo.

## Decision

- **Game:** TrashCat, Unity's Endless Runner sample from the Unity Asset Store [U1], kept outside this repo (`PG_GAME_PROJECT_DIR`).
- **Engine version:** Unity **2021.3.45f2**. The sample lists 2021.3.6f1 and 6000.3.0f1 as supported [U1]. 2021.3.45f2 is the same LTS line and the editor AltTester's own TrashCat project uses [A8].
- **Automation:** AltTester Unity SDK **2.3.2** inside the game, AltTester Desktop **2.3.3** as the server, and the AltTester Python driver **2.3.2** (`AltTester-Driver`, import `alttester`). Versions are pinned together in `docs/VERSIONS.md`.
- **Platform:** Android on a physical ARM64 phone over USB, with the IL2CPP + ARM64 build described in `game/BUILD_TRASHCAT.md`.

## Alternatives considered

| Option | Main advantage | Why not chosen |
|---|---|---|
| AltTester's own TrashCat project ([A8], tutorial removed) | Already instrumented; no first-run tutorial | No license file. The Asset Store original has clearer terms. Kept as the fallback if the tutorial makes resets unworkable (see Consequences). |
| Unity Test Framework (PlayMode tests in C#) | No extra tooling; runs in the editor | Tests run inside the game's own code base, not as black-box UI tests against an installed build. That is the opposite of what a QA regression suite checks. |
| Image- or vision-based automation (screenshots, OCR, vision LLM agents) | Works on any game without instrumentation | Locators are pixels: slow, flaky and hard to repair deterministically. That defeats the determinism gate (G2) and locator healing (Phase 3). |
| Other open-source game-automation frameworks with Unity support | Open source | Fewer public examples against our target game. AltTester publishes Python page objects for TrashCat [V4] and documents every command we need [V5]. |
| Windows standalone build instead of Android | No adb, no USB, faster builds | Less representative of a mobile title. `pm clear` gives a clean, documented reset on Android. AltTester's Python examples target Android [V4]. |
| Android emulator instead of a phone | No device needed | The phone is available and `arm64-v8a`; an x86-64 emulator would need a different ABI in the APK and adds a second runtime to debug. |

## Consequences

- **Good:**
  - Tests address objects by name and path, so locators can live in versioned YAML maps and healing becomes a data patch (Phase 3).
  - Seeded bugs are toggled at runtime through our own hook scripts (`CallStaticMethod`), so one build serves every mutant.
- **Accepted limits:**
  - One game, which is a Unity sample and not an EA title, plus one device. Results show the method works here, not at EA's scale (`PROBLEM_STATEMENT.md` section 6).
  - Unity 2021.3 is end-of-life (Unity's release API lists end of life as 2025-12-01). That is acceptable for a local, never-distributed test build. The version is pinned and recorded.
  - The AltTester driver is proprietary: it may only be used with a valid AltTester subscription (ADR-0002, ADR-0003).
- **Risk to track:** the original game runs a tutorial on first launch. Every test starts from `pm clear`, so every test starts in the tutorial state. M0.4/M0.5 must establish what it blocks. If no clean workaround exists, revisit the fallback above.

## References

- [U1] Unity Asset Store, Endless Runner Mobile Sample Project: https://assetstore.unity.com/packages/essentials/tutorial-projects/endless-runner-sample-game-87901
- [A8] AltTester's TrashCat project: https://github.com/alttester/trashcat
- [V4], [V5], F4, F9: `docs/PROBLEM_STATEMENT.md`
- Unity release data for 2021.3.45f2: https://services.api.unity.com/unity/editor/release/v1/releases?version=2021.3.45f2
- Build steps: `game/BUILD_TRASHCAT.md`; versions: `docs/VERSIONS.md`
