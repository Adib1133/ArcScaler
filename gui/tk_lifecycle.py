"""Keep cyclic collection of Tk objects on the UI thread while a window is alive."""
import gc
import threading

_users = 0
_previous = True


class TkCollection:
    def __init__(self, root):
        global _users, _previous
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('Tk windows must be created on the main thread.')
        if not _users:
            _previous = gc.isenabled()
            gc.disable()
        _users += 1
        self.root, self.closed = root, False
        self.timer = root.after(1000, self.collect)

    def collect(self):
        if self.closed: return
        gc.collect()
        self.timer = self.root.after(1000, self.collect)

    def close(self):
        global _users
        if self.closed: return
        self.closed = True
        try: self.root.after_cancel(self.timer)
        except Exception: pass
        gc.collect()
        _users -= 1
        if not _users and _previous: gc.enable()
