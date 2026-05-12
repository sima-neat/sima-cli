from rich import print
from rich.table import Table
from rich.panel import Panel

def print_metadata_summary(metadata: dict):
    table = Table(show_header=False, box=None, padding=(0, 1))

    table.add_row("[bold]Name[/bold]", metadata.get("name", "N/A"))
    table.add_row("[bold]Version[/bold]", metadata.get("version", "N/A"))
    table.add_row("[bold]Release[/bold]", metadata.get("release", "N/A"))
    table.add_row("[bold]Description[/bold]", metadata.get("description", "N/A"))

    # Platform info
    platform_info = []
    for p in metadata.get("platforms", []):
        platform_type = p.get("type", "unknown")
        if platform_type == "board":
            compat = ", ".join(p.get("compatible_with", []))
            platform_info.append(f"{platform_type} ({compat})")
        elif platform_type in ("host", "generic"):
            os_list = ", ".join(p.get("os", []))
            platform_info.append(f"{platform_type} ({os_list})")
        else:
            platform_info.append(platform_type)

    table.add_row("[bold]Platforms[/bold]", "; ".join(platform_info) or "N/A")

    # Resources
    resource_count = len(metadata.get("resources", []))
    table.add_row("[bold]Resources[/bold]", f"{resource_count} file(s)")

    # Size
    size = metadata.get("size", {})
    table.add_row("[bold]Download Size[/bold]", size.get("download", "N/A"))
    table.add_row("[bold]Install Size[/bold]", size.get("install", "N/A"))

    print()
    print(Panel(table, title="📦 Package Summary", expand=False))
    print()


def parse_size_string_to_bytes(size_str: str) -> int:
    """
    Convert a size string like '40GB' or '512MB' to bytes.
    """
    size_str = size_str.strip().upper()
    units = {"KB": 10**3, "MB": 10**6, "GB": 10**9}

    for unit, multiplier in units.items():
        if size_str.endswith(unit):
            try:
                value = float(size_str[:-len(unit)].strip())
                return int(value * multiplier)
            except ValueError:
                raise ValueError(f"Invalid numeric value in size string: '{size_str}'")

    raise ValueError(f"Unrecognized size unit in '{size_str}'. Must be KB, MB, or GB.")