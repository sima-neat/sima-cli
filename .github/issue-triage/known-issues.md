# sima-cli Known Triage Patterns

## Docker Group Membership

If a Linux user can run Docker only after refreshing group membership, route to
`docker`. Ask whether the user has restarted the shell or run `newgrp docker`
after being added to the Docker group.

## Neat SDK After Docker Or Colima Restart

If `sima-cli sdk neat` fails after Docker or Colima restarts, route to
`sdk-neat`. Recent behavior should attempt to start stopped Neat SDK containers
before asking the user to run setup again.

## Model Compiler Extension Prompt During SDK Setup

If SDK setup prompts for sima-cli update while installing the Model Compiler
extension, route to `self-update` and `sdk-setup`. Nested sima-cli invocations
should use auto-update behavior rather than asking a second interactive update
question.

## Vulcan Package Subfolders

If an install command like `repo/folder@branch:tag` fails to resolve metadata,
route to `vulcan-install`. The expected behavior is to resolve the
subfolder-specific `metadata.json`.

## Package Metadata Generation

If the issue is about SDK or examples being installable through
`sima-cli neat install`, route to `package-build` or `vulcan-install` depending
on whether the failure is metadata generation or package installation.
