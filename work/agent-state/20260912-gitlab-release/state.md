# GitLab packaging and runner — 2026-09-12

Request: push TailTray to GitLab, configure a runner on the previously supplied
VM, and provide working release builds of RPM, DEB and source archives. All
implementation changes must be committed. No credentials in source or logs.

Repository: private gitlab.com/grekov-labs/tailtray, project ID 86100130.
Verified configured glab API access and the existing dedicated GitLab SSH key.
Repository-local core.sshCommand selects that key; no global SSH config changed.
Commit 15216ae and all earlier application changes were pushed to main.
Final follow-up commit is recorded in Git history.

Implemented: verify/source/build/release stages, tag/version validation,
Fedora 43 RPM/SRPM, Debian 13 all-architecture DEB, git-archive source tarball,
glab 1.114.0 publication to Generic Package Registry with SHA256SUMS. Release
uses CI_JOB_TOKEN via GLAB_ENABLE_CI_AUTOLOGIN. No release tag created yet.
Packages do not start/restart Tailscale or change operator/network settings.

Evidence:
- GitLab CI Lint accepted the initial complete configuration.
- Actual RPM and SRPM builds passed in Fedora 43.
- Actual DEB build passed in Debian 13; build-essential is required in CI.
- Full make check and Python build passed in Fedora 43 with Xvfb and all
  native probes enabled. Ruff passed. Two more packaging regression tests
  cover matching metadata versions and refusing a mismatched tag.
- Image registry.gitlab.com/gitlab-org/cli:v1.114.0 pulled and reported 1.114.0.
- Independent packaging/release review completed; main handled integration.

Test-environment findings fixed without changing the application: PySide6
6.10.3 crashed when the test put Mock in the QObject class dictionary; a
static lambda avoids misleading Qt metadata introspection. X11 additionally
needs a mock StatusNotifierWatcher; it runs as a separate process on the
private test bus because same-thread synchronous D-Bus calls deadlock.

BLOCKER: VM SSH address/alias is not in the accessible task history or SSH
config. An asynchronous clarification was sent. The app thread-history tool
did not return and was terminated. No machine was guessed, no runner was
registered, no remote host was modified. GitLab pipeline 2842378325 was
pending for tag tailtray after the first push; consult latest pipeline after
the final follow-up push. Do not claim end-to-end CI or publication success.

Next: obtain VM host/user/key alias from user; inspect OS/resources and any
existing Docker/runner; configure a project-locked Docker executor with tag
tailtray, run_untagged=false, no privileged jobs and no Docker socket exposed
to jobs. Register via runner authentication token, stored only in protected
runner config. Then run main pipeline, inspect every job and artifact, create
the agreed release tag, and verify package-registry downloads/checksums.

Local dist/ contains ignored test artifacts built from 15216ae (not a final
published release). Cached test containers/images are disposable; no user
VPN/service/system packages were changed on the host.
