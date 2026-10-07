# Feature specs

Intended behaviour of TrashCat, written the way a game designer would. These files are the **input to test generation** (Phase 1): every generated test names the spec IDs it checks.

## Format

- One file per feature. Each statement is one line: `- <ID>: <statement>`.
- IDs are `<PREFIX>-<n>` (for example `STORE-3`). They never change meaning. To retire a statement, mark it `(retired)`; don't reuse its number.
- A statement describes what the player sees or what the game guarantees, and must be testable through the game's UI or state.
- No object names, locators or code. Those belong in `pg_sdk`.
- No bugs. Specs say what *should* happen. The seeded-bug catalog is written later and never appears here.

`[confirm]` marks a statement drafted from the game's code that still needs a look on the phone before it is committed.
