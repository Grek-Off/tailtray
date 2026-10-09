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
- Worker 01a11f5e-3fa3-7121-a551-094ee13d0fd4 currently owns ONLY
  scripts/build-repository.py and tests/test_repository.py. Main all other files.
- Qt worker finished/closed: QMenu wrappers retained, action order compared by
  stable objectName instead of Python is. Existing CI was red with deleted
  QAction on Qt6.10. Targeted cases x50 and suite passed worker reproduction.

## Current evidence (not final completion)

- Starting HEAD e501b52, clean before this task. No commit/push/tag this task yet.
- Unit tests packaging + release (21) pass. Matrix tests use temp fixtures, not
  real dist; no overwrite/removal of user artifacts.
- Signing CI variables metadata verified protected, signing key masked/hidden.
- Release container build running session19813, official Fedora44/Ubuntu26.04
  pull session90927. Older Fedora43/Debian13 build/test images cached.
- Need review generator, lint/full tests, actual package builds and signed native
  consumer checks, commit/push, verify CI all targets, then first v0.1.0 release.
- Public update URL not deployed or authorized yet. Do not call repo usable.

## Sources

GitLab official packages/debian_repository is Self-Managed Experiment only;
RPM registry unavailable. Use Generic + Pages signed native metadata.
https://docs.gitlab.com/ci/jobs/ci_job_token/ confirms Packages and Releases API
access. https://pkgs.tailscale.com/stable/ confirms resolute/trixie/fedora repos.
