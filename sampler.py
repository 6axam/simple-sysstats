#!/usr/bin/env python3
"""Low-overhead, unprivileged system graph sampler for the Omarchy widget."""

from __future__ import annotations

import json
import math
import os
import signal
import stat
import sys
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any

INTERVAL = 1
WINDOW = 60
SNAPSHOT_INTERVAL = 30
METRICS = ("cpu", "ram", "gpu", "vram", "cpu_temp", "gpu_temp", "disk", "net_rx", "net_tx")
stop = False


def on_signal(_signum: int, _frame: Any) -> None:
    global stop
    stop = True


def read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return None


def number(path: Path) -> float | None:
    raw = read_text(path)
    if raw is None:
        return None
    try:
        value = float(raw)
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def cpu_counters() -> tuple[int, int] | None:
    raw = read_text(Path("/proc/stat"))
    if raw is None:
        return None
    try:
        fields = raw.splitlines()[0].split()
        if fields[0] != "cpu" or len(fields) < 5:
            return None
        vals = [int(x) for x in fields[1:]]
        total = sum(vals)
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return total, idle
    except (ValueError, IndexError):
        return None


def percent_delta(before: tuple[int, int] | None, after: tuple[int, int] | None) -> float | None:
    if before is None or after is None:
        return None
    dt, di = after[0] - before[0], after[1] - before[1]
    if dt <= 0 or di < 0 or di > dt:
        return None
    return 100.0 * (dt - di) / dt


def ram_read() -> tuple[float, int, int] | None:
    raw = read_text(Path("/proc/meminfo"))
    if raw is None:
        return None
    try:
        values = {line.split(":", 1)[0]: int(line.split()[1]) * 1024 for line in raw.splitlines() if ":" in line}
        total, available = values["MemTotal"], values["MemAvailable"]
        used = total - available
        if total <= 0 or used < 0:
            return None
        return used * 100.0 / total, used, total
    except (ValueError, KeyError, IndexError):
        return None


def hwmon_temp(name: str) -> float | None:
    for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
        if read_text(hwmon / "name") != name:
            continue
        labels = sorted(hwmon.glob("temp*_label"))
        inputs = sorted(hwmon.glob("temp*_input"))
        for input_path in inputs:
            label_path = input_path.with_name(input_path.name.replace("_input", "_label"))
            label = read_text(label_path) if label_path.exists() else None
            # AMD edge and CPU package/Tctl sensors only; ignore empty/fake channels.
            if name == "amdgpu" and label and label.lower() not in ("edge", "junction", "hotspot"):
                continue
            if name == "k10temp" and label and label.lower() not in ("tctl", "tdie"):
                continue
            val = number(input_path)
            if val is not None and 0 < val < 150_000:
                return val / 1000.0
        # Some hwmon drivers omit labels. Accept only their first valid channel.
        if not labels and inputs:
            val = number(inputs[0])
            if val is not None and 0 < val < 150_000:
                return val / 1000.0
    return None


def gpu_read() -> tuple[float | None, float | None, int | None, int | None, str | None]:
    """Return GPU utilization, VRAM %, used, total, vendor; never invoke wake-capable CLIs."""
    drm = Path("/sys/class/drm")
    cards = sorted(drm.glob("card[0-9]*"))
    for card in cards:
        dev = card / "device"
        vendor = read_text(dev / "vendor")
        driver_link = dev / "driver"
        try:
            driver = driver_link.resolve().name
        except OSError:
            driver = ""
        if vendor == "0x1002" and driver == "amdgpu":
            busy = number(dev / "gpu_busy_percent")
            used = number(dev / "mem_info_vram_used")
            total = number(dev / "mem_info_vram_total")
            gpu = busy if busy is not None and 0 <= busy <= 100 else None
            vram = used * 100 / total if used is not None and total and 0 <= used <= total else None
            return gpu, vram, int(used) if used is not None and used >= 0 else None, int(total) if total is not None and total > 0 else None, "amd"
        if vendor == "0x8086":
            # Do not pretend generic Intel sysfs data means utilization/VRAM.
            return None, None, None, None, "intel"
        if vendor == "0x10de":
            # nvidia-smi can wake a suspended GPU; intentionally do not run it.
            return None, None, None, None, "nvidia"
    return None, None, None, None, None


def disk_read() -> tuple[float, int, int] | None:
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        if total <= 0 or used < 0:
            return None
        return used * 100.0 / total, used, total
    except OSError:
        return None


def net_counters() -> tuple[dict[str, tuple[int, int]], bool]:
    """Physical NIC byte counters; bool says at least one physical interface exists."""
    root = Path("/sys/class/net")
    result: dict[str, tuple[int, int]] = {}
    physical_seen = False
    try:
        interfaces = list(root.iterdir())
    except OSError:
        return result, False
    for iface in interfaces:
        if iface.name == "lo" or (iface / "device").exists() is False:
            continue
        physical_seen = True
        try:
            rx = int((iface / "statistics/rx_bytes").read_text().strip())
            tx = int((iface / "statistics/tx_bytes").read_text().strip())
            if rx >= 0 and tx >= 0:
                result[iface.name] = (rx, tx)
        except (OSError, ValueError):
            continue
    return result, physical_seen


