# sima-cli Agent Instructions

## Generated command documentation

- The Markdown files under `docs/sima-cli/commands/` and the generated command listings in `docs/sima-cli/index.md` come from the Click command definitions and help text in the source tree.
- Do not manually edit generated command documentation.
- Whenever a CLI command, option, argument, command summary, help string, or command docstring changes, run `./build.sh`. This is the required workflow for regenerating the command documentation and validating that the package still builds.
- Review and commit the documentation changes produced by `./build.sh` together with the source change.
