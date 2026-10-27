"""Generator-owned, restartable cleanup of recordings deleted at least 24 hours ago."""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import stat
import threading

from config import validate_mount
import voice_catalog as catalog

INTERVAL_SECONDS = 60
MARKER_NAME = re.compile(r'(voices|outputs)-[a-f0-9]{32}\.json')


def with_companions(paths):
    """AppleDouble companions on external Mac volumes belong to these exact files."""
    return [item for path in paths for item in (path.with_name('._'+path.name),path)]


def regular(path, missing_ok=True):
    path = Path(path)
    validate_mount(path)
    if path.is_symlink():
        raise ValueError('Cleanup refuses symbolic-link files')
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        if missing_ok:return path
        raise
    if not stat.S_ISREG(mode):
        raise ValueError('Cleanup requires regular files')
    return path


def in_use(db, identities, paths):
    """Include frozen references, emotion inputs and not-yet-validated queue requests."""
    def uses(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('voice_id','reference_id','emotion_reference_id') and isinstance(item,str) and item in identities:return True
                if key in ('reference','path','destination') and isinstance(item,str) and item in paths:return True
                if uses(item):return True
        elif isinstance(value, list):
            return any(uses(item) for item in value)
        return False
    for row in db.execute("SELECT id,payload FROM entries WHERE status IN ('queued','running')"):
        if row['id'] in identities or uses(json.loads(row['payload'])):return True
    return False


class CollectionCleaner:
    def __init__(self, manager, output_dir):
        self.manager, self.root, self.output_dir = manager, manager.root, Path(output_dir)
        self.stop = threading.Event()
        self.thread = None
        self.last_checked_at = None
        self.last_error = None
        self.removed_last_run = self.deferred_last_run = 0

    def snapshot(self):
        return dict(retention_seconds=catalog.DELETED_RETENTION_SECONDS,interval_seconds=INTERVAL_SECONDS,
            running=bool(self.thread and self.thread.is_alive()),last_checked_at=self.last_checked_at,
            last_error=self.last_error,removed_last_run=self.removed_last_run,deferred_last_run=self.deferred_last_run)

    def start(self):
        if self.manager._owner is None:
            raise RuntimeError('Collection cleanup requires the generator consumer lease')
        self.sweep()
        self.thread = threading.Thread(target=self.run,name='collection-cleanup',daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join()

    def run(self):
        while not self.stop.wait(INTERVAL_SECONDS):
            self.sweep()

    def source_shared(self, identity, source):
        folder = self.root/'voices'
        if folder.is_symlink():raise ValueError('Voice metadata directory must not be a symbolic link')
        for path in folder.glob('*.json'):
            if path.stem == identity or not catalog.IDENTITY.fullmatch(path.stem):continue
            row=json.loads(regular(path,False).read_text())
            if row.get('source_kind')=='references' and row.get('source_id')==source:return True
        return False

    def plan(self, kind, identity, record):
        paths = []
        source = record.get('source_reference_id')
        if kind == 'voices':
            folder = self.root/'voices'
            if folder.is_symlink():raise ValueError('Voice directory must not be a symbolic link')
            paths = [folder/f'{identity}.wav',folder/f'{identity}.json']
            if not record.get('cleanup_started'):
                row=json.loads(regular(paths[1],False).read_text())
                source=row.get('source_id') if row.get('source_kind')=='references' else None
        else:
            index=self.root/'artifact-index'/f'{identity}.json'
            if index.parent.is_symlink():raise ValueError('Artifact index must not be a symbolic link')
            output=record.get('output_path')
            if not record.get('cleanup_started'):
                if index.exists():output=json.loads(regular(index,False).read_text())['path']
                else:
                    output=next((str(folder/f'{identity}.wav') for folder in (self.root/'outputs',self.output_dir)
                                 if (folder/f'{identity}.wav').exists()),None)
            if not isinstance(output,str):raise ValueError('Deleted recording location is unavailable')
            output=Path(output)
            if not output.is_absolute() or output.name!=f'{identity}.wav':
                raise ValueError('Deleted output must be its own ID-named WAV file')
            paths=[output,output.with_suffix('.json'),index]
            if not record.get('cleanup_started'):
                metadata=json.loads(regular(output.with_suffix('.json'),False).read_text())
                if metadata.get('id')!=identity:raise ValueError('Recording metadata does not match its ID')
            record['output_path']=str(output)
        record['source_reference_id']=source
        reference_paths=[]
        if source is not None:
            if not isinstance(source,str) or not catalog.IDENTITY.fullmatch(source):raise ValueError('Invalid source reference ID')
            if not self.source_shared(identity,source):
                folder=self.root/'references'
                if folder.is_symlink() or (folder/'originals').is_symlink():
                    raise ValueError('Reference directories must not be symbolic links')
                reference_paths=[folder/f'{source}.wav',folder/f'{source}.json',folder/'originals'/f'{source}.bin']
        for path in with_companions([*paths,*reference_paths]):regular(path)
        return paths,reference_paths,source

    def purge(self, marker, clock):
        regular(marker,False)
        record=json.loads(marker.read_text())
        kind,identity=record['kind'],record['id']
        if marker != catalog.collection_marker(self.root,kind,identity):raise ValueError('Deletion marker ID does not match its filename')
        info=catalog.deletion_info(self.root,kind,identity)
        if datetime.fromisoformat(info.purge_after).timestamp()>clock:return 'retained'
        with self.manager.store.transaction(timeout=.25) as db:
            paths,references,source=self.plan(kind,identity,record)
            identities={identity}|({source} if references else set())
            if in_use(db,identities,{str(path) for path in [*paths,*references]}):
                message='Waiting for an unfinished job that still needs this recording.'
                if record.get('cleanup_deferred_reason')!=message:
                    record['cleanup_deferred_reason']=message;catalog.atomic_json(marker,record)
                return 'deferred'
            # Persist the plan before unlinking anything. A crash/restart resumes the same files.
            record.update(cleanup_started=True,cleanup_deferred_reason=None)
            catalog.atomic_json(marker,record)
            for path in with_companions([*paths,*references]):path.unlink(missing_ok=True)
            regular(marker.with_name('._'+marker.name)).unlink(missing_ok=True)
            marker.unlink()
        return 'removed'

    def sweep(self, clock=None):
        clock = datetime.now(timezone.utc).timestamp() if clock is None else clock
        removed=deferred=0;errors=[]
        try:
            validate_mount(self.root)
            folder=self.root/'collection-deletions'
            if folder.is_symlink():raise ValueError('Deletion directory must not be a symbolic link')
            # Serialize against restoration, voice copying and direct generator admission.
            for marker in sorted(folder.glob('*.json')):
                if self.stop.is_set():break
                if not MARKER_NAME.fullmatch(marker.name):continue
                try:
                    with self.manager.guard,catalog.metadata_lock:
                        outcome=self.purge(marker,clock)
                        removed+=outcome=='removed';deferred+=outcome=='deferred'
                except Exception as exc:
                    errors.append(str(exc)[:250]);logging.warning('Collection cleanup delayed: %s',exc)
        except Exception as exc:
            errors.append(str(exc)[:250]);logging.warning('Collection cleanup delayed: %s',exc)
        self.last_checked_at=datetime.fromtimestamp(clock,timezone.utc).isoformat()
        self.last_error='; '.join(errors[:3]) or None
        self.removed_last_run,self.deferred_last_run=removed,deferred
        return self.snapshot()
