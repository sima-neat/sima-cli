"""
Interactive shell (REPL) for sima-cli.

Always-on live command menu, built directly on prompt_toolkit:
  - a dropdown of commands with their help text shows as you type
  - it filters live, arrow keys pick, Enter runs
  - one level of sub-commands (e.g. `modelzoo <tab>`) is completed too
  - persistent history at ~/.sima-cli/.shell_history

If prompt_toolkit is missing we fall back to a plain input() loop so the
`shell` command still works (no menu, no history).

Still open (next pass, on purpose):
  - heavy group startup (env print + update check) re-runs per typed command
  - terminal-takeover commands (serial, selfupdate, network) not blocked yet
"""

import os
import shlex
import click

# Words that leave the loop.
_EXIT_WORDS = {"exit", "quit"}

# Where prompt history is stored for the rich shell.
_HISTORY_PATH = os.path.expanduser("~/.sima-cli/.shell_history")

_BANNER = "sima-cli interactive shell. Type to filter the menu, ↑↓ to pick, Enter to run, 'exit' or Ctrl-D to leave."

# Available colour themes for the shell.
_THEMES = ("dark", "light")
_DEFAULT_THEME = "dark"
# Config key under ~/.sima-cli/config.json where the theme is remembered.
_THEME_CONFIG_KEY = "shell_theme"


def _get_saved_theme():
    """Read the remembered theme from config, falling back to the default."""
    from sima_cli.utils.config import load_config

    theme = load_config().get(_THEME_CONFIG_KEY)
    return theme if theme in _THEMES else _DEFAULT_THEME


def _save_theme(theme):
    """Persist the chosen theme to config."""
    from sima_cli.utils.config import load_config, save_config

    config = load_config()
    config[_THEME_CONFIG_KEY] = theme
    save_config(config)


def _dispatch(line):
    """Turn a typed line into args and run it through the main group."""
    # Import here to avoid a circular import (cli.py imports this module).
    from sima_cli.cli import main

    try:
        args = shlex.split(line)
    except ValueError as e:
        click.echo(f"❌ Could not parse input: {e}")
        return

    if not args:
        return

    try:
        # standalone_mode=False stops Click from calling sys.exit() and lets
        # us keep the loop alive after a command finishes or errors.
        main.main(args=args, standalone_mode=False)
    except click.ClickException as e:
        e.show()
    except SystemExit:
        pass
        # A subcommand failure shouldn't terminate the interactive shell
        return
    except Exception as e:
        click.echo(f"❌ {e}")


def _make_completer():
    """A prompt_toolkit completer that reads commands straight from the group."""
    from prompt_toolkit.completion import Completer, Completion

    class _SimaCompleter(Completer):
        def _yield_commands(self, group, word):
            for name in sorted(group.commands):
                cmd = group.commands[name]
                if getattr(cmd, "hidden", False):
                    continue
                if name.startswith(word):
                    yield Completion(
                        name,
                        start_position=-len(word),
                        display=name,
                        display_meta=cmd.get_short_help_str(limit=50),
                    )

        def get_completions(self, document, complete_event):
            from sima_cli.cli import main

            stripped = document.text_before_cursor.lstrip()
            parts = stripped.split(" ")

            # Still typing the top-level command name.
            if len(parts) == 1:
                yield from self._yield_commands(main, parts[0])
                return

            # `<group> <subcommand>` -> complete the group's sub-commands.
            first = main.commands.get(parts[0])
            if isinstance(first, click.Group) and len(parts) == 2:
                yield from self._yield_commands(first, parts[1])
            # Past that (options/arguments) we leave the box free for now.

    return _SimaCompleter()


# Per-theme styling. The menu stays blended (`bg:default`), but each theme sets
# explicit FONT COLOURS — the typed input (base "" style), the prompt accent,
# the command names, and the dimmed descriptions — so switching actually
# recolours the text. Only the selected row gets a coloured highlight.
_THEME_STYLES = {
    # Tuned for a dark terminal: light text, cyan accent.
    "dark": {
        "": "fg:#d0d0d0",                                            # typed input text
        "prompt": "fg:#00d7ff bold",                                # prompt accent
        "completion-menu": "bg:default",
        "completion-menu.completion": "bg:default fg:#d0d0d0",       # command names
        "completion-menu.completion.current": "bg:#005f87 fg:#ffffff bold",
        "completion-menu.meta.completion": "bg:default fg:#808080",  # descriptions
        "completion-menu.meta.completion.current": "bg:#005f87 fg:#d0d0d0",
        "scrollbar.background": "bg:default",
        "scrollbar.button": "bg:#5f5f5f",
    },
    # Tuned for a light terminal: dark text, blue accent.
    "light": {
        "": "fg:#1c1c1c",                                            # typed input text
        "prompt": "fg:#0000af bold",                                # prompt accent
        "completion-menu": "bg:default",
        "completion-menu.completion": "bg:default fg:#1c1c1c",       # command names
        "completion-menu.completion.current": "bg:#afd7ff fg:#000000 bold",
        "completion-menu.meta.completion": "bg:default fg:#6c6c6c",  # descriptions
        "completion-menu.meta.completion.current": "bg:#afd7ff fg:#1c1c1c",
        "scrollbar.background": "bg:default",
        "scrollbar.button": "bg:#bcbcbc",
    },
}


