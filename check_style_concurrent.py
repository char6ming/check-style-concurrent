#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import concurrent.futures
import contextlib
import ctypes
import io
import os
import signal
import sys
import time
from collections import defaultdict
from ctypes.util import find_library
from datetime import datetime

[
    sys.__setattr__(s, io.TextIOWrapper(stream.buffer, encoding='utf-8'))
    for s in ['stdout', 'stderr']
    if (stream := getattr(sys, s)).encoding != 'UTF-8'
]

(script_dir := os.path.dirname(os.path.abspath(__file__))) in sys.path or sys.path.insert(0, script_dir)

import cpplint

assert sys.version_info >= (3, 8), "Python 3.8+ required for Walrus Operator and Clean Logic"

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

dirs = [
    'sharelib/sharelib/util2',
    'indexlib/indexlib/index2',
    'indexlib/indexlib/index/codecs',
    'sharelib/sharelib/codecs',
    'sharelib/sharelib/index',
    'sharelib/sharelib/geo'
]

project_root = os.path.dirname(os.path.dirname(script_dir))
VALID_EXTS = ('.h', '.cpp')

all_dir_files = {
    abs_path
    for sub_dir in dirs
    for current_dir, _, filenames in os.walk(os.path.join(project_root, sub_dir))
    for fname in filenames
    if fname.endswith(VALID_EXTS) and 'test' not in fname
    if (abs_path := os.path.abspath(os.path.join(current_dir, fname)))
}

normalize_file = lambda path: os.path.abspath(path) if os.path.exists(path) and path.endswith(VALID_EXTS) else None
rel_path       = lambda path: os.path.relpath(path, project_root) if path.startswith(project_root) else path

sources = [
    (lambda: len(sys.argv) > 1,      lambda: {f for x in sys.argv[1:] if (f := normalize_file(x))}),
    (lambda: not sys.stdin.isatty(), lambda: {f for s in map(str.strip, sys.stdin) if s if (f := normalize_file(s))} or None),
    (lambda: True,                   lambda: None)
]

input_files = next(fn() for cond, fn in sources if cond())
cpp_files   = sorted((input_files & all_dir_files) if input_files is not None else all_dir_files)
total_files = len(cpp_files)

if total_files == 0:
    print(' ℹ️  No C++ files found to check.')
    sys.exit(0)


def cpplint_single_file(file_info):
    idx, abs_path = file_info
    pid           = os.getpid()
    start_ts      = time.perf_counter()
    try:
        prefix_abs, prefix_rel = abs_path + ":", rel_path(abs_path) + ":"
        stderr_io,  stdout_io  = io.StringIO(),  io.StringIO()
        with contextlib.redirect_stderr(stderr_io), contextlib.redirect_stdout(stdout_io):
            cpplint._SetVerboseLevel(1)
            cpplint._SetQuiet(False)
            cpplint._cpplint_state.ResetErrorCounts()
            cpplint.ProcessFile(abs_path, vlevel=1)
            err_cnt = cpplint._cpplint_state.error_count

        err_out = "".join(
            prefix_rel + ln.strip()[len(prefix_abs):] + "\n"
            for ln in (stderr_io.getvalue() or "").splitlines()
            if ln.strip().startswith(prefix_abs)
        )
        res_code, res_status = (0, "success") if 0 == err_cnt else (1, "failed")
        return abs_path, res_code, "", err_out, res_status, idx, err_cnt, pid, (time.perf_counter() - start_ts) * 1000
    except Exception as e:
        return abs_path,       -1, "",  str(e),    "error", idx,      -1, pid, (time.perf_counter() - start_ts) * 1000


