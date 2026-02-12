"""Android controller utilities.

This is a *minimal* controller inspired by MobileWorld's runtime controller and
AndroidWorld's adb helper utilities.

The goal is to standardize:
  * snapshot/reset (AVD snapshot load)
  * auditable adb commands (recordable by hard oracles)

Notes
-----
* We do not attempt to provide a full-featured device farm controller here.
* All operations are intended for *emulator/testbed* use only.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple


class AndroidControllerError(RuntimeError):
    """Raised when an adb/emulator operation fails."""


@dataclass(frozen=True)
class AdbResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int

    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class AdbBinaryResult:
    args: list[str]
    stdout: bytes
    stderr: bytes
    returncode: int

    def ok(self) -> bool:
        return self.returncode == 0


def _parse_component(component: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse Android component string 'pkg/.Act' or 'pkg/pkg.Act'."""

    component = str(component).strip()
    if "/" not in component:
        return None, None
    pkg, activity = component.split("/", 1)
    pkg = pkg.strip()
    activity = activity.strip()
    if not pkg or not activity:
        return None, None
    if activity.startswith("."):
        activity = pkg + activity
    return pkg, activity


def _extract_component_from_dumpsys_activity(txt: str) -> Optional[str]:
    patterns = (
        r"mResumedActivity:.*?\s([\w.]+/[\w.$]+)",
        r"mFocusedActivity:.*?\s([\w.]+/[\w.$]+)",
        # Android 15/16 style
        r"\bResumedActivity:\s*ActivityRecord\{.*?\s([\w.]+/[\w.$]+)\b",
        r"\bResumed:\s*ActivityRecord\{.*?\s([\w.]+/[\w.$]+)\b",
        r"\btopResumedActivity=ActivityRecord\{.*?\s([\w.]+/[\w.$]+)\b",
        # Fallbacks sometimes appear in dumpsys activity output
        r"\bmCurrentFocus=Window\{.*?\s([\w.]+/[\w.$]+)\}",
        r"\bmFocusedApp=ActivityRecord\{.*?\s([\w.]+/[\w.$]+)\b",
    )
    for pat in patterns:
        m = re.search(pat, txt)
        if m:
            return m.group(1)
    return None


def _extract_component_from_dumpsys_window(txt: str) -> Optional[str]:
    for pat in (
        r"mCurrentFocus=Window\{.*?\s([\w.]+/[\w.$]+)\}",
        r"mFocusedApp=.*?ActivityRecord\{.*?\s([\w.]+/[\w.$]+)\b",
    ):
        m = re.search(pat, txt)
        if m:
            return m.group(1)
    return None


def _parse_wm_size(txt: str) -> Dict[str, Any]:
    physical = re.search(r"Physical size:\s*(\d+)x(\d+)", txt)
    override = re.search(r"Override size:\s*(\d+)x(\d+)", txt)
    return {
        "raw": txt.strip(),
        "physical_size": [int(physical.group(1)), int(physical.group(2))] if physical else None,
        "override_size": [int(override.group(1)), int(override.group(2))] if override else None,
    }


def _parse_wm_density(txt: str) -> Dict[str, Any]:
    physical = re.search(r"Physical density:\s*(\d+)", txt)
    override = re.search(r"Override density:\s*(\d+)", txt)
    return {
        "raw": txt.strip(),
        "physical_density": int(physical.group(1)) if physical else None,
        "override_density": int(override.group(1)) if override else None,
    }


def _parse_surface_orientation(txt: str) -> Optional[int]:
    def normalize_rotation(v: int) -> int:
        # dumpsys commonly prints ROTATION_0/90/180/270; normalize to 0/1/2/3.
        mapping = {0: 0, 90: 1, 180: 2, 270: 3}
        return mapping.get(v, v)

    m = re.search(r"SurfaceOrientation:\s*(\d+)", txt)
    if m:
        return normalize_rotation(int(m.group(1)))
    m = re.search(r"mCurrentRotation=ROTATION_(\d+)", txt)
    if m:
        return normalize_rotation(int(m.group(1)))
    m = re.search(r"mDisplayRotation=ROTATION_(\d+)", txt)
    if m:
        return normalize_rotation(int(m.group(1)))
    m = re.search(r"\brotation=(\d+)\b", txt)
    if m:
        return normalize_rotation(int(m.group(1)))
    return None


