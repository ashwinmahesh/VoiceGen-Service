"""Bounded, read-only access to runtime logs already attributed to one job."""
from datetime import datetime
import os
from pathlib import Path
import re
import stat

MAX_LOG_BYTES = 64 * 1024
ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))')


def job_log(row, root, attempt=None):
    current = row['attempts']
    attempt = current if attempt is None else attempt
    result = dict(job_id=row['id'], model=row['backend'], attempt=attempt, status=row['status'],
                  available=False, text='', truncated=False, max_bytes=MAX_LOG_BYTES,
                  source=None, updated_at=None, notice=None, error=row.get('error') if attempt == current else None)
    if attempt > current:
        raise FileNotFoundError('Job attempt not found')
    if attempt != current:
        result['notice'] = 'Runtime logs for older attempts were not retained separately. The recorded stages below still belong to this attempt.'
        return result
    if not current or not row.get('started_at'):
        result['notice'] = 'No runtime log yet. This generation attempt has not started.'
        return result
    if row['backend'] not in ('higgs', 'moss'):
        result['notice'] = 'This model does not retain a job-specific runtime log. Recorded stages and any generation error are shown instead.'
        return result
    # Never follow a metadata-supplied path or expose the shared backend/server log.
    filename = Path(root) / 'logs' / f"native-{row['id']}.log"
    try:
        descriptor = os.open(filename, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise OSError('Not a regular log file')
            started = datetime.fromisoformat(row['started_at']).timestamp()
            if info.st_mtime < started:
                result['notice'] = 'No runtime log for this attempt yet. An older attempt’s output is not shown.'
                return result
            result['truncated'] = info.st_size > MAX_LOG_BYTES
            stream.seek(max(0, info.st_size - MAX_LOG_BYTES))
            content = stream.read(MAX_LOG_BYTES)
            if result['truncated'] and b'\n' in content:
                content = content.split(b'\n', 1)[1]
    except FileNotFoundError:
        result['notice'] = 'No runtime log is available. It may not have been created yet, or the recording storage is disconnected.'
        return result
    except (OSError, ValueError):
        result['notice'] = 'The runtime log cannot be read. Check recording storage and file permissions.'
        return result
    text = ANSI.sub('', content.decode('utf-8', errors='replace')).replace('\r', '\n')
    text = ''.join(char for char in text if char in '\n\t' or (ord(char) >= 32 and ord(char) != 127))
    result.update(available=True, text=text, source='Native runtime', updated_at=info.st_mtime)
    if not text:
        result['notice'] = 'The runtime log is open but has no output yet.'
    return result
