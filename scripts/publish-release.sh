#!/bin/sh
# GitLab's CLI uses CI_JOB_TOKEN directly; never persist a personal token here.
set -eu

: "${CI_COMMIT_TAG:?Run this job on a release tag}"
: "${CI_COMMIT_SHA:?Missing release commit}"
: "${CI_JOB_TOKEN:?Missing CI authentication}"
printf '%s\n' "$CI_COMMIT_TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' || {
  echo "Expected a vMAJOR.MINOR.PATCH release tag" >&2; exit 1;
}
version=${CI_COMMIT_TAG#v}

# Missing artifacts must fail rather than produce an empty release.
set -- "dist/tailtray-$version.tar.gz" dist/tailtray-"$version"-*.rpm dist/tailtray_"$version"-*.deb
for artifact do
  test -f "$artifact" || { echo "Missing release artifact: $artifact" >&2; exit 1; }
done
(
  cd dist
  sha256sum "tailtray-$version.tar.gz" tailtray-"$version"-*.rpm tailtray_"$version"-*.deb > SHA256SUMS
)
glab release create "$CI_COMMIT_TAG" "$@" dist/SHA256SUMS \
  --ref "$CI_COMMIT_SHA" \
  --name "TailTray $CI_COMMIT_TAG" \
  --notes "Native Linux tray controller for Tailscale. Includes RPM, DEB, the tagged source archive, and SHA256SUMS. Packages do not restart Tailscale or change network preferences. Install Tailscale from its official repository first. RPM targets Fedora 43+; DEB targets Debian 13. Commit: $CI_COMMIT_SHA." \
  --use-package-registry --package-name tailtray
