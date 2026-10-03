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
from .theme import (ACCENT_DIM, BG, CARD, CARD_HI, FONT_SMALL, LINE, MUTED,
                    TEXT, StatusPill, Toggle, apply_theme)

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
        if session.wizard is not None:
            # First run: the wizard is already up, and each step captures
            # itself once you hold still. No button to find, no keys to press.
            self.status = "Calibrating"
            self.detail = ("first run - follow the prompts and hold each pose "
                           "still; it captures itself. ESC skips.")
        else:
            self.status = "Tracking"
            self.detail = (f"camera {self.cfg.camera.index} - outputs: "
                           f"{session.describe_outputs()}")
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
        self.root.minsize(600, 560)
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
        self._center_on_screen()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(TICK_MS, self._tick)

    def _center_on_screen(self) -> None:
        """Put the window somewhere it is actually visible.

        Tk's default placement is the top-left of the work area, which is
        fine for a short dialog and useless for this one: at ~950 px tall
        the bottom half lands off the bottom of a 1080p screen. Measured on
        the packaged exe, it opened with only the top 200 px showing.
        """
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        if width <= 1 or height <= 1 or screen_w <= 1 or screen_h <= 1:
            return               # no usable geometry yet; leave Tk's default
        x = max(0, (screen_w - width) // 2)
        # Bias upward: the tracker HUD opens near the top of the screen and
        # an always-on-top HUD would otherwise land on top of this window.
        y = max(0, min((screen_h - height) // 2, (screen_h - height) // 4))
        self.root.geometry(f"+{x}+{y}")

# ==================================================================
    # layout
    def _section(self, parent, title: str):
        """A titled card. The stock LabelFrame draws a grey etched box with a
        punched-out title; on a dark background that reads as a hole, so the
        card is drawn flat with the title in small caps above the content."""
        card = tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=LINE, highlightcolor=LINE)
        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=14, pady=(12, 0))
        ttk.Label(head, text=title.upper(), style="Muted.TLabel").pack(side="left")
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="both", expand=True, padx=14, pady=(8, 14))
        card.body = body
        return card

    def _toggle_row(self, parent, text: str, var, command=None):
        """A label with a pill switch on the right.

        ttk.Checkbutton draws a Windows box that clam will not recolour, and
        a row of six identical grey boxes was the least designed part of the
        old window."""
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=3)
        lbl = ttk.Label(row, text=text, style="TLabel")
        lbl.pack(side="left")
        sw = Toggle(row, var, command=command, on_text="on", off_text="off")
        sw.canvas.pack(side="right")
        # Clicking the label is what people actually try.
        lbl.bind("<Button-1>", lambda _e: sw._click())
        lbl.configure(cursor="hand2")
        sw.canvas.configure(cursor="hand2")
        return sw

    def _build(self) -> None:
        self.style = apply_theme(self.root)

        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        pad = tk.Frame(outer, bg=BG, padx=20, pady=14)
        pad.pack(fill="both", expand=True)

        # --- header ----------------------------------------------------
        top = tk.Frame(pad, bg=BG)
        top.pack(fill="x")
        titles = tk.Frame(top, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="EYETRACKER", bg=BG, fg=TEXT,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(titles, text="head tracking from any webcam", bg=BG,
                 fg=MUTED, font=FONT_SMALL).pack(anchor="w")
        self.pill = StatusPill(top, text="Ready")
        self.pill.canvas.pack(side="right", anchor="n", pady=(2, 0))
        self.version_label = ttk.Label(top, text="", style="Muted.TLabel")
        self.version_label.pack(side="right", padx=(0, 14))

        # --- start / stop ---------------------------------------------
        bar = tk.Frame(pad, bg=BG)
        bar.pack(fill="x", pady=(16, 14))
        self.start_button = ttk.Button(bar, text="Start tracking",
                                       style="Primary.TButton",
                                       command=self.on_start_stop)
        self.start_button.pack(side="left")
        self.recentre_button = ttk.Button(bar, text="Re-centre",
                                          command=self.on_recentre,
                                          state="disabled")
        self.recentre_button.pack(side="left", padx=(8, 0))
        self.calibrate_button = ttk.Button(bar, text="Calibrate",
                                           command=self.on_calibrate,
                                           state="disabled")
        self.calibrate_button.pack(side="left", padx=(8, 0))

        # --- camera ---------------------------------------------------
        cam_card = self._section(pad, "Camera")
        cam_card.pack(fill="x")
        cam = cam_card.body
        self.camera_list = tk.Listbox(cam, height=4, exportselection=False,
                                      activestyle="none", bd=0,
                                      highlightthickness=1,
                                      highlightbackground=LINE,
                                      bg=CARD, fg=TEXT,
                                      selectbackground=ACCENT_DIM,
                                      selectforeground=TEXT)
        self.camera_list.pack(fill="x")
        self.camera_list.bind("<<ListboxSelect>>", self.on_camera_selected)

        # A name in a list tells you nothing about what the lens sees.
        self.preview_box = tk.Frame(cam, bg="#0B0A0C",
                                    height=PREVIEW_MAX_H,
                                    highlightthickness=1,
                                    highlightbackground=LINE)
        self.preview_box.pack(fill="x", pady=(10, 0))
        self.preview_box.pack_propagate(False)
        self.preview_label = tk.Label(self.preview_box, bg="#0B0A0C",
                                      fg=MUTED, font=FONT_SMALL,
                                      text="starting preview...")
        self.preview_label.pack(expand=True, fill="both")
        self.preview_info = ttk.Label(cam, text="", style="Muted.TLabel")
        self.preview_info.pack(anchor="w", pady=(6, 0))

        opts = tk.Frame(cam, bg=CARD)
        opts.pack(fill="x", pady=(10, 0))
        left = tk.Frame(opts, bg=CARD)
        left.pack(side="left")
        ttk.Label(left, text="Capture size", style="Muted.TLabel").pack(anchor="w")
        self.res_var = tk.StringVar(
            value=f"{self.cfg.camera.width}x{self.cfg.camera.height}"
                  f" @ {self.cfg.camera.fps}")
        combo = ttk.Combobox(left, textvariable=self.res_var, state="readonly",
                             width=16,
                             values=["640x480 @ 30", "1280x720 @ 30",
                                     "1280x720 @ 60", "1920x1080 @ 30"])
        # The selection event, not a `command` option: ttk.Combobox rejects
        # that option, and the event fires for keyboard picks too.
        combo.bind("<<ComboboxSelected>>", self.on_resolution)
        combo.pack(anchor="w", pady=(4, 0))

        right = tk.Frame(opts, bg=CARD)
        right.pack(side="left", padx=(22, 0), anchor="n")
        ttk.Label(right, text="Preview", style="Muted.TLabel").pack(anchor="w")
        self.mirror_var = tk.BooleanVar(value=self.cfg.camera.mirror)
        self._toggle_row(right, "Mirror the picture", self.mirror_var,
                         self.on_mirror)

        # --- game outputs + display, side by side ----------------------
        # Stacked, the window came to 1170 px: taller than a 1080p screen.
        # These two are short lists of toggles, so they share a row.
        mid = tk.Frame(pad, bg=BG)
        mid.pack(fill="x", pady=(12, 0))

        out_card = self._section(mid, "Game outputs")
        out_card.pack(side="left", fill="both", expand=True)
        out = out_card.body
        self.game_link_var = tk.BooleanVar(value=self.cfg.game_link.enabled)
        self.udp_var = tk.BooleanVar(value=self.cfg.udp_json.enabled)
        self.mouse_var = tk.BooleanVar(value=self.cfg.mouse.enabled)
        for text, var, hint in (
                ("ETS2, MSFS, DCS, X-Plane", self.game_link_var,
                 "head tracking"),
                ("Minecraft", self.udp_var, "needs the mod"),
                ("Any other game", self.mouse_var, "F9 to arm")):
            row = tk.Frame(out, bg=CARD)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=text, style="TLabel").pack(side="left")
            ttk.Label(row, text=hint, style="Muted.TLabel").pack(
                side="left", padx=(8, 0))
            Toggle(row, var, command=self.on_outputs).canvas.pack(side="right")

        disp_card = self._section(mid, "Display")
        disp_card.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.compact_var = tk.BooleanVar(value=self.cfg.overlay.compact)
        self.mesh_var = tk.BooleanVar(value=self.cfg.overlay.show_mesh)
        self.top_most_var = tk.BooleanVar(value=self.cfg.overlay.top_most)
        for text, var in (("Compact HUD", self.compact_var),
                          ("Face mesh", self.mesh_var),
                          ("Always on top", self.top_most_var)):
            self._toggle_row(disp_card.body, text, var, self.on_display)

        # --- feel ------------------------------------------------------
        feel_card = self._section(pad, "Feel")
        feel_card.pack(fill="x", pady=(12, 0))
        feel = feel_card.body
        grid = tk.Frame(feel, bg=CARD)
        grid.pack(fill="x")

        def slider_row(row, label, variable, command, value_label,
                       from_, to):
            ttk.Label(grid, text=label, style="Muted.TLabel").grid(
                row=row, column=0, sticky="w", pady=4)
            ttk.Scale(grid, from_=from_, to=to, orient="horizontal",
                      variable=variable, length=180,
                      command=command).grid(row=row, column=1, sticky="w",
                                            padx=(12, 10), pady=4)
            value_label.grid(row=row, column=2, sticky="w", pady=4)

        self.yaw_var = tk.DoubleVar(value=self.cfg.pose.yaw_range)
        self.yaw_label = ttk.Label(grid, text="", style="Value.TLabel")
        slider_row(0, "Left / right", self.yaw_var, self.on_ranges,
                   self.yaw_label, 15, 90)
        self.pitch_var = tk.DoubleVar(value=self.cfg.pose.pitch_range)
        self.pitch_label = ttk.Label(grid, text="", style="Value.TLabel")
        slider_row(1, "Up / down", self.pitch_var, self.on_ranges,
                   self.pitch_label, 10, 70)
        self.cutoff_var = tk.DoubleVar(value=self.cfg.filter.min_cutoff)
        self.cutoff_label = ttk.Label(grid, text="", style="Value.TLabel")
        slider_row(2, "Smoothing", self.cutoff_var, self.on_smoothing,
                   self.cutoff_label, 0.2, 5.0)

        # --- footer ----------------------------------------------------
        self.detail_label = ttk.Label(pad, text="", style="Muted.TLabel",
                                      wraplength=660, justify="left")
        self.detail_label.pack(fill="x", pady=(14, 0))
        keys = tk.Frame(pad, bg=BG)
        keys.pack(fill="x", pady=(10, 0))
        for i, (key, what) in enumerate((("C", "calibrate"), ("R", "recentre"),
                                         ("H", "help"), ("F", "compact"),
                                         ("Q", "quit"))):
            if i:
                ttk.Label(keys, text="   ", style="Muted.TLabel").pack(side="left")
            chip = tk.Frame(keys, bg=CARD_HI, highlightthickness=1,
                            highlightbackground=LINE)
            chip.pack(side="left")
            tk.Label(chip, text=key, bg=CARD_HI, fg=TEXT,
                     font=("Consolas", 9, "bold"), padx=5, pady=1).pack(side="left")
            tk.Label(chip, text=what, bg=CARD_HI, fg=MUTED,
                     font=FONT_SMALL, padx=4, pady=1).pack(side="left")

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
        errored = bool(self.controller._error)
        tone = "warn" if errored else ("live" if running else "idle")
        self.pill.set(self.controller.status, tone)
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