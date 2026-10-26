"""Generator-owned resource history in the existing project SQLite database."""
from contextlib import closing
import logging
import math
import os
import sqlite3
import threading
import time
from uuid import uuid4

from resource_probe import ResourceProbe

WINDOWS = {'5m': 300, '30m': 1800, '1h': 3600, '6h': 21600, '24h': 86400, '3d': 259200, '7d': 604800}
RETENTION_SECONDS = WINDOWS['7d']
METRICS = ('cpu_percent', 'rss_bytes', 'gpu_percent', 'gpu_allocated_bytes', 'gpu_in_use_bytes')
SCOPES = {
    'cpu_percent': 'VoiceGen process tree; 100% equals one CPU core; can exceed 100%. Interval CPU time from ps.',
    'rss_bytes': 'Sum of resident memory for VoiceGen and its descendants, including native/LLM children. Shared pages may be counted more than once.',
    'gpu_percent': 'Device-wide GPU utilization reported by the macOS driver, including other apps. Not exclusive job usage.',
    'gpu_allocated_bytes': 'Device-wide GPU allocation in shared system memory. Not dedicated VRAM capacity; do not add it to RSS.',
    'gpu_in_use_bytes': 'Device-wide in-use GPU system memory reported by the driver; may overlap process RSS.',
}


