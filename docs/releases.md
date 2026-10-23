# Packages, Releases, and Updates

Public project: [Grek-Off/tailtray](https://github.com/Grek-Off/tailtray).
A release is created after pushing a protected `vX.Y.Z` tag whose version matches
the versions in TOML, Python, the RPM spec, the Debian changelog, and AppStream. A
partial release is not published. Status: [Release workflow](https://github.com/Grek-Off/tailtray/actions/workflows/release.yml);
downloads: [Releases](https://github.com/Grek-Off/tailtray/releases).

Verified release: [v0.1.6](https://github.com/Grek-Off/tailtray/releases/tag/v0.1.6),
commit `3e310bb02a851a5f769672ea4e0571e48f8ba6d9`,
[production pipeline](https://github.com/Grek-Off/tailtray/actions/runs/38045667801):
23 jobs SUCCESS. A separate tokenless check confirmed 11 immutable assets,
21 signed history payloads for v0.1.4/v0.1.5/v0.1.6, and an exact source archive match.

| System | Packages | Verification |
| --- | --- | --- |
| Fedora 43 | `.fc43.noarch.rpm`, `.fc43.src.rpm` | native build, installed Qt/GTK runtime, DNF |
| Fedora 44 | `.fc44.noarch.rpm`, `.fc44.src.rpm` | native build, installed Qt/GTK runtime, DNF |
| Debian 13 | `-1+debian13_all.deb` | native build, installed Qt/GTK runtime, APT |
| Ubuntu 26.04 | `-1+ubuntu26.04_all.deb` | native build, installed Qt/GTK runtime, APT |

The application architecture is `noarch`/`all`: Python with no bundled binary dependencies.
CI checks x86_64; there is no separate end-to-end arm64 run. Ubuntu 24.04 is not
included: its standard archive does not contain the required PySide6 QtWidgets/QtDBus.
Packages do not include Tailscale: add its official repository first.
Installation does not switch the VPN, assign an operator, start the daemon, or
enable desktop autostart. These actions remain under the application's and user's control.

The release also includes the reproducible source archive `tailtray-X.Y.Z.tar.gz`,
`SHA256SUMS`, the `SHA256SUMS.asc` signature and the public key.
`tailtray-repository-X.Y.Z.tar.gz` contains the signed history of immutable payloads.
The source archive exactly matches `git archive` of the release commit, compressed
with `gzip -n`. All eleven assets live in the GitHub Release, not only in temporary
workflow artifacts.

## Automated Tests and Publishing

The following run on main/PR and in the reusable release CI:

- Ruff, static-check, and full strict pytest: CLI, state/race conditions,
  disconnect/connect, operator/login, profiles, exit nodes, Qt/GTK, and D-Bus,
  including the left-click crash regression in 100 separate runs.
- JUnit and coverage artifacts; coverage includes subprocesses. This is a measurement,
  not a promise that 100% of scenarios are tested.
- Installation of each RPM/DEB in its native image. Runtime tests import
  `/usr/lib/tailtray`, not the checkout. Release tooling tests run once.
- A release rehearsal that signs copies of seven artifacts with a one-time key
  in tmpfs, builds real APT/RPM indexes, verifies history restoration and exercises
  four native consumers.

In CI, any skip is an error. The test deadline is 60s, the suite deadline is 180s,
and the address-space limit is 2 GiB. Pagination and downloads are bounded; repeating a
page is an error, and query parameters are parsed explicitly. A local run without
native dependencies may have skips:

```fish
env PYTHONPATH=src .venv/bin/python -m pytest
```

Production release: read-only CI → protected signing/draft → four checks of the
real signed repository → Pages → four anonymous HTTPS APT/DNF consumers → final
assets/history check → Release.
The key is unavailable to PR CI and is not used in the rehearsal. Actions uses the
short-lived `github.token`, not a local PAT. Actions are pinned to full SHAs;
publishing is serialized. Completed new releases are protected by GitHub immutability.

A failure may be retried only with the same source artifacts. A retry checks
existing assets and does not overwrite them. Publishing stops on a hash/size
conflict; do not move the old tag or delete assets to bypass it.
Retry the failed jobs of the original run: rebuilding an already signed version may
produce different native package bytes and a consequent conflict.

## DNF / APT Repository

GitHub Releases are not themselves an APT/DNF repository. Pages serves a static
tree of signed indexes and packages:
`https://grek-off.github.io/tailtray`.
APT verifies `InRelease`/`Release.gpg`; RPM verifies packages and `repomd.xml`.
Consumers do not need a token, GitHub account, or browser login.

For a new installation, after a successful Release workflow:

```fish
bash scripts/install-repository.sh https://grek-off.github.io/tailtray
```

The script checks the OS and pinned fingerprint, adds only TailTray, and installs
the package. It does not overwrite different configurations or keys. Fedora:
`gpgcheck=1`, `repo_gpgcheck=1`. APT: separate `Signed-By`, without `trusted=yes`.
Once the repository is configured, updates use the standard package manager:

```fish
sudo dnf upgrade --refresh       # Fedora
sudo apt-get update             # Debian / Ubuntu
sudo apt-get upgrade
```

The former `https://tailtray-657378.gitlab.io` is preserved. Already installed clients
are not switched silently. To switch voluntarily, back up your TailTray repository
file, then use `sudoedit` to edit only the original TailTray configuration file:

- Fedora: `/etc/yum.repos.d/tailtray.repo`; in `baseurl` and `gpgkey`, replace
  `https://tailtray-657378.gitlab.io` with `https://grek-off.github.io/tailtray`.
  Keep `/rpm/fedora/$releasever/`, `gpgcheck=1`, and `repo_gpgcheck=1`.
- Debian/Ubuntu: `/etc/apt/sources.list.d/tailtray.sources`; replace only the
  beginning of `URIs` with `https://grek-off.github.io/tailtray/apt`. Do not change
  the Suite or the separate `/usr/share/keyrings/tailtray-archive-keyring.asc` in `Signed-By`.

The key and fingerprint are identical on both channels. After editing, refresh the
metadata; if an error occurs, restore the saved repository file. Do not disable
signature verification. Automatic removal of the old channel is not required.

## Signing and the Next Release

Fingerprint: `D432326D1167240864F8345763FA04F99681DA62`.
Key: `packaging/repository-key.asc`. The private key is not included in Git, tar.gz,
packages, or artifacts. The backup is a Secret Service entry
`application=tailtray purpose=release-signing project=86100130`, not a plaintext file.
GitHub stores the same base64 key as the environment secret `TAILTRAY_SIGNING_KEY`
in `release`, permitted only for `v*.*.*` tags; import is only into tmpfs.
The key is valid for two years; replacement requires a separately agreed procedure.

Update the five versions and changelog, verify, and commit the changes. After CI:

```fish
git tag -a vX.Y.Z -m 'TailTray X.Y.Z'
git push origin vX.Y.Z
```

`origin` is GitHub; the old remote is preserved as `gitlab`. The local PAT remains
in Secret Service: [access and configuration](github-migration.md).
Main requires successful all-jobs CI for PRs; a maintainer/admin may make ordinary
direct changes. Force-pushes and deletion of main/release tags are prohibited.

## Preserved GitLab Channel

The first fully verified public release was GitLab `v0.1.5`, commit
`34b965591aa4e9463dc6a7565ed1b6aad96d96df`,
[pipeline 2930998441](https://gitlab.com/grekov-labs/tailtray/-/pipelines/2930998441):
21 jobs, 707 JUnit checks, with no errors or skips. The Git repository and Generic
Registry on GitLab are private; Pages is public. Old configurations and releases
have not been deleted.

GitHub v0.1.5 contains ten original files and the signed history of fourteen
v0.1.4/v0.1.5 payloads; the migration did not rebuild packages or change signatures.
The history archive was restored, and repeat publication with unchanged files was verified.
Earlier development tags were preserved but were not declared successful releases.

Sources: [GitHub Releases API](https://docs.github.com/en/rest/releases/releases),
[Pages deployment](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[immutable releases](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes).
