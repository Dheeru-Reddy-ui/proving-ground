# ADR-0003: License for this repository

- **Status:** Proposed. Options only; the decision is Dheeru's.
- **Date:** 2026-10-07
- **Deciders:** Dheeru (owner)

This is an engineering summary to support a decision, not legal advice.

## Context

The repository is public on GitHub (since 2026-10-07) and has no license file. Without one, default copyright applies: people can read the code, and GitHub's terms let other users view and fork it on GitHub, but nobody may reuse it elsewhere.

**What the repo contains:** our own Python code, our own C# hook scripts (`game/unity_scripts/`), docs, feature specs, the bug catalog, and generated tests accepted by the gates.

**What it never contains** (CLAUDE.md rule 8): Unity Asset Store content, APKs, AltTester source, or code copied from AltTester's example repositories.

**Third-party licenses in play:**

| Component | License | How this project uses it |
|---|---|---|
| AltTester Unity SDK | GPL-3.0 ([repo](https://github.com/alttester/AltTester-Unity-SDK)); a commercial Non-GPL edition also exists | Imported into the local Unity project and compiled into an APK that is never distributed. Not in this repo. |
| AltTester Python driver (`AltTester-Driver`) | "AltTester® SDK License Agreement, SDK Bindings (Non-GPL Version)": proprietary. Only holders of a valid AltTester subscription may use it; redistribution, including through repositories, is prohibited ([license text](https://alttester.com/app/uploads/AltTester/sdks/alttester/LICENSE)) | Installed from PyPI by `uv sync`; never vendored. |
| Endless Runner sample (TrashCat) | Unity Asset Store EULA ([terms](https://unity.com/legal/as-terms)): assets may be used embedded in an application, not redistributed as source | Lives only in the local Unity project, outside this repo. |
| AltTester example repositories | No license file | Read as a reference for object names and patterns only. |

**Our C# hook scripts** (`PGBugFlags`, `PGTelemetry`, `PGUiDrift`) are standalone and do not call AltTester APIs; AltTester reaches them through reflection (`CallStaticMethod`). They are compiled into the same APK as the GPL SDK, but that APK is never distributed, so GPL distribution obligations are not triggered by this repo.

## Options

### A. MIT

Permissive: anyone may reuse the code with attribution.

- For: the most common license for portfolio and tooling projects; easy for reviewers to understand; nothing in the repo is GPL code.
- Against: no explicit patent grant.

### B. Apache-2.0

Permissive, plus an explicit patent grant and a NOTICE convention.

- For: the usual choice for tooling a company might adopt; clear contribution terms.
- Against: slightly heavier (license headers and NOTICE are conventional).

### C. GPL-3.0

Copyleft: derived works must stay GPL.

- For: matches the AltTester Unity SDK's open-source edition.
- Against: discourages reuse, for no benefit, since the repo contains no GPL code. It does not change the driver's proprietary terms either.

### D. No license (current state)

All rights reserved.

- For: no decision needed; nobody can reuse the work.
- Against: signals "look but don't touch"; contributions and reuse are legally blocked.

### E. Split: MIT or Apache-2.0 for code, CC BY 4.0 for docs

- For: docs such as the problem statement and ADRs get a license designed for prose.
- Against: two licenses to explain.

## True under every option

- A license on this repo covers only our files. It cannot grant rights to the AltTester driver or SDK, or to Unity's sample. The README tells users they need their own AltTester subscription and their own copy of the sample.
- Game screenshots stay out of the repo unless a decision is made about them (`artifacts/` is gitignored).
- A third-party notices section listing dependencies and their licenses is added to the README in Phase 4 (M4.6).

## Decision

Pending. No LICENSE file is added until Dheeru decides.

## References

- AltTester pricing and Lite terms: https://alttester.com/pricing/ , https://alttester.com/terms-and-conditions/
- AltTester-Driver on PyPI: https://pypi.org/project/AltTester-Driver/
- Choosing a license: https://choosealicense.com/
