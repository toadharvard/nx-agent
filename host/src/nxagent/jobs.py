"""Long-running daemon jobs (UMS copies, console updates) that outlive a client connection."""
import threading
import time
import traceback

from .common import OpError


class Jobs:
    def __init__(self, events, keep=20):
        self.events = events
        self.keep = keep
        self.lock = threading.Lock()
        self.items = {}

    def start(self, kind, func, **args):
        with self.lock:
            if any(job['state'] == 'running' for job in self.items.values()):
                raise OpError('busy', 'Another job is running; poll job.status')

            job_id = f'{kind}-{time.time_ns()}'
            job = {'id': job_id, 'kind': kind, 'state': 'running', 'started_ns': time.time_ns(), 'steps': []}
            self.items[job_id] = job

            for old in sorted(self.items)[:-self.keep]:
                self.items.pop(old)

        def step(stage, **fields):
            job['steps'].append({'stage': stage, 'time_ns': time.time_ns(), **fields})
            self.events.emit('job_step', job=job_id, stage=stage, **fields)

        def body():
            try:
                job['result'] = func(step=step, **args)
                job['state'] = 'done'
            except OpError as exc:
                job.update(state='failed', error=exc.to_dict())
            except Exception as exc:
                job.update(state='failed', error={'kind': 'device_error', 'error': str(exc),
                                                  'trace': traceback.format_exc(limit=3)})
            finally:
                job['finished_ns'] = time.time_ns()
                self.events.emit('job_end', job=job_id, state=job['state'])

        threading.Thread(target=body, name=job_id, daemon=True).start()

        return {'job_id': job_id, 'state': 'running'}

    def status(self, job_id=None):
        with self.lock:
            if job_id is None:
                return {'jobs': [dict(job) for job in self.items.values()]}

            if job_id not in self.items:
                raise OpError('invalid', f'Unknown job {job_id}')

            return dict(self.items[job_id])
