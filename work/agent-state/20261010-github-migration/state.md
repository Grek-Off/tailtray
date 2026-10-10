# Public GitHub migration — 2026-10-10

## Authority and boundaries

User explicitly authorizes public GitHub TailTray with history/tags, complete
project presentation/contribution docs/license and CI/release migration. Human
maintainer: Владимир Греков (Vladimir Grekov), GitHub Grek-Off, public contact
grekov.vs@gmail.com. Target inferred from existing project name: Grek-Off/tailtray.
User wants agent to create the repository, not a manual prerequisite. Token now
stored in the scoped desktop Secret Service entry and authenticated as Grek-Off.
Do not expose credentials, rotate keys, rewrite existing
published history or delete the working GitLab update endpoint.

Base HEAD7dd9c7f clean; successful GitLabv0.1.5 source/tag34b9655, pipeline
2930998441. Keep MIT license, existing signing key and old update URL until new
GitHub workflow/public apt-dnf consumers are verified. No user-host VPN changes.

## Owners

- Main: credential helper, README/CONTRIBUTORS/LICENSE/project metadata, security
  policy proposal, CI/release integration, repository settings/publication/journal.
- Popper01a12527-c1db-71b0-9cda-7eb923e3cb88: CONTRIBUTING.md, CODE_OF_CONDUCT.md,
  .github/ISSUE_TEMPLATE/*, .github/PULL_REQUEST_TEMPLATE.md only; no external writes.
- Avicenna01a12527-c23f-70e2-a9fc-b91542521c42: read-only bounded tree/history
  publication-readiness audit. Never echo secret matches or operate credentials.
- Agent model inherited per runtime instruction; default role, actual unknown.

## Current evidence / decisions

- MIT already in LICENSE/TOML/spec; retain terms and contributor attribution.
- Reachable Git author names include Vladimir Grekov and Codex. Document Codex
  as OpenAI AI-assisted development, not a human/account/copyright owner.
- Knowledge MCP unavailable; CLI roots points to unavailable Windows corpus.
  Use current Git/source/journal, not absence-of-knowledge assumptions.
- secret-tool installed. Sandboxed user D-Bus check denied; authorized read-only
  elevated check confirmed org.freedesktop.secrets owned by KDE ksecretd. This
  does not yet prove storage/unlocked wallet; synthetic own-entry probe planned.
- gh CLI absent; prefer small stdlib/keyring helpers, no plaintext gh fallback.
- Public publishing still blocked by missing scoped GitHub access; independent
  local preparation continues. No GitLab visibility/settings/endpoint changes.

## Next

Provide exact token permissions/bootstrap narrowing and hidden-input storage
command; finish/review bounded docs/history branches; verify local helper with
synthetic credentials. Keep security-policy scope/exclusions explicit. Do not
claim GitHub migration or CI completed before actual scoped access and proof.

## Checkpoint after interruption / user access check

- Contribution docs accepted at de84ee3 (agent committed its own allowed files).
- Original bounded history audit: base7dd9c7f, 48 commits/633 reachable objects;
  all original refs/tags51 commits/651 objects. No high-confidence credential or
  private-key matches reported. This is not an exhaustive security audit.
  Preserve main and v0.1.0..v0.1.5, not mirror all local refs/dangling objects.
- Human public email and attribution were supplied by the owner. Public update
  infrastructure is already part of approved release documentation.
- Read/write/readback/clear on a uniquely scoped synthetic Secret Service entry
  succeeded. Only that synthetic entry was removed; real credentials untouched.
- GitHub connector profile is Grek-Off. After user said access was configured,
  scoped local PAT check returned absent and connector GET Grek-Off/tailtray
  returned404 (missing or not accessible; do not claim definitive nonexistence).
  Connector has no repository creation/admin mutation endpoint. PAT still needed.
- Main owns pending README/CONTRIBUTORS/LICENSE/TOML, keyring helper +8 tests,
  GitHub CI +7 contract tests. CI preserves GitLab, uses read-only permissions,
  immutable official actions, isolated ephemeral signing rehearsal, all4 targets.
- Hooke01a12532-6c5a-7292-bc6c-90594e0760ae created CI draft; after interruption
  runtime reports agent not found. Main reviewed/took over: install Git before
  checkout, Bash shell explicit, flat package artifacts separate from reports,
  tag/version guard, strict checks inspected in shared runtime script, tooling
  tests only in verify (not installed-app runtime). No remaining active agents.
- Official API refs verified2026-10-10: checkoutv7.0.1
  3d3c42e5aac5ba805825da76410c181273ba90b1; upload-artifactv7.0.2
  cf430e030ddbb5b0abf93d22962f4752f3646cd9; download-artifactv8.0.2
  9000827ccba6bdab643e8b6fd33ac0654aef8333; Node24 runner on hostedUbuntu24.04.
- Targeted15 tests and12 subtests PASS, Ruff whole source/tests/scripts PASS,
  make static-check PASS, diff whitespace PASS. Full native Xvfb invocation could
  not start: xvfb-run absent on user host. No host packages/services changed;
  do not claim a new strict native/full GitHub pipeline PASS.
- Root SECURITY.md inventory empty; exact proposed text shown in async approval
  question. Owner has not approved it; do not write/commit it silently. It adds
  private reporting, best-effort latest-release fixes, concrete boundaries and
  expectations, no blanket exclusions or accepted-risk suppressions.
- Repository/publication/release migration remains incomplete: token, policy
  approval, actual GitHub settings/push/pipeline/release/anonymous consumers.
- Bounded full local developer pytest:191 passed,3 native tests skipped,
  184 subtests passed,14.25s. No newly verified strict native CI run claimed.
  Package Python build unavailable locally (venv lacks build module); TOML
  metadata parsed/validated. GitHub/GitLab verify installs .[dev] including build.
  Tooling checks and CI configuration are prepared; production GitHub publishing
  is deliberately not wired to an unprotected/unconfigured signing environment.

## Verified checkpoint 2026-10-10: live GitHub migration

- Public repository https://github.com/Grek-Off/tailtray, ID1413035535;
  owner24355001. Reviewed9e8dc61 history and v0.1.0..v0.1.5 pushed normally.
- Scoped PAT exists in Secret Service; API identity verified. Tell owner to
  narrow temporary All repositories selection to tailtray; no new token needed.
- Approved exact SECURITY.md is now authored. Private vulnerability reporting,
  secret scanning/push protection enabled. Read-only default Actions, selected
  GitHub-owned actions only. Active release-tag creation/admin and immutable
  update/deletion rules. Environments release/github-pages restricted to tags
  v*.*.*. Existing RSA3072 signing key copied to release environment secret,
  fingerprint D432326D1167240864F8345763FA04F99681DA62; public export byte-equal
  tracked key. No rotation or repo-wide secret. Pages build_type workflow enabled.
- Git protocol defects reproduced and fixed: blank-line termination avoids
  read-to-EOF deadlock; repeated wwwauth[]/capability[] fields are legitimate.
  GUI askpass disabled only in migration subprocess. Regressions added.
- First live CI38044845057: verify SUCCESS; source failed dubious ownership in
  root container. Exact-workspace safe.directory env fix authored; not yet live
  proven. Native/package/repo jobs correctly skipped, no false success.
- Original GitLab v0.1.5 transferred without rebuilding/signing: ten original
  assets, eleventh signed history archive with14payloads. Actual GPG checks,
  tagged source equality, GitHub digest/size closure, safe rerun and fetch_previous
  restoration PASS. Draft lookup needed authenticated listing fallback: by-tag
  endpoint returned404 for existing draft. Regression added. GitLab untouched.
- Confucius and Mill backend agents completed and CLOSED. Parent accepted/reviewed
  files, further fixed refreshed upload acknowledgement and retry finalization.
  No active writers. Parent owns all current pending files/integration.
- Production workflow authored: read-only reusable CI -> protected signing/draft
  ->4actual signed native consumers ->Pages ->4anonymous HTTPS consumers ->
  release finalization with separate signed dist/public histories. v0.1.6 metadata
  bumped; not tagged/released yet. New files require commit and live pipeline.
- Full local developer suite222PASS,3nativeSKIP,184subtestsPASS,11.62s;
  Ruff/static/diff whitespace PASS. Native suite is mandatory/no-skip in CI;
  no strict local native PASS claimed (xvfb-run absent). Future GitHub release
  immutability enabled and verified by API, existing release unchanged.
- Next: commit verified integration, normal main push/CI; fix evidenced failures;
  tag newv0.1.6 only after CI, prove release+anonymous consumers, protect main,
  update verified docs/journal and commit. Never overwrite old tags/assets.

## Native CI proof before first GitHub tag

- ecf4c54 published, GitHub CI38045306221 ALL12jobsSUCCESS: verify/source,
  native RPM+installed runtime Fedora43/44, DEB+runtime Debian13/Ubuntu26.04,
  real signing rehearsal and4native repository consumers, CI required gate.
  Exact-workspace Git safe.directory fix is now live proven.
- origin now GitHub HTTPS, old GitLab remote preserved as gitlab; main tracks
  origin/main. Repository-local credential helper references scoped Secret
  Service reader only (no token in config); normal Git uses the same safe wallet.
- Parent added gate-checked protect-main tool +regression; focused31PASS/Ruff
  PASS. Release/docs explain separate flat dist versus history-relative sums,
  opt-in oldrepo switch, key preservation, protected job gates and retry rules.
- Next commit metadata/docs/protection tooling, new annotated tagv0.1.6, run
  Release production pipeline. Do not claim new Pages verified before all4live
  consumers and finalization succeed. Agents closed; no background writers.

## Production run dispatched

-3e310bb committed/pushed normally and annotated v0.1.6 created/pushed without
  moving any existing tag. Main CI38045667426 and Release38045667801 running.
- protect-main applied after real successful all-jobs gate. PR status context
  CI required from github-actions, strict/up-to-date; required reviewer count0
  for solo maintainer. Admin normal-update exception intentional. Separate
  active no-bypass ruleset forbids main deletion/non-fast-forward history rewrite.
- Pending: actual signing/draft, four signed repository checks, Pages deployment,
  four anonymous HTTP consumers and final11asset immutable release. No production
  PASS claimed yet. New docs version at release tag describes architecture;
  docs/github-migration pending verification paragraph must be updated afterproof.

## Production verification COMPLETE

- Release38045667801 SUCCESS, ALL23jobsSUCCESS. Main CI38045667426 SUCCESS.
  Exact source3e310bb02a851a5f769672ea4e0571e48f8ba6d9; immutable Release ID408879752
  https://github.com/Grek-Off/tailtray/releases/tag/v0.1.6, draftfalse/immutabletrue.
- Real existing signing key,4actual signed native repository consumers, Pages,
 4anonymous native HTTPS consumers and finalize allSUCCESS. 11releaseassets.
- Independent credential-free verify-github-release.py PASS: downloaded every
  GitHub asset plus all21signed Pages payloads, verified pinned key/GPG signatures,
  exact gitarchive source, remote/localtagidentity, restored v0.1.6 history.
  Versions0.1.4/0.1.5/0.1.6 preserved. No VPN/host package changes.
- Added portable read-only verifier and3tests; separate installed runtime tooling
  ignore added. Parent final docs/proof/release-notes changes pendingcommit+CI.
- Next: publish final documentation/tooling commit, confirm latest main CI,
  preserve oldGitLab endpoint availability, final concise links/reminderPATscope.