class MetricsStore:
    def __init__(self, path):
        self.path = path

    def connect(self):
        db = sqlite3.connect(self.path, timeout=.2)
        db.row_factory = sqlite3.Row
        return db

    def initialize(self):
        # The generator already owns queue recovery. Never modify queue tables here.
        with closing(self.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS resource_sessions (
                    id TEXT PRIMARY KEY, backend TEXT NOT NULL, model TEXT NOT NULL,
                    pid INTEGER NOT NULL, started_at REAL NOT NULL, ended_at REAL);
                CREATE TABLE IF NOT EXISTS resource_runs (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    backend TEXT NOT NULL, model TEXT NOT NULL, attempt INTEGER NOT NULL,
                    started_at REAL NOT NULL, ended_at REAL, status TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS resource_runs_job ON resource_runs(job_id, started_at);
                CREATE INDEX IF NOT EXISTS resource_runs_time ON resource_runs(started_at);
                CREATE TABLE IF NOT EXISTS resource_samples (
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, session_id TEXT NOT NULL,
                    run_id TEXT, job_id TEXT, backend TEXT NOT NULL, model TEXT NOT NULL, stage TEXT NOT NULL,
                    cpu_percent REAL, rss_bytes INTEGER, gpu_percent REAL,
                    gpu_allocated_bytes INTEGER, gpu_in_use_bytes INTEGER);
                CREATE INDEX IF NOT EXISTS resource_samples_time ON resource_samples(ts);
                CREATE INDEX IF NOT EXISTS resource_samples_job ON resource_samples(job_id, ts);
                CREATE INDEX IF NOT EXISTS resource_samples_run ON resource_samples(run_id, ts);
                CREATE INDEX IF NOT EXISTS resource_samples_model ON resource_samples(backend, ts);
            ''')

    def recover(self, now):
        with closing(self.connect()) as db, db:
            db.execute("""UPDATE resource_runs SET status='interrupted', ended_at=COALESCE(
                (SELECT MAX(ts) FROM resource_samples WHERE run_id=resource_runs.id),started_at)
                WHERE ended_at IS NULL""")
            db.execute('UPDATE resource_sessions SET ended_at=? WHERE ended_at IS NULL', (now,))

    def session(self, identity, backend, model, now):
        with closing(self.connect()) as db, db:
            db.execute('INSERT OR IGNORE INTO resource_sessions VALUES(?,?,?,?,?,NULL)', (identity, backend, model, os.getpid(), now))

    def start_run(self, context):
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO resource_runs VALUES(?,?,?,?,?,?,?,?,?)',
                       tuple(context[key] for key in ('id','job_id','session_id','backend','model','attempt','started_at')) + (None,'running'))

    def finish_run(self, identity, status, now):
        with closing(self.connect()) as db, db:
            db.execute('UPDATE resource_runs SET status=?,ended_at=? WHERE id=?', (status, now, identity))

    def insert(self, sample):
        keys = ('ts','session_id','run_id','job_id','backend','model','stage', *METRICS)
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO resource_samples ('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',
                       tuple(sample.get(key) for key in keys))

    def prune(self, now):
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM resource_samples WHERE ts<?', (now-RETENTION_SECONDS,))
            db.execute('DELETE FROM resource_runs WHERE ended_at<?', (now-RETENTION_SECONDS,))
            db.execute('DELETE FROM resource_sessions WHERE ended_at<?', (now-RETENTION_SECONDS,))

    def series(self, start, end, backend=None, job_id=None, run_id=None):
        where, args = ['ts>=?', 'ts<=?'], [start, end]
        if backend:
            where.append('backend=?'); args.append(backend)
        if job_id:
            where.append('job_id=?'); args.append(job_id)
        if run_id:
            where.append('run_id=?'); args.append(run_id)
        clause = ' AND '.join(where)
        # Bound responses; averages and maxima preserve spikes in longer windows.
        bucket = max(1, math.ceil((end-start)/360))
        metrics = ','.join(f'AVG({key}) AS {key},MAX({key}) AS peak_{key}' for key in METRICS)
        grouping = 'session_id,backend,model,stage'+(',run_id' if job_id else '')
        with closing(self.connect()) as db:
            summary = dict(db.execute('SELECT COUNT(*) AS samples,' +
                ','.join(f'MAX({key}) AS peak_{key}' for key in METRICS) + ' FROM resource_samples WHERE '+clause,args).fetchone())
            rows = [dict(row) for row in db.execute('SELECT MIN(ts) AS ts,MAX(ts) AS last_ts,'+grouping+
                ',COUNT(*) AS samples,'+metrics+',CAST((ts-?)/? AS INTEGER) AS bucket FROM resource_samples WHERE '+clause+
                ' GROUP BY '+grouping+',bucket ORDER BY ts DESC LIMIT 5001', [start,bucket,*args])]
            models = [dict(row) for row in db.execute('SELECT DISTINCT backend,model FROM resource_samples WHERE '+clause+' ORDER BY backend,model', args)]
        truncated = len(rows)>5000
        return {'start': start, 'end': end, 'bucket_seconds': bucket, 'summary': summary,
                'points': list(reversed(rows[:5000])), 'models': models, 'truncated': truncated}

    def jobs(self, backend=None, limit=25, offset=0, job_id=None):
        where, args = [], []
        if backend: where.append('r.backend=?'); args.append(backend)
        if job_id: where.append('r.job_id=?'); args.append(job_id)
        clause = ' WHERE '+' AND '.join(where) if where else ''
        with closing(self.connect()) as db:
            total = db.execute('SELECT COUNT(*) FROM resource_runs r'+clause,args).fetchone()[0]
            rows = [dict(row) for row in db.execute('SELECT r.*, (SELECT COUNT(*) FROM resource_samples s WHERE s.run_id=r.id) AS samples,'+
                ','.join(f'(SELECT MAX({key}) FROM resource_samples s WHERE s.run_id=r.id) AS peak_{key}' for key in METRICS)+
                ' FROM resource_runs r'+clause+' ORDER BY r.started_at DESC LIMIT ? OFFSET ?', [*args,limit,offset])]
        return {'data':rows, 'total':total, 'offset':offset, 'limit':limit, 'has_more':offset+len(rows)<total}


class ResourceMonitor:
    def __init__(self, path, backend, model, state, probe=None):
        self.store = MetricsStore(path)
        self.backend, self.model, self.state = backend, model, state
        self.probe = probe or ResourceProbe()
        self.session_id = uuid4().hex
        self.context = None
        self.lock = threading.Lock()
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.thread = None
        self.last_sample_at, self.last_error, self.probe_errors = None, None, []
        self.dropped_samples = 0
        self.last_prune = 0
        self.initialized = False
        self.pending_finishes = []

    def status(self):
        context=self.context
        return {'running':bool(self.thread and self.thread.is_alive()), 'last_sample_at':self.last_sample_at,
                'storage_error':self.last_error, 'counter_notices':self.probe_errors,
                'dropped_samples':self.dropped_samples, 'session_id':self.session_id,
                'active_job':context['job_id'] if context else None}

    def start(self):
        self._initialize()
        self.thread=threading.Thread(target=self._loop,name='voice-resources',daemon=True)
        self.thread.start()

    def _initialize(self):
        try:
            self.store.initialize()
            self.store.recover(time.time())
            self.store.session(self.session_id,self.backend,self.model,time.time())
            self.initialized=True
        except Exception:
            self.last_error='Resource history could not be initialized; check project SQLite storage.'
            logging.exception(self.last_error)

    def _capture(self):
        try:
            if not self.initialized:
                self._initialize()
                if not self.initialized:return
            self._flush_finishes()
            if self.context and not self.context.get('recorded'):
                self.store.start_run(self.context)
                self.context['recorded']=True
            metrics, self.probe_errors = self.probe.sample()
            at = time.time()
            context = self.context or {}
            phase=self.state()
            model=context.get('model',self.model)
            if phase=='expanding':
                from prompt_assets import REPOSITORY
                model=REPOSITORY
            self.store.insert({'ts':at,'session_id':self.session_id,'run_id':context.get('id'),
                'job_id':context.get('job_id'),'backend':self.backend,'model':model,
                'stage':phase, **metrics})
            self.last_sample_at, self.last_error = at, None
            if at-self.last_prune>=300:
                try:self.store.prune(at);self.last_prune=at
                except sqlite3.Error:logging.warning('Resource history cleanup delayed; retrying on the next sample')
        except Exception:
            self.dropped_samples+=1
            first_failure=self.last_error is None
            self.last_error='Resource history write failed; generation can continue. Check project SQLite storage.'
            if first_failure:logging.warning(self.last_error,exc_info=True)

    def _loop(self):
        while not self.stop_event.is_set():
            with self.lock:self._capture()
            self.wake.wait(1 if self.context else 5)
            self.wake.clear()

    def job_started(self, job):
        with self.lock:
            self.context={'id':uuid4().hex,'job_id':job['id'],'session_id':self.session_id,'backend':self.backend,
                          'model':job['model'],'attempt':job['attempts'],'started_at':time.time()}
            self._capture()
        self.wake.set()

    def job_finished(self, status):
        with self.lock:
            if not self.context:return
            try:
                self._capture()
                self.pending_finishes.append((self.context['id'],status,time.time()))
                self._flush_finishes()
            except Exception:
                self.last_error='Job resource summary is waiting for SQLite storage; it will be retried.'
                logging.warning(self.last_error,exc_info=True)
            finally:self.context=None
        self.wake.set()

    def _flush_finishes(self):
        while self.pending_finishes:
            self.store.finish_run(*self.pending_finishes[0])
            self.pending_finishes.pop(0)

    def close(self):
        self.stop_event.set();self.wake.set()
        if self.thread:self.thread.join(timeout=5)
        try:
            self._flush_finishes()
            with closing(self.store.connect()) as db, db:
                db.execute('UPDATE resource_sessions SET ended_at=? WHERE id=?',(time.time(),self.session_id))
        except Exception:logging.warning('Could not record resource session end',exc_info=True)
