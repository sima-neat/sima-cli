# sdk/script_runner.py
import re, subprocess, sys
from rich.console import Console
from sima_cli.sdk.cmdexec import exec_container_cmd

console = Console()

def parse_script(path):
    """Parse .sima script into ordered (type, content) tuples."""
    with open(path, "r") as f:
        lines = f.read()

    pattern = re.compile(r"(?P<block>(?P<target>\w+)\s*\{(?P<body>.*?)\})", re.S)
    result = []
    last_end = 0

    for match in pattern.finditer(lines):
        # Capture any local code before this block
        if match.start() > last_end:
            pre = lines[last_end:match.start()].strip()
            if pre:
                result.append(("local", pre))
        target = match.group("target")
        body = match.group("body").strip()
        result.append((target, body))
        last_end = match.end()

    # Remainder after last block
    if last_end < len(lines):
        tail = lines[last_end:].strip()
        if tail:
            result.append(("local", tail))

    return result


def execute_script(ctx, script_path):
    steps = parse_script(script_path)
    console.print(f"📜 Executing script: [cyan]{script_path}[/cyan]")

    for target, commands in steps:
        console.print(f"\n[bold cyan]▶ {target.upper()} Block[/bold cyan]")

        if target == "local":
            subprocess.run(commands, shell=True, check=False)
        else:
            console.print(f"[dim]Executing in container:[/dim] {target}")
            exec_container_cmd(ctx, target, commands)

    console.print("\n✅ [green]Script completed successfully![/green]")
