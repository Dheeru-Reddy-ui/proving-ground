# Proving Ground — Problem Statement

*Last verified against sources: 6 October 2026.*

Every factual claim in this document carries a source tag. If a claim has no tag, it is our own reasoning and is labelled as such. Do not add a fact to this file without adding its source to the table in section 8.

---

## 1. The problem in one paragraph

AI can now write game test scripts quickly. A QA team still cannot trust those scripts without proof. A generated test can call functions that don't exist, break on the next build, or pass while checking nothing meaningful. Someone has to review, run, fix and maintain every one of them, which eats most of the time the AI was supposed to save.

**Proving Ground** is a trust layer for AI-generated game tests. A generated test enters the regression suite only after it has proven three things:

1. It runs.
2. It is deterministic.
3. It catches real, seeded bugs.

When the game's UI changes, the system classifies each failure and repairs broken locators. Repairs are never allowed to weaken what a test checks.

---

## 2. What EA has said publicly

**Source types used below:**

- **EA-JOB** — the official job posting on jobs.ea.com.
- **EA-SEED** — EA's applied research group, publishing on ea.com.
- **EA-ENG-2014** — an EA engineer speaking in a 2014 public interview.
- **EA-CEO (press)** — remarks by EA's CEO, quoted by the press. This is not an EA publication.

| # | Fact (paraphrased) | Source type | Source |
|---|---|---|---|
| F1 | The AI Software Engineer Intern role sits in **EA Studios – Quality Verification**, Hyderabad. It is hybrid, reports to the Quality Engineering team, and runs for 6 months. | EA-JOB | [S1] |
| F2 | The intern will write code, take part in reviews, support automated testing, and "help explore practical AI-assisted workflows for test generation and validation." | EA-JOB | [S1] |
| F3 | Responsibilities: work embedded with QA to support game production teams through testing phases; learn and maintain automation frameworks; design, write and implement automation scripts that improve feature coverage and reduce repetitive manual testing. | EA-JOB | [S1] |
| F4 | Required qualifications: exposure to evaluating AI-generated outputs for correctness, consistency or practical usefulness; familiarity with Git, automated build pipelines, logs, telemetry or test reporting tools; OOP and a scripting language such as Python. Nice to have: a relational database and Linux. | EA-JOB | [S1] |
| F5 | SEED cites a case study in which Battlefield V needed testing of 601 features, about 0.5M hours if done manually (~300 work years). | EA-SEED | [S2] |
| F6 | SEED says scripted bots (bots driven by hand-coded scripts) still do a lot of AAA testing. Scripting each test takes time, and when a feature change breaks an existing test, a new one has to be built. | EA-SEED | [S2] |
| F7 | A SEED paper describes adding an experimental reinforcement-learning system to an existing scripted-bot testing solution for AAA games, including Battlefield 2042 and Dead Space (2023). It states that going from research to production is fundamentally hard. | EA-SEED | [S3] |
| F8 | In 2014, EA's Michael Donat described FIFA 11 test-automation scripts as fragile: screens changed during development, scripts broke, and maintenance needed a software engineer almost full time. | EA-ENG-2014 | [S4] |
| F9 | In the same interview, he reported analysing roughly 300 crash bugs. Slightly more than half came up in the initial front-end screen transitions, so menu and front-end flows needed testing too. | EA-ENG-2014 | [S4] |
| F10 | In the same interview, he said EA wanted tests specified so they are maintainable and robust to change, in vocabulary QA staff and producers already use. | EA-ENG-2014 | [S4] |
| F11 | In late April 2026, at the iicon event in Las Vegas, EA CEO Andrew Wilson said that roughly 85% of EA's QA work is now done with some ML or AI-driven algorithm. He described that work as mostly basic tasks (booting, shutting down, checking for crashes), said EA hires more QA people than ever, and described AI use as "almost entirely augmentation." | EA-CEO (press) | [S5] |

**How to read these:**

- F8–F10 are from 2014. They show the maintenance problem is long-standing. They do not describe EA's current tooling.
- F11 is the CEO's approximate figure, as reported by Insider Gaming via Game File. Quote it as "the CEO said about 85%", never as a measured EA statistic.

---

## 3. Press reports (not company statements)

| # | Report | Source |
|---|---|---|
| P1 | Business Insider (October 2025), citing anonymous EA employees, reported that internal AI tools, including an in-house chatbot called ReefGPT, produced flawed code and hallucinations that staff had to correct. | [S6] (PC Gamer summary of the Business Insider report) |

This is reporting from unnamed sources. Never present it as EA's position. It is useful only as context that "AI output needs verification" is a real concern for people doing the work.

---

## 4. Our interpretation

> **This section is our hypothesis. EA has not said that trust in AI-generated tests is its main problem.**

Read together, the facts above suggest:

- **Generating test scripts is getting cheap.** EA's role explicitly asks for AI-assisted test generation (F2) and for people who can evaluate AI output (F4).
- **Trust is the open problem.** A generated test is only worth adding if someone can show it runs, is stable and detects defects. Otherwise it adds review and maintenance load (P1 is anecdotal support).
- **Maintenance is the long-standing cost.** Feature changes break scripted tests (F6), which has been true since at least 2014 (F8).
- **Front-end and menu flows matter.** They are a real source of defects (F9), so a UI-level test system is worth building.

So the question Proving Ground answers is:

> *Can we make AI-generated game tests trustworthy enough to enter a regression suite without a human re-checking every one, and keep them working as the UI changes?*

---

## 5. What Proving Ground does

