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

## Extended Analysis

Most issues should be triaged from the issue text, command output, and this
repo's local triage guidance. Do not request extended analysis just because an
issue mentions SDK, Model Compiler, Insight, or Core. Request extended analysis
only when the issue includes enough specific detail that checking another public
repo can materially improve routing or the next maintainer action.

Allowed cross-reference repositories:

- `sima-neat/sdk`
- `sima-neat/model-compiler`
- `sima-neat/insight`
- `sima-neat/core`

Request `sima-neat/sdk` when:

- the issue involves SDK image packaging, SDK setup behavior, DevKit pairing,
  SDK container startup, SDK environment contents, or SDK workflow behavior that
  likely lives outside the CLI.
- the issue includes an SDK image tag, branch, workflow name, or setup log that
  can be checked against SDK repo scripts/workflows/docs.

Request `sima-neat/model-compiler` when:

- the issue involves Model Compiler extension installation, quantization,
  compilation, examples, BF16/INT8 behavior, model artifacts, or compiler logs.
- the issue includes enough model/compiler output to compare with examples,
  docs, or known compiler workflow files.

Request `sima-neat/insight` when:

- the issue involves insight package installation through sima-cli, media/source
  workflow setup, insight release packaging, or sima-cli integration with
  insight artifacts.

Request `sima-neat/core` when:

- the issue involves core runtime behavior surfaced through sima-cli commands,
  tutorials/examples installed or referenced by sima-cli, or packaging flows
  where sima-cli routes users into core artifacts.

When extended analysis is useful, set:

- `extended_analysis_required`: `true`
- `extended_analysis_repos`: only the specific repo or repos needed from the
  allowlist above
- `extended_analysis_reason`: a concise explanation of what should be checked

If the issue lacks concrete logs, command output, package refs, or file names,
do not request extended analysis. Ask the reporter for the missing information
instead.

## Comment Style

Keep the public triage comment concise and neutral. Do not claim a root cause
unless the issue text provides direct evidence. Prefer one short paragraph plus
specific missing information bullets when needed.
