# PII in git history — found and remediated, 12 Aug 2026

## What happened

While auditing the repo ahead of publishing it (to let the shared Claude Project
clone it and run builds on demand), we found that raw Matrixify order exports had
been committed to git instead of staying gitignored:

- `trading/source/orders_{2025,2026}-{04,05,06}_{UK,US}.csv` (12 files) — full
  Shopify order exports: customer name, email, phone, billing/shipping address,
  browser IP, and card-adjacent fields (CC BIN, AVS/CVV result codes).
- `reviews/yotpo_sample.csv` and `fixtures/yotpo_export_sample.csv` — real
  reviewer names and emails.
- `docs/2026-07_order_names_sheet.numbers` and
  `trading/tests/fixtures/2026-07_order_names_sheet.csv` — lower risk (order
  numbers, not names, on inspection of the CSV twin) but removed for hygiene
  since neither is used by the live pipeline (only a defunct one-off diagnostic,
  `trading/order_scope_diff.py`, referenced the CSV).

**Root cause:** `.gitignore` had `/source/` — anchored to the repo root — so it
never matched the nested `trading/source/` directory where these actually live.
The intent ("raw feeds are snapshotted locally, never committed") was always
correct; the pattern silently didn't implement it.

**Exposure at time of discovery:** the GitHub remote (`plank-lena/plank-data`)
was private, not public. Caught before any public push happened.

## What was done

1. Rewrote all git history with `git-filter-repo`, removing the paths above from
   every commit and every branch (not just deleting them going forward — a plain
   delete-and-commit would leave them recoverable from old commits).
2. Verified the scrub by hashing and grepping every blob that has ever existed in
   the repo's history (730 objects) for email patterns — zero remaining hits
   except one internal `@plankhardware.com` team email in a spec doc (not
   customer data, left as-is).
3. Fixed `.gitignore`: `/source/` → `source/` (unanchored, matches at any depth),
   plus explicit ignores for export-sample and raw order-export filenames as a
   second line of defense, plus `.claude/` (local Claude Code settings —
   `settings.local.json` was carrying live Matrixify export access tokens in
   plaintext; it was never committed, but nothing was stopping it from being).

## What you need to do

- This cleaned history is a **rewrite**, not a patch. If you have any existing
  local clone or worktree of this repo (including `.claude/worktrees/...`), it
  now has a different, incompatible history from this bundle — don't merge them.
  Replace your local repo entirely and force-push. See the message that shipped
  alongside the bundle for exact commands.
- If `plank-lena/plank-data` has ever been forked, or any commit SHA from before
  this rewrite was shared/cached outside your own clones (a PR link, a raw
  githubusercontent URL, a CI cache), those copies still have the original data
  and a force-push won't touch them — worth a quick check.
- Consider whether the Matrixify export links previously sitting in
  `.claude/settings.local.json` should be treated as no-longer-valid; they look
  job-scoped and likely short-lived, but you're better placed to judge that than
  I am.