def _theme_style(theme):
    """Build a prompt_toolkit Style for the given theme.

    The menu blends with the terminal (`bg:default`); only the selected row is
    highlighted. Each theme tunes the prompt accent colour and selected-row
    weight so the look suits a dark or light terminal.
    """
    from prompt_toolkit.styles import Style

    return Style.from_dict(_THEME_STYLES.get(theme, _THEME_STYLES[_DEFAULT_THEME]))


def _resolve_theme_command(line, current):
    """Handle a `:theme [dark|light]` line. Returns the new theme, or None.

    `:theme`        -> flip dark<->light
    `:theme dark`   -> set explicitly
    Returns None when `line` is not a theme command.
    """
    parts = line.split()
    if not parts or parts[0] != ":theme":
        return None

    if len(parts) == 1:
        return "light" if current == "dark" else "dark"
    if parts[1] in _THEMES:
        return parts[1]

    click.echo(f"❌ Unknown theme '{parts[1]}'. Choose from: {', '.join(_THEMES)}.")
    return current  # no change, but consume the command


def _rich_repl(theme):
    """Always-on live-menu REPL backed by prompt_toolkit."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.shortcuts import CompleteStyle
    from prompt_toolkit.application import get_app

    os.makedirs(os.path.dirname(_HISTORY_PATH), exist_ok=True)

    # One history instance, reused even when the session is rebuilt on a theme change.
    history = FileHistory(_HISTORY_PATH)
    completer = _make_completer()

    def build_session(active_theme):
        return PromptSession(
            message=[("class:prompt", "sima-cli> ")],
            completer=completer,
            complete_while_typing=True,           # menu updates live, no Tab needed
            complete_style=CompleteStyle.COLUMN,  # vertical list with descriptions
            style=_theme_style(active_theme),     # blended, theme-aware styling
            history=history,
        )

    session = build_session(theme)

    def _open_menu():
        # Pop the menu open as soon as the prompt is shown.
        get_app().current_buffer.start_completion(select_first=False)

    while True:
        try:
            line = session.prompt(pre_run=_open_menu)
        except KeyboardInterrupt:
            # Ctrl-C: clear the current line, stay in the shell.
            continue
        except EOFError:
            # Ctrl-D: leave.
            break

        line = line.strip()
        if not line:
            continue
        if line.lower() in _EXIT_WORDS:
            break

        # In-shell theme switch (e.g. `:theme`, `:theme light`).
        new_theme = _resolve_theme_command(line, theme)
        if new_theme is not None:
            if new_theme != theme:
                theme = new_theme
                _save_theme(theme)
                session = build_session(theme)  # rebuild to apply the new style
            click.echo(f"🎨 Theme: {theme}")
            continue

        _dispatch(line)


def _basic_repl():
    """Fallback REPL using plain input(); no menu, no history."""
    while True:
        try:
            line = click.prompt("sima-cli>", prompt_suffix=" ", default="", show_default=False)
        except (EOFError, KeyboardInterrupt):
            click.echo()
            break

        line = line.strip()
        if not line:
            continue
        if line.lower() in _EXIT_WORDS:
            break

        _dispatch(line)


@click.command(name="shell")
@click.option(
    "-t", "--theme",
    type=click.Choice(_THEMES, case_sensitive=False),
    default=None,
    help="Colour theme (dark or light). Defaults to the last used, then dark. Switch live with ':theme'.",
)
@click.pass_context
def shell_cmd(ctx, theme):
    """Start an interactive sima-cli shell."""
    click.echo(_BANNER)

    # Explicit flag wins; otherwise use the remembered theme. Persist the choice.
    theme = theme.lower() if theme else _get_saved_theme()
    _save_theme(theme)

    try:
        _rich_repl(theme)
    except ImportError:
        click.echo("ℹ️  prompt_toolkit not installed; using basic shell (no menu/history).")
        _basic_repl()


def register_shell_command(main):
    """Attach the 'shell' command to the main Click CLI."""
    main.add_command(shell_cmd)