1. **Map:** explore the game through AltTester and record a screen graph per build.
2. **Generate:** an LLM writes tests from feature specs. It may only use a typed page-object SDK.
3. **Prove:** each candidate test must pass four gates:
   - static checks;
   - 3/3 passes on a clean build;
   - catches at least one seeded bug from the dev split;
   - is not redundant with the existing suite.

   The result is ACCEPT, REVIEW or REJECT, with reasons.
4. **Heal:** classify each failure as product bug, test broken by UI change, flaky, or infrastructure. Repair broken locators. A repair can never weaken what a test checks.
5. **Triage:** deduplicate failures by signature, file issues with repro steps and artifacts, and send performance telemetry to Sentinel for regression detection.
6. **Report:** show a per-build verdict and record cost per accepted test.

---

## 6. What we do NOT claim

- We have no knowledge of EA's internal tools, engines or pipelines. Nothing here describes them.
- **TrashCat** (Unity's Endless Runner sample, as used in AltTester's examples) is a stand-in, not an EA game. EA has not said it uses Unity or AltTester for these titles.
- Seeded bugs are synthetic. Results show the method works on one game; they say nothing about impact at EA's scale.
- We never say "I solved EA's problem." We say: **"I built a working trust layer for AI-generated game tests and measured what it catches and what it costs."**

---

## 7. Success criteria

All of these are measured by the benchmark, never typed by hand. See `docs/BENCHMARK.md`.

| Metric | Definition |
|---|---|
| Holdout detection rate | Share of **holdout** seeded bugs killed by the accepted suite. The holdout set is never used for generation or tuning. |
| False-accept rate | Share of auto-accepted tests a human rejects on a blind audit of a random sample. |
| Flake rate | Share of accepted tests that fail on a clean build across N reruns. |
| Cost per accepted test | LLM spend plus device time, divided by the number of accepted tests. |
| Repair precision | Share of auto-repairs that pass on the drifted build **and** keep the same bug kills. |
| False-repair count | Repairs that would have hidden a real bug. **Target: 0 auto-accepted.** |

Every metric is compared across three arms:

- A human-written baseline suite
- Raw LLM output with no gates
- LLM output filtered by Proving Ground

---

## 8. Sources

| ID | Source | Type | Date | Accessed |
|---|---|---|---|---|
| S1 | EA Careers — *AI Software Engineer Intern*, Role ID 215929. https://jobs.ea.com/en_US/careers/JobDetail/AI-Software-Engineer-Intern/215929 | EA official | Live posting | 2026-10-06 |
| S2 | EA SEED — *SEED Applies Machine Learning Research to the Growing Demands of AAA Game Testing*. https://www.ea.com/seed/news/seed-ml-research-aaa-game-testing | EA official | 2023-08-29 | 2026-10-06 |
| S3 | EA SEED — *CoG 2023: Technical Challenges of Deploying Reinforcement Learning Agents for Game Testing* (paper: arXiv 2307.11105). https://www.ea.com/seed/news/cog23-challenges-deploying-rl-agents-game-testing | EA official | 2023-07-26 | 2026-10-06 |
| S4 | ACM Queue — *Automated QA Testing at EA: Driven by Events*, Queue vol. 12 no. 5 (2014). https://queue.acm.org/detail.cfm?id=2627372 | EA engineer, public interview | 2014 | 2026-10-06 |
| S5 | Insider Gaming — *EA CEO Says AI Is Used In 85% Of QA Work* (via Game File). https://insider-gaming.com/ea-ceo-ai-qa-work/ | Press quoting EA CEO | 2026-04-29 | 2026-10-06 |
| S6 | PC Gamer — summary of Business Insider's report on EA's internal AI use. https://www.pcgamer.com/gaming-industry/ea-employees-are-reportedly-frustrated-by-a-mandate-to-use-ai-mocking-the-policy-in-slack-and-suspecting-its-being-used-as-justification-for-layoffs/ | Press (anonymous sources) | 2025-10-27 | 2026-10-06 |

### Tooling facts (vendor documentation, not EA)

| ID | Fact | Source |
|---|---|---|
| V1 | AltTester Unity SDK is open source under GPL-3.0. It drives Unity apps from tests written in C#, Python, Java or Robot Framework. | https://github.com/alttester/AltTester-Unity-SDK |
| V2 | The Python driver is published on PyPI as `AltTester-Driver`. Its PyPI page lists the license as "Other/Proprietary" and links to AltTester's license file. | https://pypi.org/project/AltTester-Driver/ |
| V3 | **Free tier:** one connection, GUI mode only, for eligible users only (studios with annual revenue under €500,000; see AltTester's T&C). **Pro:** CI/CD batch mode and 2 parallel connections. | https://alttester.com/pricing/ |
| V4 | AltTester's Python examples drive **TrashCat** (Unity Endless Runner sample) on Android using page objects. AltTester Desktop must be running while tests run (v2.0.0+). | https://github.com/alttester/EXAMPLES-Python-Android-AltTrashCat |
| V5 | The AltTester API includes object finding, `GetAllElements`, input actions, screenshots, PlayerPrefs, scene commands, `SetTimeScale`, `CallStaticMethod`, `Get`/`SetStaticProperty`, component property and method calls, and log notifications. | https://alttester.com/docs/sdk/latest/pages/commands.html |
| V6 | **Known issue:** an IL2CPP build with Managed Stripping Level higher than Minimal may fail to connect to AltTester Desktop. | https://alttester.com/docs/sdk/latest/pages/known-issues.html |
