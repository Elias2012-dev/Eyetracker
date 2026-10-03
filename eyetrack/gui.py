"""The settings window: pick a camera, set things up, start and stop tracking.

Tkinter, on purpose. It ships with Python, so the packaged exe stays a
single self-contained file with no new dependency, and it gives real
widgets - listboxes, sliders, checkboxes - rather than another OpenCV
canvas that has to reimplement every control.

The interesting part is that the settings window and the HUD are both just
*front ends*: the actual tracking machine is :class:`eyetrack.session.
TrackingSession`, owned here by a worker thread. This module owns the
camera list, the settings controls and the start/stop state; the HUD window
is borrowed from :class:`eyetrack.overlay.Overlay` and appears while
tracking is running.

Everything that is not a widget lives in :class:`TrackerController`, which
is what the tests drive - a Tk window cannot be asserted on in CI, but a
controller can.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import cv2

from .config import Config
from .overlay import Overlay
from .preview import PREVIEW_MAX_H, PREVIEW_MAX_W, CameraPreview, frame_to_ppm
from .session import TrackingSession

# How often the Tk event loop wakes to poll the worker. Fast enough that the
# HUD looks live, slow enough that the UI thread is not the bottleneck.
TICK_MS = 15

# A camera frame is ~3 MB; only the newest few matter, and a stale frame is
# worse than a dropped one. Two slots keeps the worker from ever blocking.
_QUEUE_DEPTH = 2

# The preview picture is for checking the shot is right, not for judging
# quality, so ~15 fps is plenty and leaves the USB bus to the real capture.
PREVIEW_MS = 66


class TrackerController:
    """Start/stop and settings state, with no Tk in it.

    The GUI is a view over this; the tests drive it directly. Keeping the
    state machine separate is what makes "press Start twice" or "change the
    camera while tracking" testable at all.
    """

    def __init__(self, cfg: Config, config_path: Path) -> None:
        self.cfg = cfg
        self.config_path = Path(config_path)
        self.running = False
        self.status = "Ready"
        self.detail = ""
        self.session: TrackingSession | None = None
        self.last_result = None
        self._error = ""

    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Begin tracking. Returns False (and sets ``status``) on failure."""
        if self.running:
            return True
        session = TrackingSession(
            self.cfg, self.config_path,
            start_wizard=not self.cfg_is_calibrated(),
            recenter_on_start=self.cfg_is_calibrated())
        try:
            session.start()
        except SystemExit as exc:
            # open_camera raises this for an unusable camera; the GUI wants
            # to show the reason, not have the process die.
            self.status = "Could not start"
            self.detail = str(exc)
            self._error = str(exc)
            self.running = False
            return False
        except Exception as exc:                      # pragma: no cover
            self.status = "Could not start"
            self.detail = f"{type(exc).__name__}: {exc}"
            self._error = self.detail
            self.running = False
            return False
        self.session = session
        self.running = True
        self.status = "Tracking"
        self.detail = f"camera {self.cfg.camera.index} - outputs: {session.describe_outputs()}"
        return True

    def stop(self) -> None:
        if self.session is not None:
            self.session.stop()
        self.session = None
        self.running = False
        self.status = "Stopped"
        self.detail = ""

    def toggle(self) -> bool:
        if self.running:
            self.stop()
            return False
        self.start()
        return self.running

    def cfg_is_calibrated(self) -> bool:
        return self.session is not None and self.session.calib.valid

    # ------------------------------------------------------------------
    def apply(self, **changes) -> None:
        """Set config attributes from dotted keys, then persist.

        ``apply(**{"camera.device_name": "Camo", "overlay.compact": True})``
        - the form the settings pane uses, so a row of widgets does not each
        need its own save call.

        The keys are checked against the dataclass fields before anything is
        written. Config is a plain dataclass, so ``setattr`` would happily
        invent an attribute that no code ever reads - the setting would
        appear to work and then vanish on reload, which is the worst kind of
        bug to chase.
        """
        for key, value in changes.items():
            self._resolve(key, value)
        if self.session is not None:
            self.session.save_config()
        else:
            try:
                self.cfg.save(self.config_path)
            except OSError as exc:
                print(f"[eyetrack] warning: could not save config: {exc}")

    def _resolve(self, key: str, value) -> None:
        import dataclasses

        target = self.cfg
        *parts, leaf = key.split(".")
        for part in parts:
            fields = {f.name for f in dataclasses.fields(target)}
            if part not in fields:
                raise KeyError(f"{key!r}: {type(target).__name__} has no "
                               f"{part!r} section")
            target = getattr(target, part)
        fields = {f.name for f in dataclasses.fields(target)}
        if leaf not in fields:
            raise KeyError(f"{key!r}: {type(target).__name__} has no "
                           f"{leaf!r} setting")
        setattr(target, leaf, value)

    def recentre(self) -> bool:
        if self.session is None:
            return False
        return self.session.recentre()

    def begin_calibration(self) -> None:
        if self.session is not None:
            self.session.begin_calibration()

    def submit_calibration(self) -> bool:
        if self.session is None:
            return False
        return self.session.submit_calibration()

    @property
    def wizard(self):
        return self.session.wizard if self.session else None


