# sima-cli-triage

Use this skill when triaging GitHub issues in the `sima-neat/sima-cli`
repository.

## Repository Scope

`sima-cli` is the command-line interface for SiMa developer workflows. It covers
authentication, SDK setup and container management, Neat SDK shell access,
Vulcan package installation, package publishing helpers, DevKit update flows,
network repair helpers, playbook installation, and CLI self-update behavior.

## Classification

Use `bug` when the issue describes a command failure, unexpected prompt,
incorrect environment detection, Docker/SDK startup failure, install failure, or
regression from a previously working command.

Use `enhancement` when the issue asks for new CLI behavior, a new install flow,
new package support, new automation, or a usability improvement.

Use `documentation` when the issue is primarily about command docs, examples,
usage guidance, release notes, or confusing instructions.

Use `question` when the issue asks how something works and does not yet identify
a specific defect or requested change.

Use `help wanted` only when the issue is clearly suitable for external
contribution and does not require internal credentials, private infrastructure,
or release authority.

Do not propose `duplicate`, `invalid`, or `wontfix`. If an issue appears to be a
duplicate or out of scope, set `needs_human_review` to true and explain the
evidence in the summary or comment.

## Area Routing

Set `area` to one of these short names when the issue matches:

- `sdk-setup`: `sima-cli sdk setup`, SDK container creation/configuration,
  DevKit pairing, Model Compiler extension installation, or SDK user setup.
- `sdk-neat`: `sima-cli sdk neat`, stopped-container recovery, Neat SDK shell
  access, Colima/Docker restart behavior, or Neat SDK container selection.
- `docker`: Docker daemon startup, Docker socket permissions, Docker group
  membership, Colima availability, or container registry login/pull problems.
- `vulcan-install`: `sima-cli install`, `sima-cli neat install`, Vulcan metadata
  lookup, subfolder package refs, package resources, or artifact URLs.
- `package-build`: `sima-cli packages build`, generated `metadata.json`,
  package zip layout, or publishing metadata.
- `self-update`: sima-cli update checks, auto-update prompts, nested sima-cli
  invocations, `sima-cli selfupdate`, or version upgrade behavior.
- `playbooks`: `sima-cli playbooks`, coding-agent playbook installation,
  skills/rules sync, or playbook validation.
- `devkit-update`: `sima-cli update`, ELXR/boot image/device update commands,
  serial access, or DevKit update channels.
- `network`: SDK networking, NetworkManager repair, NFS/rsync fallback,
  firewall/port mapping, or syslog/network configuration.
- `release-ci`: GitHub Actions, release workflow, publishing workflow, PyPI,
  installer publishing, or CI failures.
- `docs`: generated command docs, README, usage examples, or release notes.
- `unknown`: not enough information to route.

## Common Missing Information

For SDK or Docker issues, ask for:

- host OS and architecture
- `sima-cli --version`
- exact command
- complete terminal output
- Docker or Colima status
- SDK image/container name when available

For Vulcan install issues, ask for:

- full install command
- package ref, branch, or version
- environment (`dev`, `staging`, or production) if known
- metadata URL or error output

For update or DevKit issues, ask for:

- DevKit IP or whether the command was run inside the SDK
- board/software version if available
- update channel or requested version
- full command output

## Comment Style

Keep the public triage comment concise and neutral. Do not claim a root cause
unless the issue text provides direct evidence. Prefer one short paragraph plus
specific missing information bullets when needed.