def _parse_bracket_rect(txt: str, *, key: str) -> Optional[Tuple[int, int, int, int]]:
    m = re.search(
        rf"{re.escape(key)}=\[(\d+)\s*,\s*(\d+)\]\[(\d+)\s*,\s*(\d+)\]",
        txt,
    )
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))


def _parse_decor_insets_rotation_line(txt: str, *, rotation_degrees: int) -> Optional[str]:
    in_section = False
    lines_by_rotation: Dict[int, str] = {}
    for raw_line in txt.splitlines():
        if "mDecorInsetsInfo:" in raw_line:
            in_section = True
            continue
        if not in_section:
            continue
        line = raw_line.strip()
        if not line.startswith("ROTATION_"):
            if lines_by_rotation:
                break
            continue
        m = re.match(r"ROTATION_(\d+)=", line)
        if not m:
            continue
        try:
            rot = int(m.group(1))
        except Exception:
            continue
        lines_by_rotation[rot] = line

    if rotation_degrees in lines_by_rotation:
        return lines_by_rotation[rotation_degrees]
    if 0 in lines_by_rotation:
        return lines_by_rotation[0]
    if lines_by_rotation:
        return lines_by_rotation[sorted(lines_by_rotation.keys())[0]]
    return None


def _rotation_index_to_degrees(rotation: int) -> int:
    mapping = {0: 0, 1: 90, 2: 180, 3: 270}
    return mapping.get(int(rotation), int(rotation))


def _parse_physical_frame_boundary_px(
    window_displays: str,
    *,
    surface_orientation: Optional[int],
    display_size: Optional[Tuple[int, int]],
) -> Optional[Dict[str, int]]:
    if not isinstance(window_displays, str) or not window_displays.strip():
        return None

    rotation_idx = int(surface_orientation) if isinstance(surface_orientation, int) else 0
    rotation_degrees = _rotation_index_to_degrees(rotation_idx)
    line = _parse_decor_insets_rotation_line(window_displays, rotation_degrees=rotation_degrees)
    if line is None and rotation_degrees != rotation_idx:
        # Some Android builds print ROTATION_0/1/2/3 instead of ROTATION_0/90/180/270.
        line = _parse_decor_insets_rotation_line(window_displays, rotation_degrees=rotation_idx)
    if line is None:
        return None

    rect = _parse_bracket_rect(line, key="overrideNonDecorFrame") or _parse_bracket_rect(
        line, key="nonDecorFrame"
    )
    if rect is None and display_size is not None:
        w, h = display_size
        insets = (
            _parse_bracket_rect(line, key="overrideNonDecorInsets")
            or _parse_bracket_rect(line, key="overrideConfigInsets")
            or _parse_bracket_rect(line, key="nonDecorInsets")
            or _parse_bracket_rect(line, key="configInsets")
        )
        if insets is not None:
            inset_left, inset_top, inset_right, inset_bottom = insets
            rect = (
                inset_left,
                inset_top,
                max(inset_left, w - inset_right),
                max(inset_top, h - inset_bottom),
            )

    if rect is None:
        return None

    left, top, right, bottom = rect
    if right < left or bottom < top:
        return None

    if display_size is not None:
        w, h = display_size
        left = max(0, min(int(left), int(w)))
        right = max(left, min(int(right), int(w)))
        top = max(0, min(int(top), int(h)))
        bottom = max(top, min(int(bottom), int(h)))

    return {"left": int(left), "top": int(top), "right": int(right), "bottom": int(bottom)}


