# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately to Vladimir Grekov at
grekov.vs@gmail.com. Include the affected version and a minimal, redacted
reproduction. Do not send tokens, auth keys, login URLs or private network data.
Do not publish exploit details before the maintainer has had a chance to respond.

## Versions and response

Security fixes target the latest release. Older versions have no promised
backports. Maintenance is best-effort; no response time or audit guarantee is
promised.

## System and trust boundaries

TailTray is an unofficial Linux desktop controller for the official Tailscale
daemon, not a VPN implementation. Review scope includes the desktop application,
CLI and polkit integration, credential handling, installers and release tooling.
Daemon output, device names, URLs and downloaded release metadata cross trust
boundaries and must not be treated as executable instructions.

## Security expectations

Credentials must not enter Git, diagnostic logs or process arguments. Privileged
changes require normal system authorization. Release consumers must verify the
pinned signing key and payload integrity before trusting downloaded packages.
Report violations with realistic reachability and impact; this policy grants no
blanket exclusions or permission to test third-party systems.

## Limitations

Secret Service is not isolation from all processes running as the same desktop
user. CI tests do not prove live VPN reachability or every desktop configuration.
Upstream Tailscale and operating-system security remain separate dependencies.
