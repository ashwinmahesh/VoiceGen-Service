"""Private SQLite queue. All admissions, claims and removals are transactions.

One owning JobManager holds worker.lock before recovery. SQLite is deliberately
in rollback-journal/FULL mode for portable local disks (including exFAT).
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from config import validate_mount
from voice_catalog import now


class QueueConflict(ValueError):
    pass


class QueueStore:
    def __init__(self, root):
        self.path = Path(root)/'jobs/queue.sqlite3'

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

    def existing(self, key_hash, fingerprint):
        if not key_hash:
            return None
        with self.transaction() as db:
            row = db.execute('SELECT * FROM entries WHERE key_hash=?', (key_hash,)).fetchone()
            if row is None:
                return None
            if row['fingerprint'] != fingerprint:
                raise QueueConflict('This idempotency key was used with different settings')
            return self.decode(row)

    def admit(self, backend, model, mode, immediate, payload, key_hash, fingerprint):
        timestamp = now()
        with self.transaction() as db:
            if key_hash:
                old = db.execute('SELECT * FROM entries WHERE key_hash=?', (key_hash,)).fetchone()
                if old:
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

    def claim(self, backend):
        with self.transaction() as db:
            row = db.execute('''SELECT * FROM entries WHERE status='queued' AND backend=?
                ORDER BY generate_immediately DESC,sequence LIMIT 1''',(backend,)).fetchone()
            if row is None:
                return None
            db.execute('''UPDATE entries SET status='running',attempts=attempts+1,
                started_at=?,completed_at=NULL,updated_at=?,stage='Starting',error=NULL WHERE id=?''',
                (now(),now(),row['id']))
            return self.decode(db.execute('SELECT * FROM entries WHERE id=?',(row['id'],)).fetchone())

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
