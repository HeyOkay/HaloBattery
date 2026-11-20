"""Measure the standalone native transport modules; never import app/providers.

Each repeat is a controlled measurement child, not a production worker process.
Its baseline warms the standard-library dependencies already used by the app,
including the measurement thread/API, before importing the native transport modules.
No working-set trimming, forced collection, or thread-stack adjustments.
"""
from __future__ import annotations

import argparse
import collections
import ctypes
from ctypes import wintypes as W
import dataclasses
import json
import logging
import logging.handlers
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import typing
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREWARMED_STDLIB = ["collections", "ctypes", "ctypes.wintypes", "dataclasses", "json",
                    "logging", "logging.handlers", "os", "pathlib", "queue", "subprocess",
                    "sys", "tempfile", "threading", "time", "traceback", "typing", "uuid"]


class MemoryCounters:
    """Windows private working set and commit, without launching a monitor."""

    class Counters(ctypes.Structure):
        _fields_ = [("cb", W.DWORD), ("PageFaultCount", W.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage", "PrivateUsage", "PrivateWorkingSetSize")
        ] + [("SharedCommitUsage", ctypes.c_uint64)]

    def __init__(self):
        if os.name != "nt":
            raise OSError("memory audit requires Windows PROCESS_MEMORY_COUNTERS_EX2")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetCurrentProcess.argtypes = []
        self.kernel.GetCurrentProcess.restype = W.HANDLE
        self.kernel.K32GetProcessMemoryInfo.argtypes = [
            W.HANDLE, ctypes.c_void_p, W.DWORD]
        self.kernel.K32GetProcessMemoryInfo.restype = W.BOOL
        self.kernel.GetProcessHandleCount.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
        self.kernel.GetProcessHandleCount.restype = W.BOOL
        self.handle_count = W.DWORD()
        self.kernel.GetModuleHandleW.argtypes = [W.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = W.HMODULE
        self.process = self.kernel.GetCurrentProcess()
        self.counters = self.Counters()
        self.counters.cb = ctypes.sizeof(self.Counters)

    def sample(self):
        if not self.kernel.K32GetProcessMemoryInfo(
                self.process, ctypes.byref(self.counters), ctypes.sizeof(self.counters)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.counters.PrivateWorkingSetSize:
            raise RuntimeError("PROCESS_MEMORY_COUNTERS_EX2 private working set unavailable")
        if not self.kernel.GetProcessHandleCount(self.process, ctypes.byref(self.handle_count)):
            raise ctypes.WinError(ctypes.get_last_error())
        return dict(private_working_set=int(self.counters.PrivateWorkingSetSize),
                    private_commit=int(self.counters.PrivateUsage),
                    private_commit_lifetime_peak=int(self.counters.PeakPagefileUsage),
                    working_set=int(self.counters.WorkingSetSize),
                    handle_count=int(self.handle_count.value))

    def rtss_injected(self):
        return bool(self.kernel.GetModuleHandleW("RTSSHooks64.dll"))


class PeakSampler:
    """Constant-sized peak state; its monitor thread is warmed before baseline."""

    def __init__(self, counters):
        self.counters = counters
        self.lock = threading.Lock()
        self.peak = dict(private_working_set=0, private_commit=0,
                         private_commit_lifetime_peak=0, working_set=0, handle_count=0)
        self.samples = 0
        self.failure = None
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="memory-audit-only")
        self.thread.start()

    def _run(self):
        while not self.stopped.is_set():
            try:
                self.capture()
            except Exception as exc:
                self.failure = str(exc)
                return
            self.stopped.wait(0.005)

    def capture(self):
        with self.lock:
            sample = self.counters.sample()
            for field, value in sample.items():
                self.peak[field] = max(self.peak[field], value)
            self.samples += 1
            return sample

    def reset(self):
        with self.lock:
            self.peak = self.counters.sample()
            self.samples = 0

    def close(self):
        self.capture()
        self.stopped.set()
        self.thread.join(1)
        if self.failure:
            raise RuntimeError(self.failure)


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("prototype fixture did not settle")
        time.sleep(0.001)


def lifecycle_child():
    from halo.io_scheduler import IOScheduler
    from tools.prototypes.native_io_fixtures import BlockingWin32StartOperation
    scheduler = IOScheduler()
    operations = [BlockingWin32StartOperation(), BlockingWin32StartOperation()]
    for index, operation in enumerate(operations):
        if scheduler.submit(f"blocked-{index}", lambda op=operation: op, timeout=0.02) is None:
            raise AssertionError("lifecycle fixture admission failed")
    _wait(lambda: all(operation.started.is_set() for operation in operations))
    before = time.monotonic()
    closed = scheduler.close(timeout=0.1)
    elapsed = time.monotonic() - before
    # Neither event was signalled: both daemon issue workers are still genuinely
    # inside WaitForSingleObject(INFINITE), a WinDLL call that releases the GIL.
    report = dict(kind="lifecycle", runtime_executable=sys.executable, pid=os.getpid(),
                  close_elapsed_seconds=elapsed, close_returned=closed,
                  native_waits_still_uncompleted=all(
                      not operation.completed.is_set() for operation in operations),
                  stats=scheduler.stats(), production_children=0)
    print(json.dumps(report), flush=True)
    return 0


def measure_child(cycles=750):
    counters = MemoryCounters()
    sampler = PeakSampler(counters)
    _wait(lambda: sampler.samples >= 10)
    baseline_modules = set(sys.modules)
    baseline_threads = threading.active_count()
    baseline = sampler.capture()
    baseline_rtss = counters.rtss_injected()
    sampler.reset()
    phases = []

    def phase(name, **extra):
        phases.append(dict(name=name, memory=sampler.capture(), **extra))

    from halo.native_io import WindowsApi, MAX_BUFFER
    from halo.io_scheduler import IOScheduler
    from tools.prototypes.native_io_fixtures import (
        DelayedUncancellableOperation, NamedPipePair, wait_completion,
    )
    phase("after_first_prototype_import")
    api = WindowsApi()
    scheduler = IOScheduler(max_devices=16)
    pairs = [NamedPipePair() for _ in range(16)]
    proven_successful_native_operations = 0
    cancelled = normal = 0
    started = time.monotonic()
    try:
        phase("after_thread_and_fixture_startup", stats=scheduler.stats())
        made = []
        for index, pair in enumerate(pairs):
            def factory(p=pair):
                request = p.track(api.create_read(p.server, MAX_BUFFER))
                made.append(request)
                return request
            generation = scheduler.submit(f"d{index}", factory, timeout=30.0)
            if generation is None:
                raise AssertionError("max-device admission failed")
        _wait(lambda: len(made) == 16 and all(request.state != "new" for request in made))
        phase("max_16_devices_inflight", stats=scheduler.stats(),
              native_live_requests=api.live_request_count, read_buffer_bytes=16 * MAX_BUFFER)
        # Peer writes are fixture traffic, issued by this harness, never by the
        # production coordinator or an application UI thread.
        payload = b"P" * MAX_BUFFER
        for pair in pairs:
            request = pair.track(api.create_write(pair.client, payload))
            request.start()
            completion = wait_completion(request)
            if not completion.success or completion.transferred != len(payload):
                raise AssertionError("max-buffer fixture write failed")
            if not request.dispose():
                raise AssertionError("terminal write storage not disposed")
        _wait(lambda: all(not item.busy for item in scheduler.snapshot().values()))
        coalesced = scheduler.take_results()
        if len(coalesced) != 16 or any(result.data != payload for result in coalesced):
            raise AssertionError("max-buffer coalesced results are incomplete")
        proven_successful_native_operations += 32
        phase("max_16_devices_with_results", stats=scheduler.stats(),
              retained_result_bytes=sum(len(result.data) for result in coalesced))
        # Allocation-only: code 0 is never issued. Retain the 16 full last-good
        # results while allocating the backend's declared maximum input+output
        # buffers for every device, then cancel each NEW request locally.
        maximum_requests = [pair.track(api.create_ioctl(
            pair.server, 0, input_data=payload, output_size=MAX_BUFFER)) for pair in pairs]
        phase("synthetic_max_16_ioctl_allocations_never_started", stats=scheduler.stats(),
              native_live_requests=api.live_request_count,
              input_buffer_bytes=16 * MAX_BUFFER, output_buffer_bytes=16 * MAX_BUFFER,
              retained_result_bytes=sum(len(result.data) for result in coalesced))
        for request in maximum_requests:
            request.cancel()
            if not request.dispose():
                raise AssertionError("locally cancelled NEW allocation was not disposed")
        del maximum_requests
        del coalesced
        time.sleep(0.1)
        phase("steady_state", stats=scheduler.stats())

        for index in range(cycles):
            pair = pairs[0]
            if index % 3 == 0:
                generation = scheduler.submit("d0",
                    lambda p=pair: p.track(api.create_read(p.server, 32)), timeout=0.004)
                if generation is None:
                    raise AssertionError("cancel soak admission failed")
                _wait(lambda: not scheduler.snapshot()["d0"].busy)
                cancelled += 1
            else:
                small = index.to_bytes(8, "little")
                read_generation = scheduler.submit("d0",
                    lambda p=pair: p.track(api.create_read(p.server, 32)), timeout=1.0)
                if read_generation is None:
                    raise AssertionError("normal read admission failed")
                write_generation = scheduler.submit("d1",
                    lambda p=pair, value=small: p.track(api.create_write(p.client, value)),
                    timeout=1.0)
                if write_generation is None:
                    raise AssertionError("normal write admission failed")
                _wait(lambda: not scheduler.snapshot()["d0"].busy
                               and not scheduler.snapshot()["d1"].busy)
                read_result = scheduler.snapshot()["d0"].last_good
                write_result = scheduler.snapshot()["d1"].last_good
                if (read_result is None or read_result.data != small
                        or read_result.generation != read_generation
                        or write_result is None or write_result.generation != write_generation):
                    raise AssertionError("normal soak reply/completion mismatch")
                proven_successful_native_operations += 2
                normal += 1
            scheduler.take_results()
            if index % 25 == 0:
                sampler.capture()

        # An explicitly synthetic uncancellable operation exercises late-result
        # quarantine/retry accounting without pretending a pipe is a HID driver.
        last_good = scheduler.snapshot()["d0"].last_good
        delayed = DelayedUncancellableOperation()
        if scheduler.submit("d0", lambda: delayed, timeout=0.004) is None:
            raise AssertionError("late fixture admission failed")
        _wait(lambda: scheduler.snapshot()["d0"].expired)
        refused_retries = sum(scheduler.submit("d0", lambda: delayed, timeout=0.004) is None
                              for _ in range(1000))
        if refused_retries != 1000 or delayed.disposed:
            raise AssertionError("uncompleted late operation lost its lease/storage")
        delayed.completed.set()
        _wait(lambda: not scheduler.snapshot()["d0"].busy)
        after_late = scheduler.snapshot()["d0"].last_good
        if (after_late.data != last_good.data or after_late.generation != last_good.generation
                or after_late.fresh):
            raise AssertionError("late result replaced/replayed last-good observation")
        scheduler.take_results()
        phase("after_timeout_cancel_retry_soak", stats=scheduler.stats(),
              proven_successful_native_operations=proven_successful_native_operations,
              cancellation_attempt_rounds=cancelled,
              normal_rounds=normal, synthetic_retries_refused=refused_retries)
    finally:
        shutdown_start = time.monotonic()
        closed = scheduler.close(timeout=0.5)
        fixture_closed = [pair.close() for pair in pairs]
        shutdown_seconds = time.monotonic() - shutdown_start
    phase("after_shutdown", stats=scheduler.stats(), native_live_requests=api.live_request_count,
          shutdown_seconds=shutdown_seconds, closed=closed,
          fixture_closed=fixture_closed)
    sampler.close()
    added_modules = sorted(set(sys.modules) - baseline_modules)
    delta_peak = {field: sampler.peak[field] - baseline[field] for field in baseline}
    commit_os_upper_bound = max(
        0, sampler.peak["private_commit_lifetime_peak"] - baseline["private_commit"])
    commit_os_upper_bound_applicable = (
        baseline["private_commit_lifetime_peak"] - baseline["private_commit"] <= 5 * 1024 * 1024)
    memory_accepted = (
        all(delta_peak[field] <= 5 * 1024 * 1024
            for field in ("private_working_set", "private_commit"))
        and (not commit_os_upper_bound_applicable or commit_os_upper_bound <= 5 * 1024 * 1024))
    final_stats = scheduler.stats()
    cleanup_accepted = (closed and all(fixture_closed) and api.live_request_count == 0
                        and final_stats["active"] == 0 and final_stats["worker_alive"] == 0
                        and not final_stats["coordinator_alive"])
    operations_accepted = proven_successful_native_operations >= 1000
    passed = memory_accepted and cleanup_accepted and operations_accepted
    report = dict(
        kind="memory", runtime_executable=sys.executable, pid=os.getpid(),
        baseline=baseline, phases=phases, peak=sampler.peak,
        incremental_peak=delta_peak, acceptance_bytes=5 * 1024 * 1024,
        target_bytes=3 * 1024 * 1024, accepted=passed,
        memory_accepted=memory_accepted, cleanup_accepted=cleanup_accepted,
        private_commit_os_lifetime_upper_bound_increment=commit_os_upper_bound,
        private_commit_os_lifetime_bound_applicable=commit_os_upper_bound_applicable,
        operations_accepted=operations_accepted,
        runtime_seconds=time.monotonic() - started, samples=sampler.samples,
        prewarmed_stdlib=PREWARMED_STDLIB, added_modules=added_modules,
        baseline_threads_before_prototype_including_sampler=baseline_threads,
        final_threads_after_sampler_stop=threading.active_count(),
        rtss_injected=counters.rtss_injected(), rtss_injected_at_baseline=baseline_rtss,
        max_buffer=MAX_BUFFER,
        proven_successful_native_operations=proven_successful_native_operations,
        cancellation_attempt_rounds=cancelled, production_children=0,
        measurement_api="K32GetProcessMemoryInfo(PROCESS_MEMORY_COUNTERS_EX2)",
        limitations=["Local named pipes prove Win32 transport mechanics, not HID driver cancellation.",
                     "Private working set peak is sampled every 5ms plus explicit held-allocation phases.",
                     "Private commit also has the conservative OS lifetime high-water bound.",
                     "Measurement monitor was warmed before baseline; no trimming or forced GC."],
    )
    print(json.dumps(report), flush=True)
    return 0 if passed else 3


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--lifecycle-child", action="store_true")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--cycles", type=int, default=750)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.lifecycle_child:
        return lifecycle_child()
    if args.child:
        return measure_child(args.cycles)
    if not 1 <= args.repeats <= 10 or args.cycles < 750:
        parser.error("use 1..10 repeats and at least 750 cycles (>=1000 proven successful native operations)")
    import hashlib
    source_paths = [Path(__file__), Path(__file__).with_name("native_io_fixtures.py"),
                    ROOT / "halo" / "native_io.py",
                    ROOT / "halo" / "io_scheduler.py"]
    def hashes():
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    source_hashes_before = hashes()
    executable = getattr(sys, "_base_executable", sys.executable)
    lifecycle_started = time.monotonic()
    lifecycle_process = subprocess.run(
        [executable, "-B", str(Path(__file__).resolve()), "--lifecycle-child"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=8,
        creationflags=0x08000000 if os.name == "nt" else 0)
    lifecycle_elapsed = time.monotonic() - lifecycle_started
    if lifecycle_process.returncode:
        raise RuntimeError(f"lifecycle child failed: {lifecycle_process.stderr[:4096]}")
    lifecycle = json.loads(lifecycle_process.stdout.strip().splitlines()[-1])
    lifecycle["parent_observed_exit_seconds"] = lifecycle_elapsed
    lifecycle["returncode"] = lifecycle_process.returncode
    lifecycle["accepted"] = (
        lifecycle_elapsed < 8 and lifecycle["close_elapsed_seconds"] < 0.8
        and lifecycle["native_waits_still_uncompleted"]
        and lifecycle["stats"]["workers"] == 2 and not lifecycle["close_returned"])
    runs = []
    for index in range(args.repeats):
        completed = subprocess.run(
            [executable, "-B", str(Path(__file__).resolve()), "--child",
             "--cycles", str(args.cycles)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90,
            creationflags=0x08000000 if os.name == "nt" else 0)
        if not completed.stdout.strip():
            raise RuntimeError(f"measurement child failed: {completed.stderr[:4096]}")
        run = json.loads(completed.stdout.strip().splitlines()[-1])
        run.update(repeat=index + 1, returncode=completed.returncode)
        if completed.stderr:
            run["stderr"] = completed.stderr[:4096]
        runs.append(run)
    source_hashes_after = hashes()
    summary = dict(runs=runs, lifecycle=lifecycle,
                   source_hashes_before=source_hashes_before, source_hashes_after=source_hashes_after,
                   sources_unchanged=source_hashes_before == source_hashes_after,
                   accepted=(lifecycle["accepted"] and all(run["accepted"] and run["returncode"] == 0 for run in runs)
                             and source_hashes_before == source_hashes_after),
                   repeats=len(runs), harness_children_only=True,
                   maximum_private_commit_os_lifetime_upper_bound_increment=max(
                       run["private_commit_os_lifetime_upper_bound_increment"] for run in runs),
                   maximum_incremental_peak={field: max(run["incremental_peak"][field]
                                                       for run in runs)
                        for field in ("private_working_set", "private_commit")})
    rendered = json.dumps(summary, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if summary["accepted"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
