"""Bounded daemon workers, cooperative cancellation, and keyed result delivery."""
from collections import OrderedDict
from contextvars import ContextVar
from concurrent.futures import CancelledError
import threading

_cancel=ContextVar('task_cancel',default=None)


def checkpoint():
    event=_cancel.get()
    if event is not None and event.is_set():raise CancelledError('Operation cancelled')


class TaskManager:
    def __init__(self, dispatcher, workers=3, capacity=32):
        self.dispatcher=dispatcher;self.capacity=capacity
        self.condition=threading.Condition();self.pending=OrderedDict();self.active={};self.latest={};self.closed=False
        self.threads=[threading.Thread(target=self._worker,daemon=True,name=f'ArcScaler-worker-{i}') for i in range(workers)]
        for thread in self.threads:thread.start()

    def submit(self,key,operation,done,failed,cancellable=True):
        with self.condition:
            if self.closed:raise RuntimeError('Task manager is closed')
            self.cancel(key)
            if len(self.pending)>=self.capacity:raise RuntimeError('Background queue is full; try again shortly.')
            item=(threading.Event(),operation,done,failed,cancellable)
            self.latest[key]=item
            self.pending[key]=item;self.condition.notify()
            return item[0]

    def cancel(self,key):
        with self.condition:
            latest=self.latest.get(key)
            if latest and latest[4]:latest[0].set()
            pending=self.pending.get(key)
            if pending and pending[4]:pending[0].set();self.pending.pop(key,None)
            active=self.active.get(key)
            if active and active[4]:active[0].set()

    def _worker(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda:self.pending or self.closed)
                if not self.pending:return
                key,item=self.pending.popitem(last=False);self.active[key]=item
            event,operation,done,failed,_=item
            token=_cancel.set(event)
            try:
                checkpoint();result=operation();checkpoint()
                self.dispatcher.post(self._deliver,event,done,result)
            except CancelledError:pass
            except Exception as exc:self.dispatcher.post(self._deliver,event,failed,str(exc))
            finally:
                _cancel.reset(token)
                with self.condition:
                    if self.active.get(key) is item:self.active.pop(key,None)

    def _deliver(self,event,callback,value):
        if not self.closed and not event.is_set():callback(value)

    def close(self):
        with self.condition:
            self.closed=True
            for key in list(self.pending):self.cancel(key)
            for item in self.active.values():
                if item[4]:item[0].set()
            self.condition.notify_all()
