"""Sparse lifecycle history, separate from the seven-day sampled resource curves."""
from contextlib import closing
import math

LIFECYCLE_WINDOWS = {'24h':86400, '7d':604800, '30d':2592000, '90d':7776000, 'all':None}
LIFECYCLE_RETENTION = 90*86400
LIFECYCLE_SCOPES = {
    'runtime_process':'Backend process launch to ready IPC, or close request to verified process-group exit. Includes imports/teardown/reaping; not weight-only time.',
    'weights':'Python model loader/validation, or release of model references, garbage collection and allocator cache. Lazy compilation/first inference is excluded.',
    'native_weights':'Native registry model load plus task-session construction. Excludes input preparation and synthesis.',
    'native_release':'Native task-session and model destruction before CLI process exit. Excludes earlier synthesis and later process teardown.',
    'llm_weights':'Prompt LLM loader, or explicit release/garbage collection/cache clearing. Excludes prompt generation.',
}


class LifecycleHistory:
    def initialize_lifecycle(self, db):
        db.executescript('''
            CREATE TABLE IF NOT EXISTS model_lifecycle_events (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT, job_id TEXT, attempt INTEGER,
                backend TEXT NOT NULL, model TEXT NOT NULL, action TEXT NOT NULL, scope TEXT NOT NULL,
                reason TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, duration_seconds REAL, status TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS model_lifecycle_time ON model_lifecycle_events(started_at);
            CREATE INDEX IF NOT EXISTS model_lifecycle_job ON model_lifecycle_events(job_id,run_id);
            CREATE INDEX IF NOT EXISTS model_lifecycle_model ON model_lifecycle_events(backend,started_at);
        ''')

    def lifecycle_event(self, event):
        keys = ('id','session_id','run_id','job_id','attempt','backend','model','action','scope','reason',
                'started_at','ended_at','duration_seconds','status')
        if event['action'] not in ('load','unload') or event['scope'] not in LIFECYCLE_SCOPES:
            raise ValueError('Unknown lifecycle boundary')
        if event['status'] not in ('running','success','error','interrupted'):
            raise ValueError('Unknown lifecycle status')
        for field in ('started_at','ended_at','duration_seconds'):
            if event.get(field) is not None and not math.isfinite(event[field]): raise ValueError('Invalid timing')
        if event.get('duration_seconds') is not None and event['duration_seconds'] < 0: raise ValueError('Invalid duration')
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO model_lifecycle_events ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+') '
                       'ON CONFLICT(id) DO UPDATE SET ended_at=excluded.ended_at,duration_seconds=excluded.duration_seconds,status=excluded.status '
                       "WHERE model_lifecycle_events.status='running'", tuple(event.get(key) for key in keys))

    def lifecycle(self, start=None, end=None, backend=None, action=None, scope=None, job_id=None, run_id=None, limit=25, offset=0):
        where, args = [], []
        for clause, value in (('started_at>=?',start),('started_at<=?',end),('backend=?',backend),('action=?',action),
                              ('scope=?',scope),('job_id=?',job_id),('run_id=?',run_id)):
            if value is not None: where.append(clause); args.append(value)
        clause = ' WHERE '+' AND '.join(where) if where else ''
        with closing(self.connect()) as db:
            counts = {row['status']:row['count'] for row in db.execute('SELECT status,COUNT(*) AS count FROM model_lifecycle_events'+clause+' GROUP BY status', args)}
            rows = [dict(row) for row in db.execute('SELECT * FROM model_lifecycle_events'+clause+' ORDER BY started_at DESC,id DESC LIMIT ? OFFSET ?', [*args,limit,offset])]
            summaries = [dict(row) for row in db.execute('SELECT backend,model,action,scope,COUNT(*) AS count,AVG(duration_seconds) AS mean_seconds,MAX(duration_seconds) AS max_seconds '
                         'FROM model_lifecycle_events'+clause+(' AND ' if where else ' WHERE ')+"status='success' AND duration_seconds IS NOT NULL GROUP BY backend,model,action,scope ORDER BY backend,model,scope,action", args)]
        total=sum(counts.values())
        return dict(data=rows,total=total,counts=counts,summaries=summaries,offset=offset,limit=limit,has_more=offset+len(rows)<total)
