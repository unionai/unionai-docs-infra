# Runbook: adding a new major docs line (SDK-major transition)

When the SDK ships a new major (e.g. flyte **v3**), the docs get a new **primary** line and
the current primary (**v2**) becomes a **secondary** line, exactly like **v1** is today. For
how the system works overall, see [`VERSIONING.md`](./VERSIONING.md).

The framework is line-generic, so this is **additive** — new branch + config + one edge
rule, no redesign. Concretely, moving from `{v2 primary, v1 secondary}` to
`{v3 primary, v2 secondary, v1 secondary}`:

## End state

| Line | Branch | Role | Serves |
|---|---|---|---|
| **v3** | `main` | primary | `/docs/latest` + `/docs/v3` + `/docs/v3.x.y.z` |
| **v2** | `v2` (new) | secondary | `/docs/v2` + `/docs/v2.x.y.z` |
| **v1** | `v1` | secondary | `/docs/v1` + `/docs/v1.x.y.z` |

## Steps

1. **Fork the `v2` branch** off `main` at the cutover commit — this freezes the last v2
   content into its own branch:

   ```bash
   git branch v2 <last-v2-main-commit>
   git push origin v2
   ```

2. **Configure the new `v2` branch as a secondary line** (mirror `v1`). On `v2`:
   - `versions.toml`: set `latest = false`; `stable = "v2.x.y.z"` (the last v2 cut);
     `enumerated` = the v2 pins you want to keep serving.
   - `api-packages.toml [docs_version]`: pin the SDK to the v2 series
     (`sdk_package = "flyte"`, series `v2.`), passenger/backend to their v2 values.
   - CI: the `v2` branch inherits `main`'s workflows, but `main`'s are the *primary*
     shape. Align `v2`'s to the secondary shape used by `v1`:
     - `build-and-deploy.yml`: trigger on `push: [v2]` (not `main`); production-only
       (no `pull_request`).
     - `build-pr.yml` + `deploy-pr-preview.yml`: present (the PR-preview split).
     - `regen-api-docs.yml`: `--ref v2`-triggered (manual); `base: v2`, its own PR
       branch name, and the v2 SDK PyPI link.
     (This is the same CI-sync we did for `v1` — see git history / DOC-1245.)

3. **Make `main` the new primary (v3).** On `main`:
   - Regenerate the API reference against flyte 3.x.
   - `api-packages.toml [docs_version]`: series `v3.`.
   - `versions.toml`: `latest = true` (default); `stable = "v3.x.y.z"` (the first v3 cut).

4. **Update the cross-line registry** — `served-versions.toml` (this repo):
   - Add a `[v3]` table with `latest = true`, `stable = "v3.x.y.z"`, `enumerated = []`.
   - Set `[v2].latest = false`.
   - The selector line order and latest-ownership are **derived** from this file
     (`run_hugo.sh` sorts the `[vN]` tables newest-first and gives `/docs/latest` to the
     line with `latest = true`), so **no `run_hugo.sh` code change is needed** — just this
     config.

5. **Edge routing (eng / Terraform — the one non-repo step).** Today `/docs/v2` is served
   by `main`'s deployment. Once `main` is v3, add a CloudFront behavior so:
   - `/docs/v2*` → the **new v2** deployment.
   - `/docs/v3*` and `/docs/latest` → `main`'s deployment (they follow `main`
     automatically; `/docs/*` already routes there).
   - `/docs/v1*` → the v1 deployment (unchanged).
   This is the only piece that cannot be pre-staged (the v2 deployment doesn't exist until
   step 1). Update the `/docs/stable` redirect if you want it to keep pointing at the
   primary (`→ /docs/v3`).

6. **Verify** on the live site (and a preview first):
   - `/docs/v3` = v3 stable (indexed); `/docs/latest` = v3 `main` tip (noindex).
   - `/docs/v2` = v2 stable, now served from the **new v2 deployment**.
   - `/docs/v1` unchanged.
   - The selector shows **v3 / v2 / v1**, with `LATEST` + `STABLE` badges on v3 and bare
     numbers on v2/v1. Cross-line nav works in every direction.
   - Footers stamp the right components per line.

## Notes

- **Index policy.** Decide whether the now-secondary v2 stays indexed or goes `noindex`
  to concentrate search on v3 (same lever as DOC-1291 for v1).
- **File budgets.** Each line is its own Cloudflare Pages deployment (own 20k budget), so
  three lines don't compete on the ceiling.
- **No framework changes.** The version scheme, cut/regen/fold, inline-tag machinery, and
  selector/footer are all line-generic. The only *code* touched is config
  (`served-versions.toml`, the two branches' `versions.toml` + `api-packages.toml`) plus
  the branch's CI shape and the one CloudFront behavior.
- **Retiring the oldest line.** If you ever stop serving `v1`, drop its `[v1]` table from
  `served-versions.toml`, remove its CloudFront behavior, and archive the branch. The
  tags remain in git.
