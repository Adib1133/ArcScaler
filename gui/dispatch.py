"""Workers enqueue Python callables; only the Tk thread touches Tk."""
import queue


class UIDispatcher:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.closed = False
        self.timer = root.after(25, self._drain)

    def post(self, callback, *args):
        if not self.closed:
            self.queue.put((callback, args))

    def _drain(self):
        if self.closed:
            return
        for _ in range(100):
            try:
                callback, args = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception:
                import sys
                self.root.report_callback_exception(*sys.exc_info())
        self.timer = self.root.after(25, self._drain)

    def close(self):
        if self.closed:return
        self.closed = True
        self.root.after_cancel(self.timer)
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
