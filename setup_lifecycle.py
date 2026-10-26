"""Own setup subprocesses so cancellation preserves data and leaves no installers running."""
import os
import signal
import subprocess
import sys
import time


class SetupCancelled(Exception):
    """The user chose to leave setup without starting more work."""


def announce_reuse():
    print('Setup is safe to rerun: verified model downloads are reused (no-op download).\n'
          'Dependencies and support files are checked and updated in place as needed; '
          'saved voices, recordings and queued jobs are kept.\n', file=sys.stderr, flush=True)


def announce_cancelled():
    print('\nSetup cancelled. Existing downloads and saved data are kept. '
          'Run make setup again to continue.', file=sys.stderr, flush=True)


def stop_setup_group(process, timeout=3):
    """Signal only the session/group created by this setup, including its descendants."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        process.poll()  # Reap the direct child while other group members finish.
        try:os.killpg(process.pid, 0)
        except ProcessLookupError:break
        time.sleep(.05)
    else:
        try:os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:pass
    process.wait()


def run_setup(command, environment, root):
    process=None
    try:
        # Avoid losing ownership if Ctrl+C lands between fork and Popen returning.
        previous=signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        try:
            process=subprocess.Popen(command, cwd=root, env=environment, start_new_session=True)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        return process.wait()
    except BaseException:
        # A second Ctrl+C must not interrupt cleanup and leave installers behind.
        previous_handler=signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if process is not None:stop_setup_group(process)
        finally:
            signal.signal(signal.SIGINT, previous_handler)
        raise
