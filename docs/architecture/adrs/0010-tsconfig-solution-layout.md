# ADR-0010 — TypeScript root tsconfig is a solution config; shared options live in tsconfig.base.json

> **Status:** accepted
> **Date:** 2026-05-18
> **Related plan(s):** —
> **Related ADRs:** [ADR-0008](0008-electron-shell-conventions.md) (partially supersedes its "TypeScript configuration" section)

## Context

[ADR-0008](0008-electron-shell-conventions.md) settled the four-tsconfig split — base + renderer + main + preload — because main and preload need `module: CommonJS` while renderer needs `ESNext`. That decision is still correct. The setup it described, however, makes the root `desktop/tsconfig.json` do two incompatible jobs at once: it is both the shared-options carrier (extended by the three sub-configs) **and** the file that the IDE's TypeScript language service falls back to whenever it cannot place an opened file into a more specific project. Those two roles want opposite content.

The shared-options role wants the base to contain `compilerOptions` and nothing else — no `include`, no `jsx`, no `module`. The IDE-fallback role wants the base to contain settings broad enough to typecheck any file in `desktop/` without lying — including `jsx: react-jsx` and `module: ESNext`, which conflict with the main/preload setting and which we therefore cannot put in the base. The result under ADR-0008's layout is that the IDE opens any `.tsx` file, walks up the tree looking for `tsconfig.json` (literally that filename — sub-configs named `tsconfig.renderer.json` are not auto-discovered), finds the base, and reports a flood of phantom errors: 30+ `TS17004 (Cannot use JSX unless the '--jsx' flag is provided)`, `TS1343 (import.meta only allowed when module is es2020+)` on every test, and `TS2307 (Cannot find module './*.module.css')` because the renderer's `vite/client` types are not loaded.

Meanwhile `pnpm typecheck` runs the three per-target configs and passes. Pre-commit's `tsc --noEmit --project tsconfig.X.json` chain stays silent. The IDE shouts; the CLI does not; contributors learn to ignore both. That divergence — IDE seeing errors the CLI does not — is the failure mode this ADR closes.

A second, smaller forcing function: the ADR-0008 typecheck script covered only renderer/main/preload. `tsconfig.test.json` (Jest) and a new e2e config were never typechecked. Type errors in test code slipped past pre-commit silently for the same surface-level reason — no one had wired them into the script.

A third: `baseUrl` is deprecated in TypeScript 6.0 and will stop functioning in 7.0. The ADR-0008 base config used `baseUrl: "."` to anchor the `@/*` and `@shared/*` paths.

## Decision

We restructure `desktop/`'s TypeScript configuration so that **the root `tsconfig.json` is a solution config**, never used directly for compilation. Shared compiler options move to `tsconfig.base.json`. Every per-target config extends `tsconfig.base.json` and is listed under `references` in the root. The IDE's language service, given a solution-style root, walks the `references` to find which sub-config includes a given file and uses that sub-config's settings — phantom errors disappear.

Concretely:

```
desktop/
├── tsconfig.json           # solution: `files: []`, `references: [renderer, main, preload, test, e2e]`. No compilerOptions.
├── tsconfig.base.json      # shared compilerOptions (target, strict, paths, etc.). No `include`/`files`. No `jsx`/`module`.
├── tsconfig.renderer.json  # extends ./tsconfig.base.json; module ESNext, jsx react-jsx, types [vite/client]
├── tsconfig.main.json      # extends ./tsconfig.base.json; module CommonJS, types [node]
├── tsconfig.preload.json   # extends ./tsconfig.base.json; module CommonJS, types [node]
├── tsconfig.test.json      # extends ./tsconfig.base.json; module CommonJS, jsx react-jsx, types [jest, node]
└── tsconfig.e2e.json       # extends ./tsconfig.base.json; module ESNext, types [node, @playwright/test]
```

The Playwright spec config is split out from the Jest config because Playwright specs use `import.meta.url` (requires `module` ≥ ES2020) and load `@playwright/test` types, while Jest unit tests need `module: CommonJS` and `jest` types. A single `tsconfig.test.json` cannot satisfy both — the previous setup silently excluded the Playwright specs to dodge the conflict, which removed them from typechecking entirely.

`baseUrl` is dropped from `tsconfig.base.json`. The `paths` entries become directory-relative (`./renderer/*`, `./shared/*`), which is how modern TypeScript resolves them when no `baseUrl` is set. This removes the TS 7.0 deprecation warning the IDE was showing on every config.

The `typecheck` script in `desktop/package.json` chains all five `--noEmit` invocations (renderer, main, preload, test, e2e). Pre-commit's typecheck now covers test and Playwright code. **Any new sub-config — by anyone, in any future plan — must be added both to `tsconfig.json`'s `references` array (IDE) and to the `typecheck` script (CLI).** Keeping these in sync is the durable obligation this ADR creates.

ADR-0008's "TypeScript configuration" section is **partially superseded** by this ADR: the per-target shapes it described remain correct, but the role of the root `tsconfig.json` (was: extended by sub-configs) and the existence of a separate `tsconfig.base.json` are the new structure. The rest of ADR-0008 — build pipeline, IPC discipline, security defaults, packaging — is unaffected.

## Consequences

### Positive

- **IDE and CLI agree.** Phantom `TS17004` / `TS1343` / `TS2307` errors stop appearing in the editor. Whatever the IDE shows is what `pnpm typecheck` will catch — divergence between the two is no longer the default state.
- **Test and e2e code is actually typechecked.** Pre-commit catches type errors in `*.test.ts` and `tests/*.spec.ts`, not just production code.
- **Each sub-config is independent.** Adding a future config (e.g. a Storybook one, a CLI tool) is a local change: write the config, add one line to `references`, add one line to the `typecheck` script. No cross-config edits.
- **The base config is honest about its role.** `tsconfig.base.json` is options-only, never used for compilation, never opened by the IDE. Its contents cannot accidentally leak into the IDE-fallback role because there is no fallback anymore.
- **`baseUrl` removal future-proofs us.** TypeScript 7.0's removal of `baseUrl` is a non-event for this repo.

### Negative

- **Five configs instead of four.** One more file to maintain, one more line in `references`, one more `tsc` invocation in the typecheck script. The added e2e config is the only structural growth; the rest is a relabeling.
- **A new convention to remember.** "When you add a sub-config, register it in two places." Easy to forget. Caught by pre-commit if the new config has type errors, but not caught at all if the new config is *forgotten* in the references array (the IDE will silently fall back to picking nothing and the file will be unowned). We accept this; the alternative is `tsc -b`-style enforcement which carries its own costs (see Alternative B).
- **Slightly slower typecheck.** Five sequential `tsc` invocations instead of three. Real-world impact is small (each invocation is a few seconds with `skipLibCheck`) and only matters in CI / pre-commit, not in the IDE.

### Neutral

- The path aliases (`@/*`, `@shared/*`) work identically. Vite's `resolve.alias` config in `vite.config.ts` is unchanged — it has always been independent of tsconfig.
- The four-config split in ADR-0008 is preserved in shape; only the root's role and the existence of a separate base file change.

## Alternatives considered

### Alternative A — Keep ADR-0008's structure; silence IDE warnings by adding `jsx`/`module` to the base

Add `jsx: react-jsx` and `module: ESNext` to `desktop/tsconfig.json` so the IDE-fallback role stops complaining. Rejected because (1) it lies about main/preload's actual module system — sub-configs override the base to `CommonJS`, which means the IDE sees one set of rules and the CLI another for those two processes; (2) it does not solve the `vite/client` types problem (the IDE still cannot find `*.module.css` declarations) without also adding `types: ["vite/client"]` to the base, which is wrong for main/preload; (3) it leaves test and Playwright code unowned by any IDE path. This option treats the symptom (IDE warnings) without addressing the cause (one file doing two incompatible jobs).

### Alternative B — Use `composite: true` + `tsc -b` (TypeScript project references in build mode)

The "official" project-references workflow: mark every sub-config `composite: true`, replace the typecheck script with `tsc -b`. Rejected because composite projects must emit declarations (or use `noEmit` with explicit opt-in flags) and require an incremental cache file, which complicates the gitignore and the build. The IDE's language service uses `references` for project discovery whether or not the referenced projects are composite — we get the IDE benefit without the build-mode tax. If we later want incremental builds for CI speed, we can adopt `tsc -b` then; the migration is a one-line change per sub-config.

### Alternative C — Single `tsconfig.json` per process directory (e.g. `desktop/electron/tsconfig.json`)

Move each config into the directory it owns, so the IDE's "walk up looking for tsconfig.json" lookup naturally lands on the right config. Rejected because (1) the renderer, shared, and tests directories overlap — a `.test.ts` under `shared/` is loaded into the Jest config, not the shared one — so directory-anchored configs do not cleanly partition the file tree; (2) it scatters config across the workspace, making "show me the build setup" a multi-file search; (3) it does not address the typecheck-script coverage gap. Solution-style references give the same IDE benefit without moving files.

## Notes

- The `references` field requires `composite: true` on the referenced projects **only when invoked via `tsc -b`** (build mode). The language service and `tsc --noEmit --project X` ignore this requirement. We rely on that — see Alternative B.
- Pre-commit was independently broken on at least one developer machine by a placeholder `allowBuilds:` block in `pnpm-workspace.yaml` that caused pnpm 11 to abort with `ERR_PNPM_IGNORED_BUILDS` before lint-staged or typecheck could run. That is a separate fix (move the allowlist to `onlyBuiltDependencies` in the workspace yaml; drop the duplicate from root `package.json`) shipped in the same commit as this ADR but unrelated to the tsconfig decision.
- If the IDE language service ever stops respecting `references` for non-composite projects (an upstream regression), the fallback is to add `composite: true` to each sub-config and switch the `typecheck` script to `tsc -b --noEmit`. No structural change to the file layout is required.