class TrackerWindow:
    """The Tk window: camera picker, settings, start/stop, live HUD."""

    def __init__(self, cfg: Config, config_path: Path) -> None:
        self.cfg = cfg
        self.config_path = Path(config_path)
        self.controller = TrackerController(cfg, config_path)
        self.root = tk.Tk()
        self.root.title("Eyetracker")
        self.root.minsize(520, 620)
        self._closing = False
        self._overlay: Overlay | None = None
        self._results: queue.Queue = queue.Queue(maxsize=_QUEUE_DEPTH)
        self._preview: CameraPreview | None = None
        self._photo = None          # Tk drops the image if we do not hold it
        self._preview_drawn = 0.0

        self._build()
        self._load_cameras()
        self._sync_from_config()
        self._preview_start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(TICK_MS, self._tick)

    # ==================================================================
    # layout
    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="Eyetracker",
                  font=("Segoe UI", 16, "bold")).pack(side="left")
        self.version_label = ttk.Label(top, text="", foreground="#666")
        self.version_label.pack(side="right")

        # --- start / stop -------------------------------------------
        bar = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        bar.pack(fill="x")
        self.start_button = ttk.Button(bar, text="Start tracking",
                                       command=self.on_start_stop, width=18)
        self.start_button.pack(side="left")
        self.recentre_button = ttk.Button(bar, text="Re-centre",
                                          command=self.on_recentre, state="disabled")
        self.recentre_button.pack(side="left", padx=6)
        self.calibrate_button = ttk.Button(bar, text="Calibrate",
                                           command=self.on_calibrate, state="disabled")
        self.calibrate_button.pack(side="left")
        self.status_label = ttk.Label(bar, text="Ready")
        self.status_label.pack(side="left", padx=12)

        # --- camera ---------------------------------------------------
        cam = ttk.LabelFrame(self.root, text="Camera", padding=10)
        cam.pack(fill="x", padx=12)
        self.camera_list = tk.Listbox(cam, height=4, exportselection=False,
                                      activestyle="none")
        self.camera_list.pack(fill="x")
        self.camera_list.bind("<<ListboxSelect>>", self.on_camera_selected)

        # A name in a list tells you nothing about what the lens sees. This
        # is the picture for the selected camera, at tracking resolution.
        self.preview_box = tk.Frame(cam, bg="#0d1117",
                                    width=PREVIEW_MAX_W, height=PREVIEW_MAX_H,
                                    highlightthickness=1,
                                    highlightbackground="#30363d")
        self.preview_box.pack(pady=(8, 0))
        self.preview_box.pack_propagate(False)   # hold the size we reserved
        self.preview_label = tk.Label(self.preview_box, bg="#0d1117",
                                      fg="#8b949e", text="starting preview...")
        self.preview_label.pack(expand=True, fill="both")
        self.preview_info = ttk.Label(cam, text="", foreground="#666")
        self.preview_info.pack(anchor="w")

        row = ttk.Frame(cam)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Mode:").pack(side="left")
        self.mirror_var = tk.BooleanVar(value=self.cfg.camera.mirror)
        ttk.Checkbutton(row, text="Mirror preview", variable=self.mirror_var,
                        command=self.on_mirror).pack(side="left")

        res = ttk.Frame(cam)
        res.pack(fill="x", pady=(6, 0))
        ttk.Label(res, text="Capture:").pack(side="left")
        self.res_var = tk.StringVar(
            value=f"{self.cfg.camera.width}x{self.cfg.camera.height}"
                  f" @ {self.cfg.camera.fps}")
        combo = ttk.Combobox(res, textvariable=self.res_var, state="readonly",
                             width=18,
                             values=["640x480 @ 30", "1280x720 @ 30",
                                     "1280x720 @ 60", "1920x1080 @ 30"])
        # The selection event, not a `command` option: ttk.Combobox rejects
        # that option, and the event fires for keyboard picks too.
        combo.bind("<<ComboboxSelected>>", self.on_resolution)
        combo.pack(side="left")

        # --- outputs --------------------------------------------------
        out = ttk.LabelFrame(self.root, text="Game outputs", padding=10)
        out.pack(fill="x", padx=12, pady=(8, 0))
        self.game_link_var = tk.BooleanVar(value=self.cfg.game_link.enabled)
        self.udp_var = tk.BooleanVar(value=self.cfg.udp_json.enabled)
        self.mouse_var = tk.BooleanVar(value=self.cfg.mouse.enabled)
        for text, var in (("TrackIR games (ETS2, MSFS, DCS, ...)", self.game_link_var),
                          ("Minecraft (UDP)", self.udp_var),
                          ("Mouse look (any game) - F9 to arm", self.mouse_var)):
            ttk.Checkbutton(out, text=text, variable=var,
                            command=self.on_outputs).pack(anchor="w")

        # --- display --------------------------------------------------
        disp = ttk.LabelFrame(self.root, text="Display", padding=10)
        disp.pack(fill="x", padx=12, pady=(8, 0))
        self.compact_var = tk.BooleanVar(value=self.cfg.overlay.compact)
        self.mesh_var = tk.BooleanVar(value=self.cfg.overlay.show_mesh)
        self.top_most_var = tk.BooleanVar(value=self.cfg.overlay.top_most)
        for text, var in (("Compact HUD (small numbers-only panel)", self.compact_var),
                          ("Show face mesh", self.mesh_var),
                          ("Keep HUD above other windows", self.top_most_var)):
            ttk.Checkbutton(disp, text=text, variable=var,
                            command=self.on_display).pack(anchor="w")

        # --- feel ------------------------------------------------------
        feel = ttk.LabelFrame(self.root, text="Feel", padding=10)
        feel.pack(fill="x", padx=12, pady=(8, 0))
        grid = ttk.Frame(feel)
        grid.pack(fill="x")
        ttk.Label(grid, text="Left / right range").grid(row=0, column=0, sticky="w")
        self.yaw_var = tk.DoubleVar(value=self.cfg.pose.yaw_range)
        ttk.Scale(grid, from_=15, to=90, orient="horizontal", variable=self.yaw_var,
                  length=200, command=self.on_ranges).grid(row=0, column=1, sticky="w")
        self.yaw_label = ttk.Label(grid, text="")
        self.yaw_label.grid(row=0, column=2, padx=8)

        ttk.Label(grid, text="Up / down range").grid(row=1, column=0, sticky="w", pady=4)
        self.pitch_var = tk.DoubleVar(value=self.cfg.pose.pitch_range)
        ttk.Scale(grid, from_=10, to=70, orient="horizontal",
                  variable=self.pitch_var, length=200,
                  command=self.on_ranges).grid(row=1, column=1, sticky="w", pady=4)
        self.pitch_label = ttk.Label(grid, text="")
        self.pitch_label.grid(row=1, column=2, padx=8, pady=4)

        smooth = ttk.Frame(feel)
        smooth.pack(fill="x", pady=(8, 0))
        ttk.Label(smooth, text="Smoothing").pack(side="left")
        self.cutoff_var = tk.DoubleVar(value=self.cfg.filter.min_cutoff)
        ttk.Scale(smooth, from_=0.2, to=5.0, orient="horizontal",
                  variable=self.cutoff_var, length=200,
                  command=self.on_smoothing).pack(side="left", padx=8)
        self.cutoff_label = ttk.Label(smooth, text="")
        self.cutoff_label.pack(side="left")

        footer = ttk.Frame(self.root, padding=(12, 10, 12, 12))
        footer.pack(fill="x")
        self.detail_label = ttk.Label(footer, text="", foreground="#666",
                                      wraplength=480, justify="left")
        self.detail_label.pack(fill="x")
        ttk.Label(footer, text="C calibrate   R recentre   H help   Q quit   "
                               "F full/compact", foreground="#888").pack(
            anchor="w", pady=(8, 0))

    # ==================================================================
    # camera list
    def _load_cameras(self) -> None:
        from .cameras import list_devices

        self.camera_list.delete(0, "end")
        devices = list_devices()
        for dev in devices:
            self.camera_list.insert("end", str(dev))
        if not devices:
            self.camera_list.insert("end", "(no cameras reported by Windows)")
        self._select_configured()

    def _select_configured(self) -> None:
        name = self.cfg.camera.device_name
        if name:
            for i, dev in enumerate(self._devices()):
                if dev.name == name:
                    self.camera_list.selection_clear(0, "end")
                    self.camera_list.selection_set(i)
                    self.camera_list.see(i)
                    return
        for i in range(self.camera_list.size()):
            if self.camera_list.get(i).startswith(f"[{self.cfg.camera.index}]"):
                self.camera_list.selection_clear(0, "end")
                self.camera_list.selection_set(i)
                return

    @staticmethod
    def _devices():
        from .cameras import list_devices

        return list_devices()

    # ==================================================================
    # config -> widgets
    def _sync_from_config(self) -> None:
        self.mirror_var.set(self.cfg.camera.mirror)
        self.compact_var.set(self.cfg.overlay.compact)
        self.mesh_var.set(self.cfg.overlay.show_mesh)
        self.top_most_var.set(self.cfg.overlay.top_most)
        self.game_link_var.set(self.cfg.game_link.enabled)
        self.udp_var.set(self.cfg.udp_json.enabled)
        self.mouse_var.set(self.cfg.mouse.enabled)
        self.yaw_var.set(self.cfg.pose.yaw_range)
        self.pitch_var.set(self.cfg.pose.pitch_range)
        self.cutoff_var.set(self.cfg.filter.min_cutoff)
        self.res_var.set(f"{self.cfg.camera.width}x{self.cfg.camera.height}"
                         f" @ {self.cfg.camera.fps}")
        self._update_range_labels()
        self._update_smoothing_label()
        from . import __version__
        self.version_label.config(text=f"v{__version__}")

    def _update_range_labels(self) -> None:
        self.yaw_label.config(text=f"{self.yaw_var.get():.0f}°")
        self.pitch_label.config(text=f"{self.pitch_var.get():.0f}°")

    def _update_smoothing_label(self) -> None:
        v = self.cutoff_var.get()
        if v < 1.0:
            word = "smooth"
        elif v < 2.5:
            word = "balanced"
        else:
            word = "responsive"
        self.cutoff_label.config(text=f"{word}")

    # ==================================================================
    # widget callbacks
    def on_start_stop(self) -> None:
        if self.controller.running:
            self.controller.stop()
            self._teardown_overlay()
            self._preview_start()
        else:
            self._apply_pending()
            # The camera is exclusive: the preview must let go before the
            # session tries to claim it, or Start fails for no visible reason.
            self._preview_stop()
            if not self.controller.start():
                self._preview_start()
                self._refresh_status()
                return
            self._setup_overlay()
        self._refresh_status()

    def on_recentre(self) -> None:
        if not self.controller.recentre():
            self.controller.detail = "no face detected yet - sit in frame first"
        self._refresh_status()

    def on_calibrate(self) -> None:
        self.controller.begin_calibration()
        self._refresh_status()

    def on_camera_selected(self, _event=None) -> None:
        selection = self.camera_list.curselection()
        if not selection:
            return
        text = self.camera_list.get(selection[0])
        if not text.startswith("["):
            return
        devices = self._devices()
        if selection[0] >= len(devices):
            return
        dev = devices[selection[0]]
        stopped = False
        if self.controller.running:
            # Changing the camera mid-flight means a new session; the old
            # one has to be torn down first or the device stays locked.
            self.controller.stop()
            self._teardown_overlay()
            stopped = True
        self.controller.apply(**{"camera.index": dev.index,
                                 "camera.device_name": dev.name})
        self.controller.detail = f"selected {dev.name}"
        if stopped:
            self.controller.detail += " - tracking stopped, press Start"
        self._preview_start()
        self._refresh_status()

    def on_mirror(self) -> None:
        self.controller.apply(**{"camera.mirror": bool(self.mirror_var.get())})
        self._preview_drawn = 0.0        # repaint flipped on the next tick

    def on_resolution(self, _event=None) -> None:
        try:
            w, rest = self.res_var.get().split("x")
            h, rest = rest.split(" ")
            fps = int(rest.replace("@", "").strip())
        except ValueError:
            return
        self.controller.apply(**{"camera.width": int(w), "camera.height": int(h),
                                 "camera.fps": fps})
        self._preview_start()

    def on_outputs(self) -> None:
        # Output construction happens when a session starts, so a change
        # here only takes effect on the next Start - say so rather than
        # pretending it is already live.
        self.controller.apply(**{
            "game_link.enabled": bool(self.game_link_var.get()),
            "udp_json.enabled": bool(self.udp_var.get()),
            "mouse.enabled": bool(self.mouse_var.get())})
        self.controller.detail = ("outputs change when you press Start again"
                                  if self.controller.running else "")

    def on_display(self) -> None:
        self.controller.apply(**{
            "overlay.compact": bool(self.compact_var.get()),
            "overlay.show_mesh": bool(self.mesh_var.get()),
            "overlay.top_most": bool(self.top_most_var.get())})
        if self._overlay is not None:
            self._overlay.set_compact(bool(self.compact_var.get()))
            self._overlay.show_mesh = bool(self.mesh_var.get())
            self._overlay.top_most = bool(self.top_most_var.get())
            self._overlay._topmost_applied = False

    def on_ranges(self, _value=None) -> None:
        self.controller.apply(**{
            "pose.yaw_range": round(self.yaw_var.get()),
            "pose.pitch_range": round(self.pitch_var.get())})
        self._update_range_labels()

    def on_smoothing(self, _value=None) -> None:
        self.controller.apply(**{"filter.min_cutoff": round(self.cutoff_var.get(), 2)})
        self._update_smoothing_label()

    def _apply_pending(self) -> None:
        """Push the widget values into the config before a Start."""
        self.on_mirror()
        self.on_resolution()
        self.on_outputs()
        self.on_display()
        self.on_ranges()
        self.on_smoothing()

    # ==================================================================
    # the HUD, borrowed from the overlay module
    def _setup_overlay(self) -> None:
        self._overlay = Overlay(self.cfg.overlay.window, self.cfg.overlay.show_mesh,
                                top_most=self.cfg.overlay.top_most,
                                compact=self.cfg.overlay.compact,
                                yaw_range=self.cfg.pose.yaw_range,
                                pitch_range=self.cfg.pose.pitch_range,
                                roll_range=self.cfg.pose.roll_range)
        self._worker = threading.Thread(target=self._run_session, daemon=True)
        self._worker.start()

    def _teardown_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.close()
            self._overlay = None

    def _run_session(self) -> None:
        """Own the session; hand frames to the Tk thread through a queue."""
        assert self.controller.session is not None
        session = self.controller.session
        while self.controller.running and not self._closing:
            res = session.step()
            self.controller.last_result = res
            while True:
                try:
                    self._results.put_nowait(res)
                    break
                except queue.Full:
                    try:
                        self._results.get_nowait()      # drop the stale frame
                    except queue.Empty:                  # pragma: no cover
                        pass
            if res.message:
                self.controller.detail = res.message
            if not res.ok:
                time.sleep(0.05)

    # ==================================================================
    # live preview
    def _preview_start(self) -> None:
        """(Re)open the preview on whatever camera is now selected."""
        self._preview_stop()
        if self.controller.running or self._closing:
            return          # the session owns the device while tracking
        self._preview = CameraPreview(self.cfg)
        if not self._preview.open():
            self._preview_message(self._preview.error
                                  or "this camera would not open")
            self._preview = None
            return
        self._preview_drawn = 0.0

    def _preview_stop(self) -> None:
        if self._preview is not None:
            self._preview.close()
            self._preview = None
        self._photo = None
        self.preview_label.config(image="", text="preview paused\nwhile tracking")
        self.preview_info.config(text="the HUD shows the feed while tracking")

    def _preview_message(self, text: str) -> None:
        self._photo = None
        self.preview_label.config(image="", text=text)
        self.preview_info.config(text="")

    def _preview_pump(self) -> None:
        prev = self._preview
        if prev is None or self.controller.running:
            return
        now = time.monotonic()
        if now - self._preview_drawn < PREVIEW_MS / 1000.0:
            return
        self._preview_drawn = now
        frame = prev.read()
        if frame is None:
            self._preview_stop()
            self._preview_message("no frames from this camera\n"
                                  "try another one")
            return
        try:
            payload, out_w, out_h = frame_to_ppm(frame,
                                                 mirror=self.cfg.camera.mirror)
        except (ValueError, cv2.error) as exc:
            self._preview_message(f"could not draw preview:\n{exc}")
            return
        self._photo = tk.PhotoImage(data=payload)
        self.preview_label.config(image=self._photo, text="")
        # Name the real capture size, not the scaled-down preview size, and
        # say so when the aspect leaves bars - otherwise the letterboxing
        # looks like a rendering fault rather than a 16:9 camera in a 4:3 box.
        src = f"{frame.shape[1]}x{frame.shape[0]}"
        bars = "" if (out_w, out_h) == (PREVIEW_MAX_W, PREVIEW_MAX_H) \
            else f"  (preview {out_w}x{out_h})"
        self.preview_info.config(text=f"{src}  ->  sent to the game{bars}")

    # ==================================================================
    # the Tk tick: drain the queue, draw the HUD, route keys
    def _tick(self) -> None:
        if self._closing:
            return
        self._preview_pump()
        try:
            while True:
                res = self._results.get_nowait()
        except queue.Empty:
            res = None

        if self.controller.running and self._overlay is not None:
            if res is None:
                res = self.controller.last_result
            if res is not None and res.frame is not None:
                key = self._overlay.draw(
                    res.frame, res.pose, res.values, res.tracking, res.fps,
                    self.cfg.camera.mirror, self.controller.wizard, "",
                    self.controller.session.chips(res.tracking))
                self._route_key(key)
        self.root.after(TICK_MS, self._tick)

    def _route_key(self, key: int) -> None:
        if key < 0 or self.controller.session is None:
            return
        session = self.controller.session
        if self._overlay is not None and self._overlay.window_closed():
            self.on_start_stop()          # closing the HUD stops tracking
            return
        if key in (ord("q"), 27):
            self.on_start_stop()
        elif key == ord("c"):
            self.on_calibrate()
        elif key == ord("r"):
            self.on_recentre()
        elif key == ord("h") and self._overlay is not None:
            self._overlay.toggle_help()
        elif key == ord("m") and self._overlay is not None:
            self._overlay.toggle_mesh()
            self.mesh_var.set(self._overlay.show_mesh)
        elif key == ord("f") and self._overlay is not None:
            compact = self._overlay.set_compact(not self._overlay.compact)
            self.compact_var.set(compact)
            self.controller.apply(**{"overlay.compact": compact})
        elif key == ord(" ") and session.wizard is not None:
            session.submit_calibration()

    # ==================================================================
    def _refresh_status(self) -> None:
        running = self.controller.running
        self.start_button.config(text="Stop tracking" if running else "Start tracking")
        state = "normal" if running else "disabled"
        self.recentre_button.config(state=state)
        self.calibrate_button.config(state=state)
        self.status_label.config(text=self.controller.status)
        colour = "#1a7f37" if running else ("#b35900" if self.controller._error else "#444")
        self.status_label.config(foreground=colour)
        self.detail_label.config(text=self.controller.detail)
        self._error = ""

    def on_close(self) -> None:
        self._closing = True
        self.controller.stop()
        self._teardown_overlay()
        self._preview_stop()
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def _tk_available() -> bool:
    """True when a Tk display can actually be opened."""
    if sys.platform not in ("win32", "darwin", "linux"):
        return False
    try:
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


def main(cfg: Config, config_path: Path) -> int:
    """Open the settings window. Returns a process exit code."""
    if not _tk_available():
        print("[gui] no window system available - falling back to the "
              "tracker window. Use --no-gui to skip this check.")
        from .app import run
        return run(cfg, config_path=config_path)
    return TrackerWindow(cfg, config_path).run()