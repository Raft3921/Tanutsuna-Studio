"""Serial, low-priority child jobs; never run inference during a recording."""
import os
from pathlib import Path
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, Signal
from cutraft.media_cache import ffmpeg_executable, process_options


def lower_priority():
    if os.name != 'nt':
        try: os.nice(10)
        except OSError: pass


def run_command(args, log, cancel=None, cwd=None):
    options = process_options()
    if os.name == 'nt':
        options['creationflags'] |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
    # Never buffer a whole media stream or unbounded child stderr in Python memory.
    with open(log, 'ab') as output:
        process = subprocess.Popen([str(a) for a in args], stdout=output, stderr=output, cwd=cwd, **options)
        if os.name != 'nt':
            try: os.setpriority(os.PRIO_PROCESS, process.pid, 10)
            except (OSError, AttributeError): pass
        while True:
            try:
                code = process.wait(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    process.terminate()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                    raise RuntimeError('処理を中断しました。元の収録は保持しています。')
        if code:
            raise RuntimeError(f'処理に失敗しました（終了コード {code}）。詳細：{log}')


class Jobs(QObject):
    delivered = Signal(object)
    finished = Signal(object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='Studio-post')
        self.cancel = threading.Event()
        self.busy = False
        self.closed = False
        self.delivered.connect(self.accept)

    def submit(self, tag, function):
        if self.busy:
            raise ValueError('前の処理が終わるまでお待ちください。')
        self.busy = True
        self.cancel.clear()
        def work():
            try: result, error = function(self.cancel), ''
            except Exception as exc: result, error = None, str(exc)
            if not self.closed:
                self.delivered.emit((tag, result, error))
        self.pool.submit(work)

    def accept(self, message):
        self.busy = False
        self.finished.emit(*message)

    def shutdown(self):
        self.closed = True
        self.cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
