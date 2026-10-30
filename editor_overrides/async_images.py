"""One image worker, bounded caches and one latest request per preview layer.

Only QImage/immutable snapshots cross the worker boundary; widgets stay on Qt's
main thread. Playback can drop intermediate requests instead of queuing frames.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, Signal


class AsyncImages(QObject):
    result = Signal(object)
    changed = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='Studio-preview')
        self.cache, self.latest, self.pending = OrderedDict(), OrderedDict(), OrderedDict()
        self.running = None
        self.generation = 0
        self.closed = False
        self.result.connect(self.accept)

    def request(self, slot, key, operation):
        if self.closed:
            return None
        if key in self.cache:
            self.cache.move_to_end(key)
            self.pending.pop(slot, None)
            return self.cache[key]
        if self.running != (self.generation, slot, key):
            self.pending[slot] = (key, operation)
            while len(self.pending) > 16:
                self.pending.popitem(last=False)
            self.start_next()
        return self.latest.get(slot)

    def start_next(self):
        if self.closed or self.running is not None or not self.pending:
            return
        slot, (key, operation) = self.pending.popitem(last=False)
        job = self.running = (self.generation, slot, key)
        def work():
            try:
                value, error = operation(), ''
            except Exception as exc:
                value, error = None, str(exc)
            self.result.emit((job, value, error))
        self.pool.submit(work)

    def accept(self, message):
        job, value, error = message
        self.running = None
        generation, slot, key = job
        if not self.closed and generation == self.generation:
            if error:
                self.failed.emit(error)
            else:
                self.cache[key] = value
                self.latest[slot] = value
                self.latest.move_to_end(slot)
                while len(self.cache) > 24:
                    self.cache.popitem(last=False)
                while len(self.latest) > 16:
                    self.latest.popitem(last=False)
                # Don't run the same key again when paint queued it during completion.
                if self.pending.get(slot, (None,))[0] == key:
                    self.pending.pop(slot)
                self.changed.emit()
        self.start_next()

    def clear(self):
        self.generation += 1
        self.pending.clear()
        self.cache.clear()
        self.latest.clear()

    def shutdown(self):
        self.closed = True
        self.clear()
        self.pool.shutdown(wait=True, cancel_futures=True)
