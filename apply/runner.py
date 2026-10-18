"""Drains queued auto-apply rows on the owner's laptop: `python -m apply.runner`.

Claim a row, build its bundle, run the fenced claude worker in the bundle dir, parse
its answer and write the outcome back. Any failure on the way becomes a `failed`
row with a short reason, so a job is never left `running`.
"""
import argparse
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
from typing import Optional

import config
from apply import bundle, worker
from apply.worker import WorkerResult
from db import supabase_utils

log = logging.getLogger(__name__)

_DETAIL_MAX = 500  # auto_apply_reason is shown in the UI; keep it to a log tail


def _failed(detail: str, cost: Optional[float] = None) -> WorkerResult:
    return WorkerResult("failed", None, (detail or "")[-_DETAIL_MAX:], cost)


def _kill_tree(proc) -> None:
    """Kill claude and everything it spawned (npx, node, Chromium).

    proc.kill() on Windows only ends the top process and orphans the browser tree,
    so taskkill /T takes the whole tree."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def _run_worker(cmd: list, prompt: str, cwd: str, timeout: float):
    """Run claude with the prompt on stdin; returns (returncode, stdout, stderr).

    Popen rather than run(): run()'s post-kill communicate can block forever while
    grandchildren hold the pipes open. On timeout the tree is killed and output is
    drained with a short bound, then TimeoutExpired is re-raised."""
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            cwd=cwd, creationflags=flags,
                            env=worker.scrubbed_env())
    try:
        out, err = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass  # give up on the output; the row is failed anyway
        raise
    return proc.returncode, out, err


def run_one(job: dict, args) -> WorkerResult:
    """Run the worker for one claimed job. Never raises: errors become `failed`."""
    stop = getattr(args, "stop_event", None)
    try:
        full = supabase_utils.get_job_for_auto_apply(job["job_id"])
        if not full:
            return _failed("job row not found")
        bundle_dir = bundle.build(full, config.AUTO_APPLY_WORK_DIR)
        mcp = worker.write_mcp_config(bundle_dir, bundle.start_url(full),
                                      full.get("ats_type"), args.headless)
        try:
            cmd = worker.build_command(bundle_dir, mcp, args.model)
        except ValueError as e:
            # Would fail every job the same way, so stop the other claim loops too.
            print(f"Cannot start worker: {e}")
            if stop is not None:
                stop.set()
            return _failed(str(e))
        # Prompt on stdin, not argv. cwd must be the bundle: Claude auto-allows Read
        # there and Playwright file uploads only work from it.
        returncode, stdout, stderr = _run_worker(
            cmd, worker.prompt_text(), bundle_dir, config.AUTO_APPLY_TIMEOUT_MINUTES * 60)
        # claude exits non-zero on max-turns yet still prints JSON, so always parse.
        result = worker.parse_output(stdout, full.get("ats_type"), bundle.start_url(full))
        if result.status == "failed" and not (result.detail or "").strip():
            result = _failed(f"claude exited {returncode}: {(stderr or '')[-400:]}",
                             result.cost_usd)
        return result
    except subprocess.TimeoutExpired:
        return _failed(f"timed out after {config.AUTO_APPLY_TIMEOUT_MINUTES} min")
    except bundle.BundleError as e:
        return _failed(str(e))
    except OSError as e:
        return _failed(f"could not run claude: {e}")
    except Exception as e:  # noqa: BLE001 - the row must not stay `running`
        log.exception("auto-apply crashed for %s", job.get("job_id"))
        return _failed(f"{type(e).__name__}: {e}")


def _join_all(threads: list) -> None:
    for t in threads:
        t.join()


def _claude_available() -> bool:
    return bool(shutil.which(config.CLAUDE_BIN) or os.path.isfile(config.CLAUDE_BIN))


def _dry_run(args) -> int:
    """List queued rows and show the command shape; claims, writes and runs nothing."""
    rows = supabase_utils.get_queued_auto_apply()
    print(f"{len(rows)} queued job(s)")
    for r in rows:
        print(f"  {r['job_id']} {r.get('company')} {r.get('ats_type')} {r.get('apply_url')}")
    if rows:
        bdir = os.path.join(config.AUTO_APPLY_WORK_DIR, str(rows[0]["job_id"]))
        try:
            cmd = worker.build_command(bdir, os.path.join(bdir, "mcp.json"), args.model)
            print("Command for first job (prompt goes on stdin):")
            print("  " + " ".join(cmd))
        except ValueError as e:
            print(f"Cannot build command: {e}")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="apply.runner",
                                 description="Run queued auto-apply jobs.")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--max", type=int, default=None, help="max jobs to process in total")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--headless", action="store_true", help="run the browser headless")
    args = ap.parse_args(argv)
    args.stop_event = threading.Event()

    if not _claude_available():
        print("Claude Code CLI not found; set CLAUDE_BIN")
        return 2
    if args.dry_run:
        return _dry_run(args)

    reset = supabase_utils.reset_stale_auto_apply(config.AUTO_APPLY_TIMEOUT_MINUTES + 5)
    print(f"Reset {reset} stale running job(s)")

    lock = threading.Lock()
    taken = [0]
    done = []  # (job, result)
    host, pid = socket.gethostname(), os.getpid()

    def loop(n: int):
        name = f"{host}:{pid}:{n}"
        while not args.stop_event.is_set():
            with lock:  # reserve a slot before claiming so --max is never exceeded
                if args.max is not None and taken[0] >= args.max:
                    return
                taken[0] += 1
            job = supabase_utils.claim_next_auto_apply(worker=name)
            if job is None:
                with lock:
                    taken[0] -= 1
                return
            result = run_one(job, args)
            # Every write is capped, not just failures: the reason is shown in the UI.
            saved = supabase_utils.set_auto_apply_result(
                job["job_id"], result.status, (result.detail or "")[:_DETAIL_MAX],
                result.cost_usd)
            if not saved:
                log.error("could not write auto-apply result for %s", job["job_id"])
            with lock:
                done.append((job, result, saved))

    interrupted = False
    threads = [threading.Thread(target=loop, args=(i,)) for i in range(max(1, args.parallel))]
    for t in threads:
        t.start()
    try:
        _join_all(threads)
    except KeyboardInterrupt:
        # Threads are non-daemon; without this they would keep claiming jobs.
        args.stop_event.set()
        print("Stopping after the jobs in progress; run again to continue.")
        _join_all(threads)
        interrupted = True

    total = 0.0
    for job, r, saved in done:
        cost = r.cost_usd or 0.0
        total += cost
        print(f"{job['job_id']} {job.get('company')} {r.status} ${cost:.2f}"
              + ("" if saved else " (not saved)"))
    print(f"Total cost: ${total:.2f} over {len(done)} jobs")
    return 1 if interrupted or any(r.status == "failed" or not saved
                                   for _, r, saved in done) else 0


if __name__ == "__main__":
    raise SystemExit(main())
