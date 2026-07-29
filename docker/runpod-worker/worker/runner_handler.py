"""Serverless code-runner handler — the RunPod side of execute-code.

Receives {"input": {"code": "...", "workdir": "/data/sessions/<sid>"}},
subprocess-runs the code and yields stdout/stderr lines as they appear so
the backend can stream them via GET /stream/{job_id}. The final yield
carries the process returncode. `return_aggregate_stream=True` keeps the
full output available on /status for late readers.

Job-level timeouts come from the submit payload's policy.executionTimeout
(RunPod kills the worker; the backend maps status TIMED_OUT onto its
sandbox-timeout handling), so no timeout logic lives here.
"""

import os
import queue
import subprocess
import tempfile
import threading

import runpod


def handler(job):
    inp = job.get("input") or {}
    code = inp.get("code") or ""
    workdir = inp.get("workdir") or "/data"
    os.makedirs(workdir, exist_ok=True)

    # Stage the code in a temp file on the ephemeral container disk and
    # exec it through a tiny -c bootstrap. Passing the script itself via
    # `python -c <code>` puts it on argv, and execve's ARG_MAX (~2 MB,
    # shared with the environment) makes Popen raise "Argument list too
    # long" for the multi-MB scripts the backend otherwise accepts. The
    # bootstrap keeps `python -c` semantics (sys.path[0] = cwd, argv[0] =
    # "-c") so agent code behaves exactly as on the Modal path.
    fd, code_path = tempfile.mkstemp(suffix=".py", prefix="runpod-job-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(code)
        bootstrap = (
            f"exec(compile(open({code_path!r}, 'rb').read(), "
            f"{code_path!r}, 'exec'))"
        )

        proc = subprocess.Popen(
            ["python", "-u", "-c", bootstrap],
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        q: queue.Queue = queue.Queue()

        def pump(stream, name):
            for line in iter(stream.readline, ""):
                q.put({"stream": name, "text": line})
            stream.close()
            q.put({"eof": name})

        for stream, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
            threading.Thread(target=pump, args=(stream, name), daemon=True).start()

        eofs = 0
        while eofs < 2:
            item = q.get()
            if "eof" in item:
                eofs += 1
                continue
            yield item

        proc.wait()
        yield {"returncode": proc.returncode}
    finally:
        try:
            os.unlink(code_path)
        except OSError:
            pass


runpod.serverless.start({"handler": handler, "return_aggregate_stream": True})