def net_rates(before: dict[str, tuple[int, int]] | None,
              after: dict[str, tuple[int, int]], elapsed: float) -> tuple[float | None, float | None]:
    if before is None or elapsed <= 0:
        return None, None
    common = before.keys() & after.keys()
    if not common:
        return None, None
    rx = tx = 0
    for iface in common:
        drx, dtx = after[iface][0] - before[iface][0], after[iface][1] - before[iface][1]
        if drx < 0 or dtx < 0:
            return None, None
        rx += drx
        tx += dtx
    return rx / elapsed, tx / elapsed


def xdg_path(env: str, fallback: Path) -> Path:
    value = os.environ.get(env)
    return Path(value) if value and Path(value).is_absolute() else fallback


def atomic_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmpname = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"), allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmpname, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmpname)
        except OSError:
            pass
        raise


def restore(path: Path, now_ms: int) -> dict[str, deque[list[float | int]]]:
    history = {name: deque() for name in METRICS}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("history"), dict):
            return history
        cutoff = now_ms - WINDOW * 1000
        for name in METRICS:
            entries = raw["history"].get(name, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, list) or len(entry) != 2:
                    continue
                ts, val = entry
                if type(ts) is not int or isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
                    continue
                if cutoff <= ts <= now_ms:
                    history[name].append([ts, float(val)])
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return history


def snapshot(now_ms: int, latest: dict[str, float | None], details: dict[str, int | None],
             caps: dict[str, bool], history: dict[str, deque[list[float | int]]]) -> dict[str, Any]:
    return {
        "updatedAt": now_ms, "interval": INTERVAL, "window": WINDOW,
        "capabilities": caps, "latest": latest, "details": details,
        "history": {k: list(history[k]) for k in METRICS},
    }


def main() -> int:
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    runtime = xdg_path("XDG_RUNTIME_DIR", Path(f"/run/user/{os.getuid()}")) / "maks-sysstats-graph.json"
    state_root = xdg_path("XDG_STATE_HOME", Path.home() / ".local/state")
    state = state_root / "omarchy/maks-sysstats-history.json"
    history = restore(state, int(time.time() * 1000))
    prev_cpu = cpu_counters()
    prev_net, physical_seen = net_counters()
    prev_mono = time.monotonic()
    last_persist = 0.0
    while not stop:
        target = prev_mono + INTERVAL
        while not stop:
            remaining = target - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.25))
        if stop:
            break
        mono = time.monotonic()
        elapsed = mono - prev_mono
        now_ms = int(time.time() * 1000)
        cpu_now = cpu_counters()
        cpu = percent_delta(prev_cpu, cpu_now)
        prev_cpu = cpu_now
        ram_reading = ram_read()
        ram = ram_reading[0] if ram_reading else None
        gpu, vram, vram_used, vram_total, vendor = gpu_read()
        cpu_temp = hwmon_temp("k10temp")
        gpu_temp = hwmon_temp("amdgpu") if vendor == "amd" else None
        disk_reading = disk_read()
        disk = disk_reading[0] if disk_reading else None
        net_now, net_physical = net_counters()
        rx, tx = net_rates(prev_net, net_now, elapsed)
        # Zero speed is meaningful only when physical devices are present and counters read.
        net_cap = net_physical and (rx is not None and tx is not None)
        prev_net = net_now
        physical_seen = net_physical
        latest: dict[str, float | None] = {
            "cpu": cpu, "ram": ram, "gpu": gpu, "vram": vram,
            "cpu_temp": cpu_temp, "gpu_temp": gpu_temp, "disk": disk,
            "net_rx": rx, "net_tx": tx,
        }
        for name, value in latest.items():
            if value is not None and math.isfinite(value):
                history[name].append([now_ms, float(value)])
            cutoff = now_ms - WINDOW * 1000
            while history[name] and history[name][0][0] < cutoff:
                history[name].popleft()
        caps = {
            "cpu": cpu is not None, "ram": ram is not None,
            "gpu": gpu is not None, "vram": vram is not None,
            "cpu_temp": cpu_temp is not None, "gpu_temp": gpu_temp is not None,
            "disk": disk is not None, "net": bool(net_cap),
        }
        details = {
            "ramUsed": ram_reading[1] if ram_reading else None,
            "ramTotal": ram_reading[2] if ram_reading else None,
            "vramUsed": vram_used, "vramTotal": vram_total,
            "diskUsed": disk_reading[1] if disk_reading else None,
            "diskTotal": disk_reading[2] if disk_reading else None,
        }
        data = snapshot(now_ms, latest, details, caps, history)
        try:
            atomic_json(runtime, data)
        except OSError as exc:
            print(f"sampler: runtime snapshot failed: {exc}", file=sys.stderr)
        if mono - last_persist >= SNAPSHOT_INTERVAL:
            try:
                atomic_json(state, data)
                last_persist = mono
            except OSError as exc:
                print(f"sampler: history snapshot failed: {exc}", file=sys.stderr)
        prev_mono = mono
    # Persist once on a graceful exit for the best available recovery point.
    try:
        atomic_json(state, data if 'data' in locals() else snapshot(int(time.time() * 1000),
                    {k: None for k in METRICS}, {"ramUsed": None, "ramTotal": None, "vramUsed": None,
                    "vramTotal": None, "diskUsed": None, "diskTotal": None},
                    {"cpu": False, "ram": False, "gpu": False, "vram": False, "cpu_temp": False,
                     "gpu_temp": False, "disk": False, "net": False}, history))
    except OSError as exc:
        print(f"sampler: final history snapshot failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
