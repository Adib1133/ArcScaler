"""Desktop entry point with persistent diagnostics and a single-instance lock."""
import logging
from logging.handlers import RotatingFileHandler
import sys
from contextlib import ExitStack
from core.files import operation_lock, OperationBusy, atomic_write
from core.paths import data_root


def configure_logging():
    log_path = data_root() / 'application.log'
    handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger = logging.getLogger('arcscaler')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return log_path


def main():
    log_path = configure_logging()
    errors = []
    def error(exc_type, exc_value, tb):
        errors.append(str(exc_value))
        logging.getLogger('arcscaler').error('Unhandled error', exc_info=(exc_type, exc_value, tb))
        if '--smoke-test' not in sys.argv:
            from tkinter import messagebox
            messagebox.showerror('ArcScaler', f'{exc_value}\n\nDiagnostics: {log_path}')
    sys.excepthook = error
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ArcScaler.Desktop")
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        with ExitStack() as stack:
            storage = data_root()
            try:
                stack.enter_context(operation_lock(storage))
            except OperationBusy:
                atomic_write(storage / 'activate.request', b'activate')
                return 0
            from gui.main_window import MainWindow
            app = MainWindow()
            app.report_callback_exception = error
            def activate_requested():
                request = storage / 'activate.request'
                try:
                    request.unlink()
                except FileNotFoundError:
                    pass
                else:
                    app.deiconify()
                    app.lift()
                    app.focus_force()
                app.after(200, activate_requested)
            app.after(200, activate_requested)
            if '--smoke-test' in sys.argv:
                app.withdraw()
                # Exercise startup, resource discovery, scheduled callbacks, then clean exit.
                def finish():
                    if app.version_tab.busy:
                        app.after(100, finish)
                    else:
                        app._on_close()
                app.after(500, finish)
            app.mainloop()
    except Exception:
        error(*sys.exc_info())
        return 1
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
