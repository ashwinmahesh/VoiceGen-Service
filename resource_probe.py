"""Unprivileged macOS resource snapshots. No inference imports or extra packages."""
import math
import os
import plistlib
import subprocess
import sys
import time


def cpu_seconds(value):
    days, _, clock = value.rpartition('-')
    parts = [float(part) for part in clock.split(':')]
    return (int(days) * 86400 if days else 0) + sum(part * 60**i for i, part in enumerate(reversed(parts)))


def process_tree(output, parent):
    rows = {}
    for line in output.splitlines():
        try:
            pid, ppid, cpu, rss, *started = line.split()
            rows[int(pid)] = (int(ppid), cpu_seconds(cpu), int(rss) * 1024, ' '.join(started))
        except (ValueError, TypeError):
            continue  # A process may disappear during collection.
    selected = {parent} if parent in rows else set()
    pending = set(rows) - selected
    while True:
        children = {pid for pid in pending if rows[pid][0] in selected}
        if not children:
            break
        selected.update(children)
        pending.difference_update(children)
    return {pid: rows[pid] for pid in selected}


def number(value, maximum=None):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        return None
    return value if maximum is None or value <= maximum else None


def gpu_counters(payload):
    devices = plistlib.loads(payload)
    stats = [node['PerformanceStatistics'] for node in devices
             if isinstance(node, dict) and isinstance(node.get('PerformanceStatistics'), dict)]
    def total(key):
        values = [number(node.get(key)) for node in stats]
        return sum(values) if values and all(value is not None for value in values) else None
    usage = [number(node.get('Device Utilization %'), 100) for node in stats]
    return {'gpu_percent': max(usage) if usage and all(v is not None for v in usage) else None,
            'gpu_allocated_bytes': total('Alloc system memory'),
            'gpu_in_use_bytes': total('In use system memory')}


class ResourceProbe:
    def __init__(self, pid=None, runner=subprocess.check_output, clock=time.monotonic):
        self.pid = pid or os.getpid()
        self.runner, self.clock = runner, clock
        self.previous, self.previous_at = {}, None

    def sample(self):
        result = dict(cpu_percent=None, rss_bytes=None, gpu_percent=None,
                      gpu_allocated_bytes=None, gpu_in_use_bytes=None)
        errors = []
        try:
            raw = self.runner(['/bin/ps', '-axo', 'pid=,ppid=,time=,rss=,lstart='],
                              timeout=.75, env={**os.environ, 'LC_ALL': 'C'})
            rows = process_tree(raw.decode(), self.pid)
            at = self.clock()
            if not rows:
                raise ValueError('Service process was not present in the snapshot')
            result['rss_bytes'] = sum(row[2] for row in rows.values())
            current = {(pid, row[3]): row[1] for pid, row in rows.items()}
            if self.previous_at is not None and at - self.previous_at >= .25:
                cpu = sum(max(0, value - self.previous.get(key, 0)) for key, value in current.items())
                result['cpu_percent'] = round(cpu / (at - self.previous_at) * 100, 2)
                self.previous, self.previous_at = current, at
            elif self.previous_at is None:
                self.previous, self.previous_at = current, at
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            self.previous, self.previous_at = {}, None
            errors.append('CPU/memory counters unavailable: ' + type(exc).__name__)
        if sys.platform == 'darwin':
            try:
                raw = self.runner(['/usr/sbin/ioreg', '-a', '-r', '-d', '1', '-c', 'IOAccelerator'], timeout=.75)
                result.update(gpu_counters(raw))
                if result['gpu_percent'] is None:
                    errors.append('GPU counters unavailable on this device')
            except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, plistlib.InvalidFileException) as exc:
                errors.append('GPU counters unavailable: ' + type(exc).__name__)
        else:
            errors.append('GPU counters require Apple Silicon macOS')
        return result, errors
