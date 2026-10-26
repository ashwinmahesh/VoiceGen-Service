"""Private SQLite queue. All admissions, claims and removals are transactions.

One owning JobManager holds the database-adjacent worker lock before recovery.
Production databases live in the serving project; injectable paths isolate tests.
SQLite uses rollback-journal/FULL mode.
"""
from contextlib import contextmanager, closing
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from config import validate_mount
from voice_catalog import now


class QueueConflict(ValueError):
    pass


class QueueStore:
    def __init__(self, root, db_path=None):
        self.path = (Path(db_path) if db_path is not None else Path(root)/'jobs/queue.sqlite3').expanduser().resolve()
        self.legacy_path = Path(root)/'jobs/queue.sqlite3'

    def migrate(self):
        """Called only while both data and database worker ownership are held."""
        source = self.legacy_path
        if source.absolute() == self.path.absolute() or not source.exists():
            return
        validate_mount(source)
        if self.path.exists():
            raise RuntimeError('Both legacy and project queue databases exist; review migration before startup')
        backup = source.with_name(source.name+'.migrated-backup')
        if backup.exists():
            raise RuntimeError('Legacy queue backup already exists; review migration before startup')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name+'.migrating')
        if temporary.exists():
            raise RuntimeError('Incomplete queue migration exists; review before startup')
        try:
            with closing(sqlite3.connect(source)) as original, closing(sqlite3.connect(temporary)) as target:
                if original.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Legacy queue integrity check failed')
                original.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Migrated queue integrity check failed')
                if list(original.iterdump()) != list(target.iterdump()):
                    raise RuntimeError('Migrated queue contents differ')
            temporary.replace(self.path)
            source.rename(backup)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    @contextmanager
    def transaction(self):
        validate_mount(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        with self.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS entries (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL, backend TEXT NOT NULL, model TEXT NOT NULL,
                mode TEXT NOT NULL, status TEXT NOT NULL,
                generate_immediately INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                started_at TEXT, completed_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL, key_hash TEXT UNIQUE, fingerprint TEXT NOT NULL,
                stage TEXT NOT NULL, error TEXT, result TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS runnable ON entries(status,backend,generate_immediately DESC,sequence)')
            db.execute('''CREATE TABLE IF NOT EXISTS candidate_batches (
                id TEXT PRIMARY KEY, backend TEXT NOT NULL, created_at TEXT NOT NULL,
                key_hash TEXT UNIQUE, fingerprint TEXT NOT NULL, job_ids TEXT NOT NULL)''')

    @staticmethod
    def decode(row):
        if row is None:
            raise FileNotFoundError('Queue entry not found')
        row = dict(row)
        row['generate_immediately'] = bool(row['generate_immediately'])
        for key in ['payload','result']:
            if row[key] is not None:
                row[key] = json.loads(row[key])
        return row

    def existing(self, key_hash, fingerprint, compatible=(), legacy_compatible=()):
        if not key_hash:
            return None
        with self.transaction() as db:
            row = db.execute('SELECT * FROM entries WHERE key_hash=?', (key_hash,)).fetchone()
            if row is None:
                return None
            legacy = legacy_compatible if json.loads(row['payload']).get('fingerprint_version',1)<2 else ()
            if row['fingerprint'] not in (fingerprint,*compatible,*legacy):
                raise QueueConflict('This idempotency key was used with different settings')
            return self.decode(row)

    def admit(self, backend, model, mode, immediate, payload, key_hash, fingerprint):
        timestamp = now()
        with self.transaction() as db:
            if key_hash:
                old = db.execute('SELECT * FROM entries WHERE key_hash=?', (key_hash,)).fetchone()
                if old:
                    if old['backend'] != backend:
                        raise QueueConflict('This idempotency key belongs to another backend')
                    if old['fingerprint'] != fingerprint:
                        raise QueueConflict('This idempotency key was used with different settings')
                    return self.decode(old)
            identity = uuid4().hex
            payload['destination'] = str(Path(payload['destination'])/f'{identity}.wav')
            db.execute('''INSERT INTO entries
                (id,backend,model,mode,status,generate_immediately,created_at,updated_at,
                 payload,key_hash,fingerprint,stage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identity,backend,model,mode,'queued',int(immediate),timestamp,timestamp,
                 json.dumps(payload,allow_nan=False),key_hash,fingerprint,'Waiting for execution slot'))
            return self.decode(db.execute('SELECT * FROM entries WHERE id=?',(identity,)).fetchone())

    def read(self, identity):
        with self.transaction() as db:
            return self.decode(db.execute('SELECT * FROM entries WHERE id=?',(identity,)).fetchone())

    def _candidate_batch(self, db, row):
        if row is None:
            raise FileNotFoundError('Candidate batch not found')
        result = dict(row)
        result['jobs'] = [self.decode(db.execute('SELECT * FROM entries WHERE id=?',(identity,)).fetchone())
                          for identity in json.loads(result.pop('job_ids'))]
        return result

    def read_candidates(self, identity):
        with self.transaction() as db:
            return self._candidate_batch(db, db.execute('SELECT * FROM candidate_batches WHERE id=?',(identity,)).fetchone())

    def existing_candidates(self, backend, key_hash, fingerprint):
        if not key_hash:
            return None
        with self.transaction() as db:
            row = db.execute('SELECT * FROM candidate_batches WHERE key_hash=?',(key_hash,)).fetchone()
            if row is None:
                return None
            if row['backend'] != backend or row['fingerprint'] != fingerprint:
                raise QueueConflict('This candidate idempotency key was used with another backend or different settings')
            return self._candidate_batch(db, row)

    def admit_candidates(self, backend, model, mode, immediate, payloads, key_hash, fingerprint):
        """Commit the group and all options together before the worker can claim one."""
        timestamp = now()
        with self.transaction() as db:
            if key_hash:
                old = db.execute('SELECT * FROM candidate_batches WHERE key_hash=?',(key_hash,)).fetchone()
                if old:
                    if old['backend'] != backend or old['fingerprint'] != fingerprint:
                        raise QueueConflict('This candidate idempotency key was used with another backend or different settings')
                    return self._candidate_batch(db, old)
            batch_id = uuid4().hex
            identities = []
            for index, payload in enumerate(payloads, 1):
                identity = uuid4().hex
                identities.append(identity)
                payload = {**payload, 'destination':str(Path(payload['destination'])/f'{identity}.wav'),
                           'candidate_batch_id':batch_id, 'candidate_index':index, 'candidate_count':len(payloads)}
                db.execute('''INSERT INTO entries
                    (id,backend,model,mode,status,generate_immediately,created_at,updated_at,
                     payload,key_hash,fingerprint,stage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (identity,backend,model,mode,'queued',int(immediate),timestamp,timestamp,
                     json.dumps(payload,allow_nan=False),None,fingerprint,'Waiting for execution slot'))
            db.execute('INSERT INTO candidate_batches VALUES (?,?,?,?,?,?)',
                       (batch_id,backend,timestamp,key_hash,fingerprint,json.dumps(identities)))
            return self._candidate_batch(db, db.execute('SELECT * FROM candidate_batches WHERE id=?',(batch_id,)).fetchone())

    def change(self, identity, **changes):
        allowed = {'status','stage','error','result','started_at','completed_at'}
        if not changes or not set(changes) <= allowed:
            raise ValueError('Unsupported queue update')
        if 'result' in changes:
            changes['result'] = json.dumps(changes['result'],allow_nan=False)
        changes['updated_at'] = now()
        with self.transaction() as db:
            db.execute('UPDATE entries SET '+','.join(f'{k}=?' for k in changes)+' WHERE id=?',
                       (*changes.values(),identity))
            return self.decode(db.execute('SELECT * FROM entries WHERE id=?',(identity,)).fetchone())

    def has_pending(self, backend):
        with self.transaction() as db:
            return db.execute("SELECT 1 FROM entries WHERE status='queued' AND backend=? LIMIT 1",(backend,)).fetchone() is not None

    def claim(self, backend, allow_llm=True):
        with self.transaction() as db:
            rows = db.execute('''SELECT * FROM entries WHERE status='queued' AND backend=?
                ORDER BY generate_immediately DESC,sequence''',(backend,))
            row=None
            for candidate in rows:
                plan=json.loads(candidate['payload']).get('candidate_plan',{})
                if plan.get('method')=='llm' and not plan.get('ready') and not allow_llm:continue
                row=candidate;break
            if row is None:
                return None
            db.execute('''UPDATE entries SET status='running',attempts=attempts+1,
                started_at=?,completed_at=NULL,updated_at=?,stage='Starting',error=NULL WHERE id=?''',
                (now(),now(),row['id']))
            return self.decode(db.execute('SELECT * FROM entries WHERE id=?',(row['id'],)).fetchone())

    def freeze_candidate_variants(self, batch_id, variants):
        """Commit every effective design together before any member synthesizes."""
        with self.transaction() as db:
            batch=self._candidate_batch(db,db.execute('SELECT * FROM candidate_batches WHERE id=?',(batch_id,)).fetchone())
            if len(batch['jobs'])!=len(variants):raise QueueConflict('Diversity plan differs from the admitted candidate count')
            if all(row['payload'].get('candidate_plan',{}).get('ready') for row in batch['jobs']):return
            for row,variant in zip(batch['jobs'],variants):
                if row['status']=='done':raise QueueConflict('Cannot change a completed candidate design')
                payload=row['payload'];payload['effective']['description']=variant['description']
                payload['candidate_variant']=variant;payload['candidate_plan']['ready']=True
                db.execute('UPDATE entries SET payload=?,updated_at=? WHERE id=?',
                           (json.dumps(payload,allow_nan=False),now(),row['id']))

    def fail_candidate_planning(self, batch_id, error):
        with self.transaction() as db:
            batch=self._candidate_batch(db,db.execute('SELECT * FROM candidate_batches WHERE id=?',(batch_id,)).fetchone())
            for row in batch['jobs']:
                if row['status']=='queued':
                    db.execute("UPDATE entries SET status='error',stage='Distinct voice planning failed',error=?,completed_at=?,updated_at=? WHERE id=?",
                               (error,now(),now(),row['id']))

    def interrupted(self):
        with self.transaction() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM entries WHERE status='running' ORDER BY sequence")]

    def remove(self, identity=None, backend=None):
        """Tombstones retain retry keys; a claim and delete cannot both win."""
        with self.transaction() as db:
            if identity is not None:
                row = db.execute('SELECT status FROM entries WHERE id=?',(identity,)).fetchone()
                if row is None:
                    raise FileNotFoundError('Queue entry not found')
                if row['status'] == 'deleted':
                    return 0
                if row['status'] != 'queued':
                    raise QueueConflict('Only pending entries can be deleted; running work is not cancelled')
            clause, args = (" AND id=?",[identity]) if identity else ('',[])
            if backend:
                clause += ' AND backend=?'
                args.append(backend)
            return db.execute("UPDATE entries SET status='deleted',stage='Removed from queue',updated_at=? WHERE status='queued'"+clause,
                              (now(),*args)).rowcount

    def retry(self, identity, backend):
        with self.transaction() as db:
            row = self.decode(db.execute('SELECT * FROM entries WHERE id=?',(identity,)).fetchone())
            if row['backend'] != backend:
                raise QueueConflict('Select this entry\'s backend at startup before retrying')
            if row['status'] != 'error':
                raise QueueConflict('Only failed entries can be retried')
            db.execute("UPDATE entries SET status='queued',stage='Retry queued',error=NULL,started_at=NULL,completed_at=NULL,updated_at=? WHERE id=?",(now(),identity))
            return self.decode(db.execute('SELECT * FROM entries WHERE id=?',(identity,)).fetchone())

    def listing(self, statuses=('queued','running'), backend=None, limit=24, offset=0):
        clauses, args = [], []
        if backend:
            clauses.append('backend=?'); args.append(backend)
        base = ' WHERE '+ ' AND '.join(clauses) if clauses else ''
        with self.transaction() as db:
            counts = {row['status']:row['n'] for row in db.execute('SELECT status,count(*) AS n FROM entries'+base+' GROUP BY status',args)}
            if statuses:
                clauses.append('status IN ('+','.join('?' for _ in statuses)+')'); args.extend(statuses)
            where = ' WHERE '+' AND '.join(clauses) if clauses else ''
            total = db.execute('SELECT count(*) FROM entries'+where,args).fetchone()[0]
            rows = db.execute("SELECT * FROM entries"+where+" ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,generate_immediately DESC,sequence LIMIT ? OFFSET ?",(*args,limit,offset))
            more = offset+limit < total
            return dict(data=[self.decode(row) for row in rows],total=total,counts=counts,
                        limit=limit,offset=offset,has_more=more,next_offset=offset+limit if more else None)
