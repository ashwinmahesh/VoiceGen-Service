"""Native descendant reservation lifetime, using only tiny Python subprocesses."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from config import ROOT, PROJECT_ROOT
from inference_locks import inherit_reservations, reservation_descriptors


class ReservationTests(unittest.TestCase):
    def test_context_uses_actual_descriptors_and_is_thread_local(self):
        with tempfile.TemporaryFile(dir=ROOT/'cache/tmp') as handle:
            self.assertEqual(reservation_descriptors(),())
            with inherit_reservations(handle,handle,None):
                self.assertEqual(reservation_descriptors(),(handle.fileno(),))
                result=[]
                thread=threading.Thread(target=lambda:result.append(reservation_descriptors()))
                thread.start();thread.join();self.assertEqual(result,[()])
            self.assertEqual(reservation_descriptors(),())

    def test_native_time_wrapper_keeps_owner_and_gpu_locks_after_parent_kill(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'cache/tmp') as temporary:
            root=Path(temporary)
            parent_code=r'''
import fcntl,subprocess,sys
from pathlib import Path
from inference_locks import inherit_reservations,reservation_descriptors
root=Path(sys.argv[1]);handles=[]
for name in ['worker.lock','data.lock','gpu.lock']:
    handle=(root/name).open('a+');fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);handles.append(handle)
child_code="""
from pathlib import Path
import sys,time
root=Path(sys.argv[1]);(root/'child-ready').write_text('ready')
end=time.monotonic()+10
while not (root/'release').exists() and time.monotonic()<end:time.sleep(.01)
"""
with (root/'native.log').open('w') as stream,inherit_reservations(*handles):
    child=subprocess.Popen(['/usr/bin/time','-l',sys.executable,'-c',child_code,str(root)],
        pass_fds=reservation_descriptors(),stdout=stream,stderr=stream)
    child.wait()
'''
            env={**os.environ,'PYTHONPATH':str(PROJECT_ROOT)}
            process=subprocess.Popen([sys.executable,'-c',parent_code,str(root)],cwd=PROJECT_ROOT,env=env)
            def locked(name):
                with (root/name).open('a+') as handle:
                    try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:return True
                    return False
            try:
                end=time.monotonic()+5
                while not (root/'child-ready').exists() and time.monotonic()<end:
                    if process.poll() is not None:self.fail('Fake native parent exited early')
                    time.sleep(.01)
                self.assertTrue((root/'child-ready').exists())
                process.kill();process.wait(timeout=3)
                for name in ['worker.lock','data.lock','gpu.lock']:
                    self.assertTrue(locked(name),name+' released before native child exit')
                (root/'release').write_text('finish')
                end=time.monotonic()+5
                while any(locked(name) for name in ['worker.lock','data.lock','gpu.lock']) and time.monotonic()<end:time.sleep(.01)
                for name in ['worker.lock','data.lock','gpu.lock']:self.assertFalse(locked(name))
            finally:
                (root/'release').write_text('finish')
                if process.poll() is None:process.kill();process.wait(timeout=3)


if __name__=='__main__':unittest.main()
