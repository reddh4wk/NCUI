#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe

CPU_BAR_SAMPLE = "/tmp/nc_cpu_bar_sample.json"


def read_proc_stat() -> dict:
    res = {}
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            for line in f:
                if not line.startswith("cpu"):
                    continue
                parts = line.split()
                vals = [int(x) for x in parts[1:]]
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                res[parts[0]] = (idle, sum(vals))
    except Exception:
        pass
    return res


def get_cpu_bar_pct() -> float:
    curr = read_proc_stat().get("cpu", (0, 0))
    prev = curr
    if os.path.exists(CPU_BAR_SAMPLE):
        try:
            with open(CPU_BAR_SAMPLE, "r", encoding="utf-8") as f:
                prev = tuple(json.load(f))
        except Exception:
            pass
    try:
        with open(CPU_BAR_SAMPLE, "w", encoding="utf-8") as f:
            json.dump(list(curr), f)
    except Exception:
        pass
    d_tot = curr[1] - prev[1]
    return 0.0 if d_tot <= 0 else (1.0 - (curr[0] - prev[0]) / float(d_tot)) * 100.0


def get_cpu_telemetry(prev_stat: dict) -> dict:
    curr_stat = read_proc_stat()
    if not prev_stat:
        time.sleep(0.05)
        prev_stat = curr_stat
        curr_stat = read_proc_stat()
    usage = {}
    for k, (c_idle, c_tot) in curr_stat.items():
        p_idle, p_tot = prev_stat.get(k, (c_idle, c_tot))
        d_tot = c_tot - p_tot
        d_idle = c_idle - p_idle
        usage[k] = max(0.0, min(100.0, (1.0 - (d_idle / float(d_tot))) * 100.0)) if d_tot > 0 else 0.0
    core_keys = sorted([k for k in usage if k != "cpu" and k[3:].isdigit()], key=lambda x: int(x[3:]))
    cores_pct = [usage[k] for k in core_keys]
    model_name = "CPU"
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    raw_m = re.sub(r"\b(AMD|Processor|\d+-Core)\b", "", line.split(":", 1)[1]).strip()
                    model_name = re.sub(r"\s+", " ", raw_m)[:24]
                    break
    except Exception:
        pass
    freqs_khz = []
    try:
        for entry in os.listdir("/sys/devices/system/cpu"):
            if re.match(r"^cpu\d+$", entry):
                p = f"/sys/devices/system/cpu/{entry}/cpufreq/scaling_cur_freq"
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as ff:
                        freqs_khz.append(int(ff.read().strip()))
    except Exception:
        pass
    max_ghz = (max(freqs_khz) / 1000000.0) if freqs_khz else 0.0
    temp_c = 0.0
    try:
        hwmon_base = "/sys/class/hwmon"
        fallback_temp = 0.0
        if os.path.exists(hwmon_base):
            for hm in os.listdir(hwmon_base):
                hm_dir = os.path.join(hwmon_base, hm)
                name_p = os.path.join(hm_dir, "name")
                hm_name = open(name_p, "r", encoding="utf-8").read().strip() if os.path.exists(name_p) else ""
                t_p = os.path.join(hm_dir, "temp1_input")
                if os.path.exists(t_p):
                    val = int(open(t_p, "r", encoding="utf-8").read().strip()) / 1000.0
                    if hm_name in ("k10temp", "coretemp", "zenpower"):
                        temp_c = val
                        break
                    elif fallback_temp == 0.0:
                        fallback_temp = val
        if temp_c == 0.0:
            temp_c = fallback_temp
    except Exception:
        pass
    try:
        loads = os.getloadavg()
    except Exception:
        loads = (0.0, 0.0, 0.0)
    return {
        "stat": curr_stat,
        "total_pct": usage.get("cpu", 0.0),
        "cores_pct": cores_pct,
        "model": model_name,
        "max_ghz": max_ghz,
        "temp_c": temp_c,
        "loads": loads,
    }


def get_ram_telemetry() -> dict:
    mem = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    parts = v.strip().split()
                    if parts and parts[0].isdigit():
                        mem[k.strip()] = int(parts[0]) * 1024
    except Exception:
        pass
    tot = mem.get("MemTotal", 1)
    avail = mem.get("MemAvailable", 0)
    used = max(0, tot - avail)
    sw_tot = mem.get("SwapTotal", 0)
    sw_used = max(0, sw_tot - mem.get("SwapFree", 0))
    return {
        "total": tot,
        "avail": avail,
        "used": used,
        "pct": (used / float(tot)) * 100.0,
        "cache": mem.get("Cached", 0) + mem.get("SReclaimable", 0) + mem.get("Buffers", 0),
        "dirty": mem.get("Dirty", 0),
        "sw_total": sw_tot,
        "sw_used": sw_used,
        "sw_pct": (sw_used / float(sw_tot)) * 100.0 if sw_tot > 0 else 0.0,
    }


def get_process_list(sort_by: str = "cpu", group_by_app: bool = False) -> list[dict]:
    sort_flag = "-pcpu" if sort_by == "cpu" else "-rss"
    ok, out = probe.run_cmd(["ps", "-eo", "pid=,pcpu=,pmem=,rss=,nlwp=,stat=,comm=,args=", "--sort", sort_flag], timeout=2.0)
    if not ok or not out:
        return []
    procs = []
    for line in out.splitlines():
        parts = line.strip().split(None, 7)
        if len(parts) < 7:
            continue
        pid_s, cpu_s, mem_s, rss_s, nlwp_s, stat_s, comm = parts[:7]
        args = parts[7] if len(parts) > 7 else comm
        if comm == "ps":
            continue
        try:
            pid = int(pid_s)
            procs.append({
                "pid": pid,
                "pids": [pid],
                "cpu": float(cpu_s),
                "mem": float(mem_s),
                "rss": int(rss_s) * 1024,
                "nlwp": int(nlwp_s),
                "stat": stat_s,
                "comm": comm,
                "args": args,
                "count": 1,
            })
        except ValueError:
            continue
    if not group_by_app:
        return procs
    grouped = {}
    for p in procs:
        k = p["comm"]
        if k not in grouped:
            grouped[k] = dict(p, pids=list(p["pids"]))
        else:
            g = grouped[k]
            g["pids"].append(p["pid"])
            g["cpu"] += p["cpu"]
            g["mem"] += p["mem"]
            g["rss"] += p["rss"]
            g["nlwp"] += p["nlwp"]
            g["count"] += 1
    res = list(grouped.values())
    res.sort(key=lambda x: x["cpu"] if sort_by == "cpu" else x["rss"], reverse=True)
    return res


def signal_processes(pids_csv: str, force_kill: bool = False) -> None:
    sig = "-9" if force_kill else "-15"
    pids = [p for p in str(pids_csv).split(",") if p.isdigit()]
    if pids:
        probe.run_cmd(["kill", sig] + pids, timeout=1.5)
