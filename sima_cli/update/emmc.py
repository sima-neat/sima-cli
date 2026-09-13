"""Device-side preparation for whole-eMMC recovery writes."""


def prepare_emmc(device='/dev/mmcblk0'):
    """Unmount by block-device identity, then release LVM mappings; fail closed.

    Kept self-contained so netboot can execute it on the recovery device.
    """
    import json
    import os
    import re
    import subprocess

    def devices():
        result = subprocess.check_output(
            ['lsblk', '--json', '--paths', '--output', 'NAME,MAJ:MIN,TYPE', device],
            text=True,
        )
        found = {}

        def visit(nodes):
            for node in nodes:
                found[node['maj:min']] = node
                visit(node.get('children', []))

        visit(json.loads(result)['blockdevices'])
        if not found:
            raise RuntimeError('Cannot identify eMMC block devices')
        return found

    def unescape(value):
        return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)

    def mounts(blocks):
        with open('/proc/self/mountinfo') as stream:
            rows = [line.split() for line in stream]
        return [(int(row[0]), unescape(row[4]), blocks[row[2]]['name'])
                for row in rows if row[2] in blocks]

    blocks = devices()
    mounted = mounts(blocks)
    print('Mounted filesystems backed by ' + device + ':', flush=True)
    for _, target, source in mounted:
        print('  {} -> {}'.format(source, target), flush=True)
    if not mounted:
        print('  (none)', flush=True)
    if any(target == '/' for _, target, _ in mounted):
        raise RuntimeError('Root filesystem is on eMMC; boot into network recovery first')
    with open('/proc/swaps') as stream:
        for line in list(stream)[1:]:
            path = unescape(line.split()[0])
            stat = os.stat(path)
            dev = stat.st_rdev or stat.st_dev
            if '{}:{}'.format(os.major(dev), os.minor(dev)) in blocks:
                raise RuntimeError('Active swap on eMMC: ' + path)
    # Nested and stacked mounts must be released before their parents.
    for _, target, _ in sorted(mounted, key=lambda row: (row[1].count('/'), row[0]), reverse=True):
        subprocess.run(['umount', '--', target], check=True)
    if mounts(devices()):
        raise RuntimeError('eMMC still has mounted filesystems; refusing to flash')
    for node in reversed(list(blocks.values())):
        if node['type'] == 'lvm':
            subprocess.run(['lvchange', '-an', node['name']], check=True)
    remaining = devices()
    if mounts(remaining) or any(node['type'] not in ('disk', 'part') for node in remaining.values()):
        raise RuntimeError('eMMC still has active mounts or device mappings; refusing to flash')
    subprocess.run(['sync'], check=True)
    print('eMMC filesystems unmounted and mappings released.', flush=True)
