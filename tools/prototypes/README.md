# Native transport verification tools

The runtime lives in the application package:

- `halo.native_io` owns Windows overlapped requests, buffers, events and handles.
- `halo.io_scheduler` runs native operations on two fixed daemon workers with a
  bounded coordinator. It permits at most 16 active device contexts and rejects
  report buffers above 32 KiB instead of truncating them.
- `halo.hid_transport` adapts existing `hid.device()` calls, preserving report
  lengths, report-ID handling, feature-report behavior and pending passive reads.
- `halo.polling` schedules the existing provider instances on two fixed provider
  workers. The app consumes cached observations rather than running device calls.

Normal application builds include these runtime modules. This directory contains
only development fixtures and measurement tools. The runtime does not import the
named-pipe fixtures or measurement harness, and creates no worker processes.

## Ownership and limits

A timeout invalidates publication, not ownership. `CancelIoEx` targets the exact
request; buffers and the device lease remain retained until actual terminal
completion and disposal. Late replies cannot become fresh readings. Deferred
close intents survive cancellation, queued-cleanup expiration and late completion.
Ordinary finite/nonblocking input reads preserve the pending read for the next
read, matching HIDAPI behavior. Confirmed ordinary errors can be followed by a
successful provider fallback; cancellation and unresolved ownership cannot.

Opening devices, metadata and `HidD_SetFeature` remain synchronous native calls on
the fixed workers. Drivers can refuse cancellation. Two genuinely blocked native
workers exhaust that transport; no replacement thread/process is created, and
unresolved ownership is retained until completion or process exit. Existing
serial/libusb/WGI and provider background readers retain their implementations.
This design does not guarantee recovery from every third-party native failure.

The tests use local named pipes and synthetic operations. They never query real
HID/USB devices or the network. A disposable measurement child verifies normal
interpreter exit while two native waits remain unresolved; that process is part
of the developer harness, not the application runtime.

## Reproduce offline checks

From the repository root on Windows:

```powershell
python -B -W error::ResourceWarning -m unittest discover -s tests -p '*prototype*.py'
python -B -W error::ResourceWarning -m unittest discover -s tests -p 'test_experimental_hid.py'
python -B -W error::ResourceWarning -m unittest discover -s tests -p 'test_experimental_polling.py'
python -B tools/prototypes/measure_native_io.py --repeats 5 --cycles 750 --output "$env:TEMP\halobattery-io-memory.json"
```

The retained test filenames reflect the original prototype. They now import the
production modules directly. Measurement includes module import, thread startup,
steady state, bounded saturation, timeout/cancel cycles and shutdown. Both private
working set and private committed memory are recorded. No working-set trimming,
forced collection or thread-stack reduction is used. The target is at most 5 MiB
incremental memory, with the smaller 3 MiB target reported separately.

## Historical measurements before promotion

On this Windows x64 / Python 3.14 machine, five independent standalone runs with
32 KiB buffers passed the 5 MiB ceiling with unchanged source hashes:

| Check | Result |
| --- | --- |
| Maximum additional private working set, sampled | 2.785 MiB |
| Maximum additional private commit | 3.137 MiB |
| OS private-commit high-water upper bound | 3.137 MiB |
| Successful native operations | 1,032 per run |
| Additional cancellation attempts | 250 per run |
| Refused retries behind an unresolved lease | 1,000 per run |
| Handles after ordinary shutdown | Returned to baseline |
| Requests/threads after ordinary shutdown | Zero |
| Process exit with two blocked native waits | 0.330 s |

The rejected 64 KiB-buffer experiment exceeded 5 MiB private commit. The accepted
32 KiB cap remains an explicit compatibility limit. The incomplete-shutdown test
honestly returns false from `close()` and retains ownership; it never force-frees
pending I/O storage. The OS releases those resources at process exit.

The working-set peak was sampled every 5 ms plus explicit saturation phases;
private commit also used the OS lifetime high-water counter. RTSS was injected
before baseline in every repeat. These standalone results are distinct from
application measurements.

The later local integration candidate passed a same-EXE legacy/native comparison
with +0.945 MiB median private working set and +1.570 MiB median private commit.
The user confirmed Razer BlackShark V2 Pro, WLmouse Beast X Max and wireless
GameSir G7 PRO in the Desktop candidate. Those observations describe that machine
and candidate, not every supported driver or device.

## Sources

- [Overlapped I/O](https://learn.microsoft.com/en-us/windows/win32/sync/synchronization-and-overlapped-input-and-output)
- [CancelIoEx and request lifetime](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex)
- [Driver cancellation limits](https://learn.microsoft.com/en-us/windows/win32/fileio/canceling-pending-i-o-operations)
- [Nonwaiting completion](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-getoverlappedresult)
- [Private memory counters](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters_ex2)
- [Pinned cython-hidapi binding](https://github.com/trezor/cython-hidapi/tree/0.15.0)
- [Pinned HIDAPI Windows implementation](https://github.com/libusb/hidapi/blob/d6b2a974608dec3b76fb1e36c189f22b9cf3650c/windows/hid.c)
