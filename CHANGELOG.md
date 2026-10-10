# Changelog

## 0.1.6 — 2026-10-10

- Public GitHub project with preserved Git history and version tags, MIT license,
  maintainer/AI-tool credits, contribution templates and approved security policy.
- Protected, immutable releases; native Fedora 43/44 RPM/SRPM, Debian 13 and
  Ubuntu 26.04 DEB packages and reproducible source archives.
- Signed APT/DNF repositories published through Pages, gated by strict native
  runtime tests and anonymous package-manager checks on all four distributions.
- Existing signing identity and historical package payloads retained. The old
  GitLab update endpoint remains available; repository changes are opt-in.
- Bounded, resumable publication validates exact assets, source commit, signatures
  and history. It refuses conflicting uploads, unsafe URLs and repeated API pages.

## 0.1.5 — 2026-10-09

- First fully verified public package/update channel on GitLab Pages.
- Fixed immutable release links for DEB filenames containing `+`.
- Verified installed Qt/GTK runtime tests and signed native APT/DNF consumers on
  Fedora 43/44, Debian 13 and Ubuntu 26.04.

Earlier tags remain available in Git history. Not every development tag represents
a successful public package release.
