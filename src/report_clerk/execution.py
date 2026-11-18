"""Run potentially blocking report work in a process with a hard deadline."""
import multiprocessing
import os
import pickle
import signal
import tempfile
import traceback
from pathlib import Path


def _worker(path, fn, args, kwargs):
    if os.name == 'posix':
        os.setsid()  # Claude and its descendants share this worker's process group.
    try:
        result = (True, fn(*args, **kwargs))
    except BaseException as error:
        result = (False, type(error).__name__, str(error), traceback.format_exc())
    with open(path, 'wb') as output:
        pickle.dump(result, output)


def _stop(process):
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            if process.is_alive():
                process.kill()
    elif process.is_alive():
        process.kill()
    process.join(5)


def execute(fn, *args, timeout: float, **kwargs):
    # Spawn avoids inheriting locks or SDK state from the parent.
    context = multiprocessing.get_context('spawn')
    with tempfile.TemporaryDirectory(prefix='report-stage-') as directory:
        path = Path(directory) / 'result'
        process = context.Process(target=_worker, args=(path, fn, args, kwargs))
        process.start()
        try:
            process.join(timeout)
            if process.is_alive():
                raise TimeoutError(f'{fn.__name__} exceeded {timeout:g}s deadline')
            if not path.is_file():
                raise RuntimeError(f'{fn.__name__} worker exited without a result (exit {process.exitcode})')
            with path.open('rb') as result_file:
                result = pickle.load(result_file)  # Only this trusted worker writes this private file.
            if not result[0]:
                raise RuntimeError(f'{result[1]}: {result[2]}\n{result[3]}')
            return result[1]
        finally:
            _stop(process)  # Also remove descendants left behind after a successful worker exit.
            process.close()
