# Test review — 2026-10-09

## Scope and authority

Review redundant/weak tests, improve real regressions, wire CI. Commit changes
and verify the private GitLab pipeline. Prior release automation remains in
scope; no public package/source/Pages publication permission. Preserve tags.
Do not operate the live VPN or rotate/read the release signing secret.

## Current evidence / incident

- User reported kernel OOMs at13:06:12/13:17:45 Moscow in the new pagination test.
  Code confirmed `"page=1" in path` matched `per_page=100` forever.
- Fixed exact `urlsplit`/`parse_qs`, six-request fixture budget, and tested a
  deliberately non-advancing page fails on request7. Download-size test uses
  a patched64-byte limit instead of allocating128MiB.
- Release/repository/deploy subset36 PASS under512MiB address-space cap and
  timeout30s, runtime0.222s. At256MiB the host's SSL imports cannot initialize;
  this limit is not suitable even for pure release tests.
- Other interrupted drafts: deploy mock contexts were already exited before
  invoking main; fixed lifetime/no global cwd changes. "All preflight writes
  forbidden" asserted an unsupported transaction: replaced with immutable
  conflict/no-completion-marker invariant, without altering production.
- Parent owns CI, runner, packaging tests, docs and rehearsal integration.
- Worker01a12036-00aa-7850-a791-c0d373effccb finished and closed. Removed AST
  assertion; parent added actual QSystemTrayIcon signal runtime regression.
  Subprocess probes use installed code when package-testing, not checkout.
- Commit871c11e pushed: OOM fix, bounded release/deploy fixture tests36 PASS.
- Parent reviewed/fixed rehearsal: test key capabilities parsed, isolated
  GNUPGHOME propagated to verifier, synthetic public key imported only inside
  explicitly disposable CI, no duplicate production upload implementation,
  transport request budget128, per-command timeout120s, agent killed in finally.
- New pytest CI policy itself tested: skipping a native test must yield both
  JUnit failure AND nonzero exit; report rewriting alone did not enforce exit.
- Latest local full pytest169 PASS/3 native skips under2GiB,16.28s;70% aggregate
  branch coverage. CLI89%,controller86%,menu93%. Added synthetic SecretService
  tests; no actual keyring calls. All Ruff/static checks passed.
- New CI runs installed package runtime on4 native distros plus signed-repo
  rehearsal/4 consumers on branches before protected-tag publication. Need
  actual new CI proof; local report/older green pipeline are not that proof.

## Next

Finish useful test reductions/runtime checks, bounded test runner/JUnit reports,
secretless signed-repo rehearsal and CI gates. Verify exact pushed SHA green;
only then protected v0.1.2 release (previous mainc748b1e was green).
Public Pages remains off. Installed UI module update was completed last turn;
live menu verification was interrupted and must not be claimed as confirmed.
