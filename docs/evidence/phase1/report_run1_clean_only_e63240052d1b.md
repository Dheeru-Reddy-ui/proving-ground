# Proving Ground run report

Generated 2026-10-08 05:44 UTC by `pg report` from stored rows. Build `e63240052d1b`.

| Generation run | Feature | Model | Prompt | Status | Requested | Prove run |
|---|---|---|---|---|---|---|
| 1 | store | gemini-3.8-flash | generate_tests_v1 (`e032cb0c14bb`) | succeeded | 8 | `prove-1` |

Human baseline runs used for novelty and the kill matrix: none.

## Candidates by decision

| Decision | Count |
|---|---|
| accept | 0 |
| review | 0 |
| reject | 2 |
| pending | 6 |
| review approved by a human | 0 |

## Main rejection reasons

| Reason | Rejections |
|---|---|
| `not_deterministic` | 2 |

## Determinism (G2)

Candidates that passed 3/3 clean runs: **6/8** of those that reached G2.

## Dev kill matrix

K = killed 2/2, u = failed 1/2 (unstable, not counted), . = survived, blank = not run (relevance filter).

No bug runs recorded.

## Cost and device time

| Measure | Value |
|---|---|
| LLM tokens in / out | 4818 / 12614 |
| LLM cost (configured rates) | $0.000000 |
| LLM cost per accepted test | n/a (no ACCEPT) |
| Device time, proving | 8.8 min |
| Device time per accepted test | n/a (no ACCEPT) |
| Device time, human baseline | 0.0 min |
| Executions (attempts) / infra attempts | 20 / 0 |

## Candidates

| Id | Name | Specs | Decision | Trust | First reason |
|---|---|---|---|---|---|
| 1 | `test_store_open_and_close_navigation` | STORE-1, STORE-11 | pending | 46 | waiting for valid runs: G3 |
| 2 | `test_store_shows_balances_and_four_sections` | STORE-2, STORE-3 | pending | 48 | waiting for valid runs: G3 |
| 3 | `test_store_items_show_prices` | STORE-4 | pending | 49 | waiting for valid runs: G3 |
| 4 | `test_purchase_deducts_exact_prices` | STORE-5, STORE-10 | pending | 49 | waiting for valid runs: G3 |
| 5 | `test_buy_power_up_adds_to_inventory` | STORE-6, STORE-10 | pending | 44 | waiting for valid runs: G3 |
| 6 | `test_buy_theme_becomes_owned_and_selectable` | STORE-7 | pending | 48 | waiting for valid runs: G3 |
| 7 | `test_unaffordable_item_cannot_be_bought` | STORE-8 | reject | 15 | failed 1 of 1 clean runs (test_error) |
| 8 | `test_owned_character_and_theme_cannot_be_bought` | STORE-9 | reject | 15 | failed 1 of 1 clean runs (test_error) |

