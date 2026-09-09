"""Read-only interpretation and presentation of eLxr A/B state."""
import re
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text


def parse_state(output):
    fields = {}
    for line in output.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            fields[key.strip().lower()] = value.strip()
    fields['raw'] = output
    for key in ('running slot', 'active slot', 'fallback slot'):
        value = fields.get(key, '')
        match = re.match(r'([AB])\b', value, re.I)
        fields[key] = match.group(1).upper() if match else 'unknown'
    for name in ('upgrade_available', 'next-boot'):
        match = re.search(r'\b' + name + r':\s*([^,\s]+)', output, re.I)
        fields[name] = match.group(1) if match else 'unknown'
    for slot in ('A', 'B'):
        match = re.search(r'\b(valid|invalid|blank)\s+' + slot + r'\b', output, re.I)
        fields['validity ' + slot] = match.group(1).lower() if match else 'unknown'
    lower = output.lower()
    fields['rollback'] = ('normal' if 'normal boot' in lower else
                          'rollback' if re.search(r'\brollback\s+boot\b|rollback.*(?:yes|latched)', lower) else 'unknown')
    return fields


def inspect_target(target, display=True):
    output = target.run('simaai-ab-info')
    # These queries do not change the slot, upgrade flag, or boot counter.
    extra = target.run('simaai-trootctl bootcount', check=False)
    state = parse_state(output)
    state['bootcount'] = extra.strip() or 'unknown'
    if display:
        render_state(state)
    return state


def render_state(state):
    console = Console()
    table = Table(title='eLxr system slots')
    for title in ('Slot', 'Role', 'Version', 'OS', 'Validity'):
        table.add_column(title)
    running = state.get('running slot', 'unknown')
    for slot in ('A', 'B'):
        prefix = 'active' if state.get('active slot') == slot else 'fallback' if state.get('fallback slot') == slot else None
        role = 'Running' if slot == running else 'Fallback' if prefix == 'fallback' else 'Unknown'
        values = [slot, role, state.get(f'{prefix} version', 'unknown'), state.get(f'{prefix} os', 'unknown'), state.get('validity ' + slot, 'unknown')]
        table.add_row(*(Text(value) for value in values))
    console.print(table)
    details = '\n'.join([
        'Medium: ' + state.get('medium', 'unknown'),
        'Next boot: ' + state.get('next-boot', 'unknown'),
        'Upgrade pending: ' + state.get('upgrade_available', 'unknown'),
        'Boot status: ' + state.get('rollback', 'unknown'),
        'Boot attempts: ' + state.get('bootcount', 'unknown'),
    ])
    console.print(Panel(Text(details), title='Boot state'))
