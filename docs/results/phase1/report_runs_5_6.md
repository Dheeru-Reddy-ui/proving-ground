# Proving Ground run report

Generated 2026-10-08 08:08 UTC by `pg report` from stored rows. Build `87d396162a05`.

| Generation run | Feature | Model | Prompt | Status | Requested | Prove run |
|---|---|---|---|---|---|---|
| 5 | store | gemini-3.5-flash | generate_tests_v2 (`9607c2de5ed2`) | succeeded | 8 | `prove-5` |
| 6 | run_and_gameover | gemini-3.5-flash | generate_tests_v2 (`3acf45bb0c51`) | succeeded | 8 | `prove-6` |

Human baseline runs used for novelty and the kill matrix: none.

## Candidates by decision

| Decision | Count |
|---|---|
| accept | 1 |
| review | 12 |
| reject | 3 |
| pending | 0 |
| review approved by a human | 0 |

## Main rejection reasons

| Reason | Rejections |
|---|---|
| `redundant` | 1 |
| `not_deterministic` | 1 |
| `unknown_sdk_member` | 1 |

## Determinism (G2)

Candidates that passed 3/3 clean runs: **14/15** of those that reached G2.

## Dev kill matrix

K = killed 2/2, u = failed 1/2 (unstable, not counted), . = survived, blank = not run (relevance filter).

| Test | SB02 | SB04 | SB05 | SB08 | SB09 | SB11 | SB13 | SB14 | SB15 | SB16 |
|---|---|---|---|---|---|---|---|---|---|---|
| c9 test_store_navigation_and_balances_main_menu | . | . | . | . | . |  |  |  |  | . |
| c10 test_store_navigation_game_over | . | . | . | . | . | . | . | . | . | . |
| c11 test_store_item_prices | . | . | . |  |  |  |  |  |  | . |
| c12 test_buy_power_up_inventory_and_balance | . | . | . |  |  |  |  |  |  | . |
| c13 test_buy_character_unlock_and_select | K | . | . |  |  |  |  |  |  | . |
| c14 test_buy_theme_unlock_and_select | . | . | . |  |  |  |  |  |  | . |
| c15 test_store_unaffordable_items_highlighting | . | . | . |  |  |  |  |  |  | . |
| c16 test_store_already_owned_items | . | . | . |  |  |  |  |  |  | . |
| c18 test_pause_and_resume |  |  |  | . | . |  |  | . |  |  |
| c19 test_pause_and_quit |  |  |  | . | . |  |  | . |  |  |
| c20 test_decline_continue_to_game_over |  |  |  | . | . |  | . | . |  |  |
| c21 test_continue_with_premium_success |  |  |  | . | . |  |  | . |  |  |
| c22 test_continue_with_premium_insufficient |  |  |  |  |  |  |  | . |  |  |
| c24 test_game_over_navigation | . | . | . |  |  |  | . | . |  | . |

## Cost and device time

| Measure | Value |
|---|---|
| LLM tokens in / out | 9742 / 14129 |
| LLM cost (configured rates) | $0.000000 |
| LLM cost per accepted test | $0.000000 |
| Device time, proving | 64.1 min |
| Device time per accepted test | 64.1 min |
| Device time, human baseline | 0.0 min |
| Executions (attempts) / infra attempts | 149 / 3 |

## Candidates

| Id | Name | Specs | Decision | Trust | First reason |
|---|---|---|---|---|---|
| 9 | `test_store_navigation_and_balances_main_menu` | STORE-1, STORE-2, STORE-3, STORE-11 | review | 59 | killed none of 6 relevant dev bug(s) |
| 10 | `test_store_navigation_game_over` | STORE-1, STORE-11 | review | 57 | killed none of 10 relevant dev bug(s) |
| 11 | `test_store_item_prices` | STORE-4 | review | 59 | killed none of 4 relevant dev bug(s) |
| 12 | `test_buy_power_up_inventory_and_balance` | STORE-5, STORE-6, STORE-10 | review | 59 | killed none of 4 relevant dev bug(s) |
| 13 | `test_buy_character_unlock_and_select` | STORE-5, STORE-7, STORE-10 | accept | 79 | kills SB02 |
| 14 | `test_buy_theme_unlock_and_select` | STORE-5, STORE-7, STORE-10 | reject | 59 | adds no new kill and no new spec ID over 1 suite test(s) |
| 15 | `test_store_unaffordable_items_highlighting` | STORE-8 | review | 59 | killed none of 4 relevant dev bug(s) |
| 16 | `test_store_already_owned_items` | STORE-9 | review | 59 | killed none of 4 relevant dev bug(s) |
| 17 | `test_run_start_and_hud` | RUN-1, RUN-2, RUN-4 | reject | 15 | failed 1 of 1 clean runs (assertion) |
| 18 | `test_pause_and_resume` | RUN-5 | review | 60 | killed none of 3 relevant dev bug(s) |
| 19 | `test_pause_and_quit` | RUN-6 | review | 60 | killed none of 3 relevant dev bug(s) |
| 20 | `test_decline_continue_to_game_over` | RUN-9, RUN-10 | review | 58 | killed none of 4 relevant dev bug(s) |
| 21 | `test_continue_with_premium_success` | RUN-7, RUN-8 | review | 58 | killed none of 3 relevant dev bug(s) |
| 22 | `test_continue_with_premium_insufficient` | RUN-7 | review | 52 | killed none of 1 relevant dev bug(s) |
| 23 | `test_coins_and_premium_added_to_balance` | RUN-11 | reject | 0 | `Run` has no member `premium_state` (not in the SDK manifest) |
| 24 | `test_game_over_navigation` | RUN-12 | review | 56 | killed none of 6 relevant dev bug(s) |
