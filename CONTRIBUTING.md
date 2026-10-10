# Contributing to TailTray

Thank you for helping improve TailTray, an unofficial native tray client for
the official Tailscale daemon on Linux. The project is maintained by Vladimir
Grekov ([Grek-Off](https://github.com/Grek-Off)). Public project support and
conduct reports can be sent to [grekov.vs@gmail.com](mailto:grekov.vs@gmail.com).

## Before you start

Please search existing issues and pull requests before opening a new one. For
questions or a small change, an issue first can help avoid duplicated work.
Keep reports and patches focused, reproducible, and respectful. Do not include
credentials, private network data, or unredacted diagnostic output; see the
issue forms for the specific redaction rules.

## Local development

TailTray targets Linux and uses the official `tailscaled` service. The first
target environment is Fedora with GNOME or KDE. Install the project/runtime
dependencies described in [README.md](README.md), then use the repository's
existing checks:

```bash
make test
make static-check
make check
```

Run the narrowest relevant command while iterating. In a pull request, report
exactly which commands you ran, their result, and any checks you could not run
(with the reason). Do not claim coverage or native desktop validation that did
not happen.

## Changes and pull requests

- Keep changes small and explain the user-visible or maintenance problem they
  solve.
- Preserve the existing separation between TailTray and the official
  Tailscale daemon. Do not add secrets, local credentials, or machine-specific
  paths to the repository.
- Update documentation when behavior or user-facing commands change.
- Use a clear pull request title and summarize important implementation and
  compatibility considerations.
- Include meaningful verification evidence, including negative or unavailable
  checks where relevant.

The repository uses the [MIT License](LICENSE). By submitting a contribution,
you agree that it may be distributed under that license. Please make sure you
have the right to submit the work and identify material copied or adapted from
another source.

AI-assisted development is welcome when the contributor reviews the result and
can explain the change. AI tools do not replace human responsibility for the
submitted patch. Codex/OpenAI may be used as an AI development assistant; it is
not a human contributor or a GitHub account.

## Credits and maintainer contact

Contributors are credited through the project history and release materials.
Please add attribution for substantial third-party code, artwork, or text and
include its license or source in the pull request. For maintainer questions,
conduct concerns, or private reports, contact Vladimir Grekov at
[grekov.vs@gmail.com](mailto:grekov.vs@gmail.com).
