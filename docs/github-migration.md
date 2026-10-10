# GitHub migration and local access

Target: **Grek-Off/tailtray**, a public repository maintained by Vladimir Grekov.
The public repository exists with preserved `main` history and v0.1.0–v0.1.5
tags. The proven v0.1.5 release was transferred byte-for-byte: ten original
assets plus a signed history archive covering fourteen immutable v0.1.4/v0.1.5
payloads. Restoring that archive and a non-mutating repeat publication were
verified. GitHub v0.1.6 CI/public APT-DNF verification is the remaining gate;
do not confuse prepared workflow YAML with a successful live release.

## One-time bootstrap token

Create a **fine-grained personal access token**, not a classic PAT:

- Name: `tailtray-bootstrap`.
- Resource owner: `Grek-Off`.
- Expiration: 30 days, or shorter if setup can be completed sooner.
- Repository access: temporarily **All repositories** because `tailtray` does
  has not been created and cannot be selected. **It now exists: narrow access
  to Only select repositories → tailtray.**
- Repository permissions, **Read and write**: Administration, Contents,
  Workflows, Actions, Pages, Environments.
- Metadata: Read-only (automatic). No other account, organization or repository
  permissions are needed for this setup.

Administration permits repository creation/settings; Contents and Workflows
permit Git history and workflow updates; Actions permits CI dispatch/retries;
Pages permits the update repository; Environments permits protected release
secrets. This temporarily grants substantial access to other repositories owned
by the same account. Use it only for TailTray, then immediately edit the token's
repository access to **Only select repositories → tailtray** after creation.
Changing its repository selection does not require pasting a new token into chat.

Permission requirements come from the official
[token guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens),
[repository API](https://docs.github.com/en/rest/repos/repos),
[Pages API](https://docs.github.com/en/rest/pages/pages) and
[environment secret API](https://docs.github.com/en/rest/actions/secrets).

## Store locally on Linux/KDE

Run as your regular desktop user, **without sudo**, from this checkout:

```fish
python3 scripts/github_credentials.py --owner Grek-Off
python3 scripts/github_credentials.py --owner Grek-Off --check
```

Paste the PAT only at the hidden terminal prompt. KDE Wallet may ask to unlock
the wallet or authorize Secret Service access. The command stores it through
`secret-tool`, verifies readback, and never prints it. `--check` confirms only
that a well-formed entry is available, not its validity or API permissions.

The helper refuses pipes, command-line token arguments, echo fallback, invalid
values and replacement of an existing entry. There is no plaintext fallback.
It creates only this scoped Secret Service entry:

```text
application=tailtray purpose=github-token owner=grek-off
```

Do not put the token in `.env`, Git remotes, shell variables, shell history,
GitHub Actions YAML, a backup file or chat. Do not run `secret-tool lookup` in a
terminal or tool log: it prints the secret. Migration tooling should consume
`read_token()` in memory and sanitize failures. Secret Service is not isolation
from every process running as the same desktop user.

## Publication and release gates

1. Verify authenticated account and repository permissions without logging
   credentials. Create a private staging repository; if the target already
   exists, inspect it rather than overwrite it.
2. Narrow bootstrap access to `tailtray`. Configure repository security,
   protected release environment and branch/tag rules before exposing a
   production signing key. Do not rotate the existing signing identity.
3. Publish an explicitly checked `main` commit and existing release tags using
   normal, non-forced pushes. Do not mirror remotes, local Codex refs or dangling
   Git objects. Preserve attribution and Git history.
4. Make the repository public as authorized; run the GitHub pipeline and verify
   native packages, signatures, release assets and anonymous apt/dnf consumers.
   Production Actions must use a short-lived `GITHUB_TOKEN`, not this PAT, and
   release signing must not be reachable from an untrusted pull request.
5. Keep the working GitLab update URL available until GitHub publication and
   consumers are proven. Do not silently redirect installed clients or delete
   old releases. A workflow file or local PASS is not proof of a live release.

The current working release is GitLab `v0.1.5`; installation and pinned signing
key verification remain documented in [releases.md](releases.md).

## Production workflow

`release.yml` runs only for stable version tags created by the maintainer. It
reuses the same read-only CI as pull requests. Only after all native runtime,
packaging and ephemeral-signing rehearsal checks pass can the protected
`release` environment expose the existing signing key. Signed package inputs
and repository outputs are separate immutable workflow artifacts.

The draft stays unpublished until native APT/DNF checks pass on all four target
distributions, Pages deployment succeeds and anonymous HTTPS consumers obtain
the exact tagged version. Finalization rechecks the tagged Git commit, source
archive, complete asset set, hashes and signed repository history. GitHub
release immutability locks newly published assets. Release workflows serialize
and never cancel an in-progress deployment. Retry failed jobs with their original
artifacts; never overwrite a conflicting release asset or move a version tag.

Actions use short-lived `github.token`; the local bootstrap PAT is not a CI
secret. Signing key is only an environment secret restricted to release tags.
PR CI cannot inherit secrets or write releases. GitHub-owned actions are pinned
to full commit SHAs. Container Git trusts only `github.workspace`, not `*`.

The earlier GitLab endpoint remains available for installed clients. Switching
an installed package repository is an explicit user action, not a hidden app
update. The signing fingerprint remains
`D432326D1167240864F8345763FA04F99681DA62`.
