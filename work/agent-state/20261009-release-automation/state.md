# Release automation — 2026-10-09

## Contract

Build and publish GitLab releases with native packages for multiple distros and
source tar.gz; enable signed dnf/apt update repository where authorized. Commit
all project changes. Existing project 86100130 / grekov-labs/tailtray is private.
No permission to make private packages/source publicly accessible yet; question
sent asynchronously, unanswered. Do not infer public access from this checkpoint.

## Decisions / ownership

- Fedora 43/44, Debian 13, Ubuntu 26.04: native matrix, noarch/all, smoke install
  with official Tailscale repository, no live host VPN changes.
- Generic registry stores immutable release files using CI_JOB_TOKEN; signed
  static apt/rpm indices are generated for Pages. Public Pages activation gated
  by protected TAILTRAY_PUBLISH_REPOSITORY=1 and explicit privacy authorization.
- Prior versions retrieved through authenticated Packages API, signed manifest
  checked against pinned fingerprint, payload hashes checked before merge.
- Release and Pages operations serialized; stale Pages deployment must fail.
- Signing key created in tmpfs, backup in Secret Service only. Protected hidden
  base64 file variable TAILTRAY_SIGNING_KEY and protected public fingerprint
  configured; protected v* tags (Maintainer). No private material in source/logs.
- Public fingerprint D432326D1167240864F8345763FA04F99681DA62.
- All packaging/Qt workers finished and closed; main owns integration.
- HTTP400 worker 01a11ffb-da17-7c31-a40e-36af84ec35ce finished/closed;
  inspected patch in gitlab_release.py + test_release.py. Main confirmed actual
  GET encoded nested path = 400, slash-preserving nested path = 404 (missing).
- Qt worker finished/closed: QMenu wrappers retained, action order compared by
  stable objectName instead of Python is. Existing CI was red with deleted
  QAction on Qt6.10. Targeted cases x50 and suite passed worker reproduction.

## Current evidence (not final completion)

- Starting HEAD e501b52, clean before this task. Integration HEAD7cf23dc pushed
  main; pipeline2929730269 succeeded on all six jobs, all four native targets
  built and installed successfully. Protected v0.1.0/v0.1.1 tags preserved;
  tag pipelines failed publication, no successful Release yet.
- v0.1.0 failed missing fetch-previous CLI fingerprint default; fixed in2c89499.
- v0.1.1 pipeline2929759725 release17054468524 failed Generic nested path GET400.
  Fix preserves literal slashes and percent-encodes individual segments.
- Pending next release version0.1.2 synced across TOML/spec/Debian/AppStream.
- Latest local suite158 tests passed,3 native desktop skips; full native
  verification is provided by actual GitLab verify job. Temp fixtures isolate
  package tests from real dist artifacts.
- Signing CI variables metadata verified protected, signing key masked/hidden.
- No active workers/build processes. Need commit/push current patch, wait exact
  main SHA CI success, tagv0.1.2, verify release publication plus four signed
  native repository consumers. Do not tag before exact latest main is green.
- New screenshot issue: Health warning falsely invalidated Running and explicit
  ExitNodeStatus.Online. Fixed independent diagnostics; warning->clear tested
  with stable Qt actions/no mutations. Live CLI read2026-10-09 reported Running,
  Health=[],ExitNodeStatus.Online=true. No live VPN/login/settings mutations.
- Public update URL not deployed or authorized yet. Do not call repo usable.

## Sources

GitLab official packages/debian_repository is Self-Managed Experiment only;
RPM registry unavailable. Use Generic + Pages signed native metadata.
https://docs.gitlab.com/ci/jobs/ci_job_token/ confirms Packages and Releases API
access. https://pkgs.tailscale.com/stable/ confirms resolute/trixie/fedora repos.
