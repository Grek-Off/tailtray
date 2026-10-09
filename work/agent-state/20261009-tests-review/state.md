# Test review — 2026-10-09

## Scope and authority

Review redundant/weak tests, improve real regressions, wire CI. Commit changes
and verify the private GitLab pipeline. Prior release automation remains in
scope. User explicitly authorized public package/source/Pages distribution on
2026-10-09 after the previous turn. Keep Git and Generic Registry private;
only the signed package/source/index tree may become public. Preserve tags.
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
- Commit2b59818 pushed; GitLab CI lint valid. Pipeline2930262044 verify/source
  and both Fedora runtime jobs PASS. Debian/Ubuntu smoke failed on PNG byte
  equality after dh_strip_nondeterminism; compare decoded RGBA pixels and size
  instead. Keep the visual integrity assertion, not the encoding assumption.
- Commita274d1f pipeline2930304597 fully PASS:11 jobs,689 JUnit checks,0 skips.
  Protected tagv0.1.2 points exactly there. Tag pipeline2930383974 passed all
  tests/builds/rehearsals; actual release failed HTTP500 on first binary upload.
  Retried publication job17059264717 failed identically; no GitLab Release.
- Real urllib preprocessing regression reproduces binary PUT being silently
  labeled application/x-www-form-urlencoded. Fix explicit octet-stream; JSON
  remains JSON and GET has no body content type. No real HTTP/socket/credential
  in this regression. Need actual corrected publication proof, not a mock PASS.
- Preserve tagv0.1.2; next releasev0.1.3. Sync package/AppStream/public Python
  versions and assert the public version too (it was stale0.1.0).
- Commitf7bf27b pushed;45 release/repository/packaging tests PASS under512MiB.
  Found a second CI policy gap: module-level/importorskip collection skip was
  hidden by passing sibling tests (reproduced exit0). Handle collect reports
  too and assert nonzero exit/JUnit error/no skipped record in a tiny fixture.

## Next

- HEAD25821de pipeline2930435645 PASS:11 jobs,695 JUnit checks,0 skips; source
  suite175 tests/163 subtests and74% coverage. Tagv0.1.3 points exactly there.
- Tag pipeline2930458673 passed11 verify/build/rehearsal jobs but release
  job17059600400 failed HTTP500 at the same nested DEB endpoint even after
  octet-stream fix. That fixed a transport defect, not the whole root cause.
- Pages currently private, no deployments. API-provided URL:
  https://tailtray-657378.gitlab.io. No guessed domain, no public endpoint yet.
- Parent owns all writes/credentials/GitLab settings. Read-only agentMeitner
  01a120e6-78f2-7c60-af7f-cd18680d54e4 completed/closed. Public output controlled;
  current native checks only file://, so real anonymous HTTPS apt/dnf check
  remains required.14 scoped repository/deploy tests passed in that review.

- Bounded real API probe: plain file, nested path without plus, and flat plus
  filename returned201. Nested plus filename (encoded or literal) returned500.
  Synthetic package71986892/20261009-2df3225b5145 contained exactly3 files/105
  bytes; verified metadata and removed only that owned probe after diagnosis.
- Implemented signed flat-v1 Registry transport names; native paths unchanged.
  Legacy signed history still supported. Version0.1.4 synchronized, old tags
  untouched. Added roundtrip/idempotence/path-collision and layout regressions.
- Native consumer script now refuses non-CI execution; added four anonymous
  HTTPS consumer jobs after Pages. Key/signature/tag readiness + native package
  downloads; apt update errors fail closed. Pages expiry explicitly never.
- Local bounded subset51 PASS/28 subtests, Ruff/bash syntax/diff checks PASS.
  Authenticated GitLab CI lint valid, no errors/warnings. Public settings and
  two publishing variables not yet changed (both specific variables absent).

- Commit6411868 pushed, exact branch pipeline2930851881 currently building.
- Authorized Pages setting changed onlypages_access_level=public. ProjectGit
  visibility remainsprivate, Registry accessunchanged, Pagesforce_https=true.
  Created protected/raw/env_var TAILTRAY_PUBLISH_REPOSITORY=1 and
  TAILTRAY_REPOSITORY_URL=https://tailtray-657378.gitlab.io; no secret key reads.
- Read-only Epicurus01a120f6-4d4a-7032-ac65-d27992cb185e completed/closed:
  pinning first fingerprint alone allowed bundled extra primary keys; readiness
  budget too short for Pages. Shared helper now rejects extra primary keys,
  consumer checks actual VALIDSIG signer, installer requires HTTPS redirects,
  retry budget24 with outer240-second timeout. Added safe fake-GPG regression.

- Commitbecbb5137a4563da4b541d2205f92be8694d9519 branch pipeline2930882385 fully
  PASS:11 jobs,707 JUnit checks,0 failures/0 skips. Four native builds/runtime
  suites + actual signed repository rehearsal/history + four apt/dnf consumers.
- Actual committed public key also passes strict single-primary pin in an
  isolated temporary GNUPGHOME on the host (no user trust/keyring changes).

- Tagv0.1.4 exactbecbb51 pipeline2930917781:11 verification/rehearsal jobs PASS.
  Actual repository + all10 immutable release files uploaded successfully;500
  resolved. POST/releases returned400. Pages/remaining9 jobs skipped.
- Primary GitLab app/models/releases/link.rb FILEPATH_REGEX excludesplus in
  optionaldirect_asset_path, unlike Generic Registry filenames. Added contract
  assertion; reproduced failure on existingDEB alias. Remove optional aliases,
  retain original linknames and immutable URLs; version0.1.5 synchronized.
  Preserve tagv0.1.4 and its signed completed Registry history; do not rewrite.
- CI currently uses shared GitLab runners (as existingREADME/CI state says);
  the older requestedVM setup remains unresolved due to missingSSH target.

Next: bounded regression/static/lint, push and run newprotectedv0.1.5 fulltag
pipeline (all prepublication gates remain mandatory). Verify actual release,
Pages/native anonymous HTTPS consumers and source integrity/historyv0.1.4.
Installed UI module update was completed last turn;
live menu verification was interrupted and must not be claimed as confirmed.