class AndroidController:
    """Thin wrapper around adb for reproducible resets and hard oracle queries."""

    def __init__(
        self,
        *,
        adb_path: str = "adb",
        serial: Optional[str] = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._adb_path = adb_path
        self._serial = serial
        self._timeout_s = timeout_s

    @property
    def serial(self) -> Optional[str]:
        return self._serial

    def _base_cmd(self) -> list[str]:
        cmd = [self._adb_path]
        if self._serial:
            cmd += ["-s", self._serial]
        return cmd

    def adb(self, *args: str, timeout_s: float | None = None, check: bool = True) -> AdbResult:
        """Run an adb command and return stdout/stderr/returncode.

        This method is suitable for recording by hard oracles (the `args`
        field is stable and the output can be digested).
        """

        cmd = self._base_cmd() + list(args)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self._timeout_s if timeout_s is None else float(timeout_s),
        )
        result = AdbResult(
            args=cmd,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
        if check and not result.ok():
            raise AndroidControllerError(
                f"adb command failed (rc={result.returncode}): {' '.join(cmd)}\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )
        return result

    def adb_binary(
        self, *args: str, timeout_s: float | None = None, check: bool = True
    ) -> AdbBinaryResult:
        cmd = self._base_cmd() + list(args)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=self._timeout_s if timeout_s is None else float(timeout_s),
        )
        result = AdbBinaryResult(
            args=cmd,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
        if check and not result.ok():
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise AndroidControllerError(
                f"adb command failed (rc={result.returncode}): {' '.join(cmd)}\n"
                f"stderr: {stderr}"
            )
        return result

    def adb_shell(
        self,
        command: str,
        *,
        timeout_s: float | None = None,
        timeout_ms: int | None = None,
        check: bool = True,
    ) -> AdbResult:
        if timeout_ms is not None:
            timeout_s = float(timeout_ms) / 1000.0
        return self.adb("shell", command, timeout_s=timeout_s, check=check)

    def adb_version(self) -> str:
        # `adb version` prints to stdout.
        res = self.adb("version", check=False)
        return (res.stdout or res.stderr).strip()

    def get_build_fingerprint(self) -> str:
        res = self.adb_shell("getprop ro.build.fingerprint", check=False)
        return res.stdout.strip()

    def screencap(self, *, timeout_s: float | None = None) -> bytes:
        """Return a screenshot PNG (best-effort via `adb exec-out screencap -p`)."""

        res = self.adb_binary("exec-out", "screencap", "-p", timeout_s=timeout_s, check=False)
        if res.ok() and res.stdout.startswith(b"\x89PNG"):
            return res.stdout

        # Fallback to file-based screenshot.
        remote = "/sdcard/__mas_screencap.png"
        cap = self.adb_shell(
            f"screencap -p {shlex.quote(remote)}",
            timeout_s=timeout_s,
            check=False,
        )
        if not cap.ok():
            stderr = (res.stderr or b"").decode("utf-8", errors="replace")
            raise AndroidControllerError(
                "screencap failed via exec-out and shell fallback\n"
                f"exec-out rc={res.returncode} stderr={stderr[:500]}\n"
                f"shell rc={cap.returncode} stderr={(cap.stderr or '')[:500]}"
            )

        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            pull = self.pull_file(remote, tmp_path, timeout_s=timeout_s, check=False)
            if not pull.ok() or not tmp_path.exists():
                raise AndroidControllerError(f"adb pull failed: {pull.stderr.strip()}")
            data = tmp_path.read_bytes()
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            self.adb_shell(f"rm -f {shlex.quote(remote)}", check=False)

        if not data.startswith(b"\x89PNG"):
            raise AndroidControllerError("screencap produced non-PNG bytes")
        return data

    def screencap_to_file(self, path: Path, *, timeout_s: float | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.screencap(timeout_s=timeout_s))
        return path

    def uiautomator_dump(
        self,
        *,
        local_path: Path,
        remote_path: str | None = None,
        timeout_s: float | None = None,
        max_attempts: int = 5,
        remove_remote: bool = True,
    ) -> Path:
        """Dump UIAutomator XML to local_path (pulls from device)."""

        local_path.parent.mkdir(parents=True, exist_ok=True)
        remote = remote_path or "/sdcard/__mas_uiautomator_dump.xml"

        dump_cmd = f"uiautomator dump {shlex.quote(remote)}"
        for attempt in range(max_attempts):
            res = self.adb_shell(dump_cmd, timeout_s=timeout_s, check=False)
            if res.ok():
                pull = self.pull_file(remote, local_path, timeout_s=timeout_s, check=False)
                if pull.ok() and local_path.exists() and local_path.stat().st_size > 0:
                    break
            if attempt + 1 < max_attempts:
                time.sleep(0.5)
        else:
            raise AndroidControllerError(
                "uiautomator dump failed after retries: " f"remote={remote} local={local_path}"
            )

        if remove_remote:
            self.adb_shell(f"rm -f {shlex.quote(remote)}", check=False)
        return local_path

    def get_foreground(self, *, timeout_s: float | None = None) -> Dict[str, Any]:
        """Best-effort foreground app/activity."""

        component: Optional[str] = None
        res = self.adb_shell("dumpsys activity activities", timeout_s=timeout_s, check=False)
        if res.ok() and res.stdout:
            component = _extract_component_from_dumpsys_activity(res.stdout)

        if component is None:
            res2 = self.adb_shell("dumpsys window windows", timeout_s=timeout_s, check=False)
            if res2.ok() and res2.stdout:
                component = _extract_component_from_dumpsys_window(res2.stdout)

        pkg, activity = _parse_component(component) if component else (None, None)
        return {
            "package": pkg,
            "activity": activity,
            "component": component,
        }

    def get_screen_info(self, *, timeout_s: float | None = None) -> Dict[str, Any]:
        size = self.adb_shell("wm size", timeout_s=timeout_s, check=False)
        density = self.adb_shell("wm density", timeout_s=timeout_s, check=False)
        win = self.adb_shell("dumpsys window displays", timeout_s=timeout_s, check=False)
        wm_size = _parse_wm_size(size.stdout if size.ok() else "")
        wm_density = _parse_wm_density(density.stdout if density.ok() else "")
        surface_orientation = _parse_surface_orientation(win.stdout if win.ok() else "")

        display_size: Optional[Tuple[int, int]] = None
        if isinstance(wm_size, dict):
            candidates = (wm_size.get("override_size"), wm_size.get("physical_size"))
            for candidate in candidates:
                if isinstance(candidate, (list, tuple)) and len(candidate) == 2:
                    try:
                        w = int(candidate[0])
                        h = int(candidate[1])
                    except Exception:
                        continue
                    if w > 0 and h > 0:
                        display_size = (w, h)
                        break

        physical_frame_boundary_px = None
        if win.ok() and win.stdout:
            physical_frame_boundary_px = _parse_physical_frame_boundary_px(
                win.stdout,
                surface_orientation=surface_orientation,
                display_size=display_size,
            )

        info: Dict[str, Any] = {
            "wm_size": wm_size,
            "wm_density": wm_density,
            "surface_orientation": surface_orientation,
        }
        if physical_frame_boundary_px is not None:
            info["physical_frame_boundary_px"] = physical_frame_boundary_px
        return info

    def pull_file(
        self, src: str, dst: str | Path, *, timeout_s: float | None = None, check: bool = True
    ) -> AdbResult:
        dst_path = Path(dst)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        return self.adb("pull", str(src), str(dst_path), timeout_s=timeout_s, check=check)

    def push_file(
        self, src: str | Path, dst: str, *, timeout_s: float | None = None, check: bool = True
    ) -> AdbResult:
        src_path = Path(src)
        return self.adb("push", str(src_path), str(dst), timeout_s=timeout_s, check=check)

    def dumpsys(
        self, service: str, *, timeout_s: float | None = None, check: bool = True
    ) -> AdbResult:
        return self.adb_shell(f"dumpsys {shlex.quote(service)}", timeout_s=timeout_s, check=check)

    def content_query(
        self,
        *,
        uri: str,
        projection: str | Sequence[str] | None = None,
        where: str | None = None,
        sort: str | None = None,
        limit: int | None = None,
        timeout_s: float | None = None,
        check: bool = True,
    ) -> AdbResult:
        proj = None
        if isinstance(projection, (list, tuple)):
            proj = ",".join(str(p) for p in projection)
        elif projection is not None:
            proj = str(projection)

        parts: list[str] = ["content", "query", "--uri", str(uri)]
        if proj:
            parts += ["--projection", proj]
        if where:
            parts += ["--where", str(where)]
        if sort:
            parts += ["--sort", str(sort)]
        if limit is not None:
            parts += ["--limit", str(int(limit))]

        cmd = " ".join(shlex.quote(p) for p in parts)
        return self.adb_shell(cmd, timeout_s=timeout_s, check=check)

    def settings_get(
        self,
        *,
        namespace: str,
        key: str,
        timeout_s: float | None = None,
        check: bool = True,
    ) -> AdbResult:
        cmd = " ".join(shlex.quote(p) for p in ("settings", "get", namespace, key))
        return self.adb_shell(cmd, timeout_s=timeout_s, check=check)

    def settings_put(
        self,
        *,
        namespace: str,
        key: str,
        value: str,
        timeout_s: float | None = None,
        check: bool = True,
    ) -> AdbResult:
        cmd = " ".join(shlex.quote(p) for p in ("settings", "put", namespace, key, value))
        return self.adb_shell(cmd, timeout_s=timeout_s, check=check)

    def run_as(
        self,
        *,
        package: str,
        command: str,
        timeout_s: float | None = None,
        check: bool = True,
    ) -> AdbResult:
        cmd = " ".join(shlex.quote(p) for p in ("run-as", package, "sh", "-c", command))
        return self.adb_shell(cmd, timeout_s=timeout_s, check=check)

    def root_shell(
        self,
        command: str,
        *,
        timeout_s: float | None = None,
        check: bool = True,
    ) -> AdbResult:
        cmd = " ".join(shlex.quote(p) for p in ("su", "0", "sh", "-c", command))
        return self.adb_shell(cmd, timeout_s=timeout_s, check=check)

    def probe_env_capabilities(self, *, timeout_s: float = 8.0) -> Dict[str, Any]:
        """Probe Android-side capabilities for oracle gating (best-effort)."""

        notes: list[str] = []

        def _try_shell(cmd: str, *, t: float = timeout_s) -> AdbResult | None:
            try:
                return self.adb_shell(cmd, timeout_s=t, check=False)
            except Exception as e:
                notes.append(f"adb_shell_error:{cmd}:{type(e).__name__}")
                return None

        android: Dict[str, Any] = {
            "available": False,
            "serial": self._serial,
            "adb_path": self._adb_path,
            "adb_version": None,
            "build_fingerprint": None,
            "android_api_level": None,
            "boot_completed": None,
            "root_available": None,
            "run_as_available": None,
            "sdcard_writable": None,
            "can_pull_data": None,
            "can_list_data_data": None,
            "notes": notes,
        }

        try:
            android["adb_version"] = self.adb_version() or None
        except Exception as e:
            notes.append(f"adb_version_error:{type(e).__name__}")

        fp = _try_shell("getprop ro.build.fingerprint")
        if fp and fp.ok():
            fingerprint = fp.stdout.strip() or None
            android["build_fingerprint"] = fingerprint
            android["available"] = bool(fingerprint)
        else:
            notes.append("adb_shell_unavailable")

        bc = _try_shell("getprop sys.boot_completed")
        if bc and bc.ok():
            android["boot_completed"] = (bc.stdout or "").strip() == "1"

        api_level = _try_shell("getprop ro.build.version.sdk")
        if api_level and api_level.ok():
            raw = (api_level.stdout or "").strip()
            try:
                android["android_api_level"] = int(raw) if raw else None
            except Exception:
                android["android_api_level"] = None
                notes.append(f"android_api_level_parse_failed:{raw[:30]}")

        su = _try_shell("su 0 id")
        if su is not None:
            android["root_available"] = bool(su.ok())

        probe_pkg = os.environ.get("MAS_RUNAS_PROBE_PKG")
        if probe_pkg:
            ra = _try_shell(f"run-as {shlex.quote(probe_pkg)} id")
            android["run_as_available"] = bool(ra.ok()) if ra is not None else False
            if ra is not None and not ra.ok() and ra.stderr:
                notes.append(f"run_as_failed:{(ra.stderr or '')[:120]}")
        else:
            android["run_as_available"] = None
            notes.append("run_as_probe_pkg_not_set")

        probe_path = "/sdcard/mas_probe_capabilities.txt"
        w = _try_shell(f"sh -c {shlex.quote(f'echo ok > {probe_path}')}")
        android["sdcard_writable"] = bool(w.ok()) if w is not None else None
        _try_shell(f"rm -f {shlex.quote(probe_path)}")

        # Best-effort probe that `adb pull` works (used by pull-based hard oracles).
        def _try_pull(src: str) -> AdbResult | None:
            try:
                with tempfile.TemporaryDirectory(prefix="mas_pull_probe_") as td:
                    dst = Path(td) / "pull_probe.bin"
                    return self.pull_file(src, dst, timeout_s=timeout_s, check=False)
            except Exception as e:  # pragma: no cover
                notes.append(f"adb_pull_error:{src}:{type(e).__name__}")
                return None

        can_pull = False
        pull_probed = False
        # Prefer a deterministic sdcard file when writable; otherwise probe read-only paths.
        if android.get("sdcard_writable") is True:
            pull_probe_path = "/sdcard/mas_probe_pull.txt"
            w2 = _try_shell(f"sh -c {shlex.quote(f'echo ok > {pull_probe_path}')}")
            if w2 is not None and w2.ok():
                res = _try_pull(pull_probe_path)
                pull_probed = pull_probed or (res is not None)
                if res is not None and res.ok():
                    can_pull = True
                elif res is not None:
                    combined = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
                    if combined:
                        notes.append(f"adb_pull_failed:{pull_probe_path}:{combined[:120]}")
            _try_shell(f"rm -f {shlex.quote(pull_probe_path)}")

        pull_candidates: list[str] = ["/system/build.prop", "/proc/version"]

        for src in pull_candidates:
            if can_pull:
                break
            res = _try_pull(src)
            if res is None:
                continue
            pull_probed = True
            if res.ok():
                can_pull = True
                break
            combined = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
            if combined:
                notes.append(f"adb_pull_failed:{src}:{combined[:120]}")

        android["can_pull_data"] = can_pull if pull_probed else None

        ls = _try_shell("ls /data/data")
        if ls is not None:
            android["can_list_data_data"] = bool(ls.ok())
            if not ls.ok() and ls.stderr:
                notes.append(f"ls_/data/data:{(ls.stderr or '')[:120]}")

        return android

    # ------------------------------- Reset/Snapshot -------------------------------

    def load_snapshot(self, snapshot_tag: str) -> AdbResult:
        """Load an AVD snapshot.

        MobileWorld uses: `adb emu avd snapshot load <tag>` and checks that the
        output contains an OK marker. We mirror that behaviour.
        """

        res = self.adb("emu", "avd", "snapshot", "load", snapshot_tag, check=False)
        out = (res.stdout + "\n" + res.stderr).strip()
        if not res.ok():
            raise AndroidControllerError(
                f"snapshot load failed (rc={res.returncode}): {snapshot_tag}\n{out}"
            )
        # Some emulator versions do not print OK; keep this as evidence but do not fail.
        return res

    # --------------------------- Optional convenience APIs -------------------------

    def get_resumed_activity(self) -> Optional[str]:
        """Best-effort foreground activity.

        This is intentionally best-effort and should not be used as a *hard*
        oracle by itself.
        """

        res = self.adb_shell("dumpsys activity activities", check=False)
        if not res.ok():
            return None
        return _extract_component_from_dumpsys_activity(res.stdout)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_lines(path: Path, lines: Iterable[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(line)
            if not line.endswith("\n"):
                f.write("\n")