def init_worker(root_dir, sample_file):
    """
    Prevent orphan processes: 1 is PR_SET_PDEATHSIG in Linux: harvest all orphans on parent death.
    Pre-set repository root to bypass recursive I/O discovery.
    @todo： Monkey Patch -->  cpplint.GetHeaderGuardCPPVariable = optimized_header_guard
    """
    try:
        ctypes.CDLL(find_library('c')).prctl(1, signal.SIGKILL)
    except Exception:
        pass
    cpplint.ParseArguments([f'--repository={root_dir}', sample_file])
    cpplint._root       = root_dir
    cpplint._repository = root_dir


start_time    = time.perf_counter()
num_cpus      = (len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count()) or 1
max_workers   = max(1, min(total_files, num_cpus - 1))
completed     = 0
results       = defaultdict(list)
process_stats = defaultdict(lambda: {'count': 0, 'cost': 0.0})

print(f" 🎯  {datetime.now():%Y-%m-%d %H:%M:%S} | CPUs: {num_cpus} | Files: {total_files} | Workers: {max_workers}")

"""
num_w: width of a single number; line_w: total width for "Progress: X/N (Done: Y/N)"
line_w calculation: "Progress: " (10) + n + "/" (1) + n + " (Done: " (8) + n + "/" (1) + n + ")" (1) = 4*n + 21
Note: Adjusted to 4*n + 21 to ensure a 2-space breathing gap before the emoji
"""
num_w, line_w = (n := len(str(total_files))), 4 * n + 21

with concurrent.futures.ProcessPoolExecutor(max_workers = max_workers, initializer = init_worker, initargs = (project_root, "any.h")) as executor:
    status_config = {
        'success': ('✅',             '', lambda res, fp, msg, count: res['success'].append(fp)),
        'failed' : ('❌',             '', lambda res, fp, msg, count: res['failed'].append((fp, msg, count))),
        'error'  : ('💥', ' (EXCEPTION)', lambda res, fp, msg, count: res['failed'].append((fp, msg, count)))
    }
    futures = [executor.submit(cpplint_single_file, info) for info in enumerate(sorted(cpp_files, key=os.path.getsize, reverse=True), 1)]
    for future in concurrent.futures.as_completed(futures):
        file_path, code, out, err, status, file_index, err_cnt, pid, duration = future.result()
        completed += 1
        process_stats[pid]['count'] += 1
        process_stats[pid]['cost']  += duration
        emoji, suffix, handler = status_config.get(status, status_config['error'])
        prog = f"PID-{pid:<6} | {file_index:>{num_w}}/{total_files} (Done: {completed:>{num_w}}/{total_files})"
        print(f"{prog:<{line_w}}  {emoji}  {os.path.getsize(file_path):6}B {duration:6.1f}ms {rel_path(file_path)}{suffix}")
        handler(results, file_path, out + err, err_cnt)
        results['total'].append(file_path)


print(f'\n 🏁  {datetime.now():%Y-%m-%d %H:%M:%S} Finished! | Elapsed: {time.perf_counter() - start_time:.3f}s')
print(f' 📊  Summary: Total {len(results["total"])}, Success {len(results["success"])}, Failed {len(results["failed"])}')


[
    print(f" {'🚩  Max' if rank_idx == 0 else '🚀  Min'} cost | PID-{pid:<6} | {metrics['count']:>3} files | {metrics['cost'] / 1000:.3f}s")
    for sorted_workers in [sorted(process_stats.items(), key=lambda x: x[1]['cost'], reverse=True)]
    for rank_idx in sorted({0, len(sorted_workers) - 1})
    for pid, metrics in [sorted_workers[rank_idx]]
]


if results['failed']:
    print('\n=== Failure Details ===')
    total_errors = 0
    for fp, msg, count in results['failed']:
        total_errors += max(0, count)
        count_str = f" ({count} errors)" if count >= 0 else ""
        print(f'\n ❌  {rel_path(fp)}{count_str}:\n{msg.strip() or "(No error output)"}')
    print(f"\n 🚨  Style check failed! {total_errors} errors found in total.\n")
    sys.exit(1)
else:
    print(f"\n 🎉  All {total_files} files passed style check!\n")
    sys.exit(0)
