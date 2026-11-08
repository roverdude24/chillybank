# ChillyBank

Family fork of [Open Net Worth](https://github.com/open-net-worth-ai/open-net-worth).
The fork stays thin so ideas move both ways with little merge pain.

## Branches

| Branch | What it holds |
|---|---|
| `main` | Exact mirror of `upstream/main`. Never commit here. |
| `chilly` | ChillyBank: `main` + every fix branch + family-only files. Run this. |
| `fix/<topic>` | One upstreamable change, branched from `upstream/main`. Open a PR upstream from it. |

## Sync

```bash
git fetch upstream
git checkout main && git merge --ff-only upstream/main && git push origin main
git checkout chilly && git merge main          # take their ideas
git checkout -b fix/<topic> upstream/main       # give ours back: change, push, `gh pr create -R open-net-worth-ai/open-net-worth`
```

When an upstream PR merges, delete the `fix/` branch; the next `git merge main` brings it home.

## Fork-only settings (no code)

Put these in `.env` at the repo root (gitignored):

```bash
APP_NAME=ChillyBank
```

- Base currency: pick **VND** on the first-run screen (it is preselected for Vietnam).
- Settings → Appearance → number format **1.234.567** gives Vietnamese grouping (`1.250.000 ₫`).
- Lucius answers in Vietnamese: tell him once, "Luôn trả lời bằng tiếng Việt", and he saves it to memory.

## Changes carried over upstream

| Branch | Change | Upstream PR |
|---|---|---|
| `fix/egress-provenance` | `fetch_page` runs unattended only for URLs that arrived from outside (search result, page, email). Closes the ledger-exfiltration High in `KNOWN_ISSUES.md`. | not yet |
| `fix/firstrun-brand` | First-run wordmark uses the instance brand name instead of a hard-coded one. | not yet |

## Abstraction obstacles (ideas to send upstream)

Found while building the fork, ranked by what they block.

1. **Permissions are per tool, never per argument.** `fetch_page` is safe for one URL and an exfiltration channel for another, but the policy can only say "always allow" or "ask" for the whole tool. The egress fix had to special-case dispatch. Better: a tool declares `egress="url"` beside `mutates=(...)` and the gate decides per call.
2. **No i18n layer.** 639 components, ~172k lines of TSX with hard-coded English. A Vietnamese UI means touching hundreds of files, and every upstream merge then conflicts. Better: `react-i18next` with `en.json`; a fork adds `vi.json` and never conflicts.
3. **White-label is half done.** `BrandContext` exists, yet ~40 places hard-code "Open Net Worth" (first-run, restore, index.html title). `fix/firstrun-brand` fixes one.
4. **Two permission engines behind a flag.** The legacy gate fails open on errors, `permissions_v2` fails closed. Every security change must reason about both.
5. **One all-powerful API token.** An outside reader (the family TV) can only get the token that can also change the book. Better: a read-only, scope-limited token or an outbound snapshot push.
6. **Setup breaks on paths with an apostrophe.** `make setup` fails in the docs-site build (`virtual:astro-expressive-code/config` parse error) under `VINH's WORKS`. The Python venv and frontend still build.
