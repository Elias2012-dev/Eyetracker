"""The settings window's dark theme.

The HUD already had a palette (a warm near-black plum with green and amber
accents); this mirrors it so the two windows look like one program.

Tk on Windows ships a light theme and no way to restyle most native
controls, so this module does two things:

* points the ``clam`` theme - the only built-in theme Tk lets you recolour
  freely - at these colours, for the controls that cannot be hand-drawn
  (listbox, sliders, combobox, scrollbars);
* hand-draws the rest on a ``Canvas`` (toggles, pills, section rules),
  because that is the only way to get a rounded, coloured control out of
  Tkinter at all.

The palette is data, and :func:`contrast_ratio` is a pure function, so the
colour choices are tested rather than eyeballed.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# --- palette -----------------------------------------------------------
# Deliberately the HUD's colours (overlay.py holds them as BGR tuples):
# the settings window and the tracker HUD should read as one program.
BG = "#180F12"        # window backdrop
CARD = "#27191E"      # raised panel fill
CARD_HI = "#33242C"    # panel fill on hover / pressed
LINE = "#634B54"      # hairline borders
LINE_SOFT = "#3E2F37"
TEXT = "#FAF5F3"      # primary text
MUTED = "#B6A2A6"     # secondary text
ACCENT = "#94E27A"    # "tracking / go" green, same as the HUD's OK pill
ACCENT_DIM = "#3E5C38"
WARN = "#F0AA56"      # amber, same as the HUD's warning pill
ON_BADGE = "#0E1A0B"  # text drawn on top of ACCENT

FONT_STACK = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 19, "bold")
FONT_SMALL = ("Segoe UI", 9)


# --- contrast ----------------------------------------------------------
def _channel(value: int) -> float:
    srgb = value / 255.0
    return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance of an ``#rrggbb`` string."""
    c = colour.lstrip("#")
    if len(c) != 6:
        raise ValueError(f"expected #rrggbb, got {colour!r}")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b))


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours, 1.0 to 21.0."""
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# --- drawing helpers ---------------------------------------------------
def round_rect(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
               radius: float, **kwargs) -> int:
    """A rounded rectangle. Tk has no such primitive, so approximate the
    corners with a smoothed polygon - visually identical at these radii."""
    r = max(1.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    steps = 12
    pts = []
    for cx, cy, start in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                          (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for i in range(steps + 1):
            a = math.radians(start + 90 * i / steps)
            pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
    return canvas.create_polygon(pts, smooth=True, **kwargs)


# --- controls ----------------------------------------------------------
class Toggle:
    """A pill switch on a Canvas.

    ``ttk.Checkbutton`` renders a grey Windows box that cannot be coloured,
    which is the single ugliest thing in the default window. Drawing the
    switch means it can be the same green as the HUD's tracking pill.
    """

    W, H = 46, 25

    def __init__(self, master, variable: tk.BooleanVar, command=None,
                 on_text: str = "", off_text: str = ""):
        self.var = variable
        self.command = command
        self.on_text = on_text
        self.off_text = off_text
        self.canvas = tk.Canvas(master, width=self.W, height=self.H,
                                highlightthickness=0, bd=0,
                                bg=master.cget("bg"))
        self._track = round_rect(self.canvas, 0, 0, self.W, self.H,
                                 self.H / 2, fill=LINE, width=0)
        self._knob = self.canvas.create_oval(2, 2, self.H - 2, self.H - 2,
                                             fill=MUTED, width=0)
        self.canvas.create_text(self.W + 8, self.H / 2, anchor="w",
                                text=off_text, fill=MUTED,
                                font=FONT_SMALL, tags="lbl")
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.var.trace_add("write", lambda *_: self.redraw())
        self.redraw()

    def _click(self, _event=None) -> None:
        self.var.set(not self.var.get())
        if self.command is not None:
            self.command()

    def redraw(self) -> None:
        on = bool(self.var.get())
        self.canvas.itemconfigure(self._track, fill=ACCENT if on else LINE)
        self.canvas.itemconfigure(self._knob,
                                  fill=TEXT if on else MUTED)
        r = self.H / 2 - 2
        cx = (self.W - r - 2) if on else (r + 2)
        self.canvas.coords(self._knob, cx - r, 2, cx + r, self.H - 2)
        label = self.on_text if on else self.off_text
        if label:
            self.canvas.itemconfigure("lbl", text=label,
                                      fill=TEXT if on else MUTED)


class StatusPill:
    """The live state chip in the header: Idle / Tracking / error."""

    W, H = 132, 30

    def __init__(self, master, text="Ready", tone="idle"):
        self.canvas = tk.Canvas(master, width=self.W, height=self.H,
                                highlightthickness=0, bd=0,
                                bg=master.cget("bg"))
        self._body = round_rect(self.canvas, 0, 0, self.W, self.H, self.H / 2,
                                fill=LINE, width=0)
        self._dot = self.canvas.create_oval(11, self.H / 2 - 4,
                                            19, self.H / 2 + 4,
                                            fill=MUTED, width=0)
        self._text = self.canvas.create_text(self.W / 2 + 6, self.H / 2,
                                             text=text, fill=TEXT,
                                             font=FONT_BOLD)
        self.set(text, tone)

    def set(self, text: str, tone: str = "idle") -> None:
        """tone: idle | live | warn. Colour alone never carries the meaning -
        the wording does too, so it stays readable without colour."""
        fill, dot = {
            "idle": (LINE, MUTED),
            "live": (ACCENT_DIM, ACCENT),
            "warn": ("#5A3A1E", WARN),
        }[tone]
        self.canvas.itemconfigure(self._body, fill=fill)
        self.canvas.itemconfigure(self._dot, fill=dot)
        self.canvas.itemconfigure(self._text, text=text)


# --- application -------------------------------------------------------
def apply_theme(root: tk.Misc) -> ttk.Style:
    """Recolour every stock widget. Returns the style for further tweaks."""
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.configure(bg=BG)

    style.configure(".", background=BG, foreground=TEXT,
                    fieldbackground=CARD, bordercolor=LINE,
                    darkcolor=LINE, lightcolor=LINE_SOFT,
                    troughcolor=CARD_HI, focuscolor=ACCENT,
                    font=FONT_STACK)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED, font=FONT_SMALL)
    style.configure("Value.TLabel", foreground=ACCENT, font=FONT_BOLD)
    style.configure("Card.TFrame", background=CARD, relief="flat",
                    borderwidth=1, bordercolor=LINE)
    style.configure("TLabelframe", background=BG, bordercolor=LINE,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                    font=FONT_BOLD)
    style.configure("TButton", background=CARD_HI, foreground=TEXT,
                    bordercolor=LINE, focusthickness=0, padding=(14, 9),
                    relief="flat", font=FONT_STACK)
    style.map("TButton",
              background=[("active", ACCENT_DIM), ("disabled", CARD)],
              foreground=[("disabled", MUTED)])
    style.configure("Primary.TButton", background=ACCENT, foreground=ON_BADGE,
                    bordercolor=ACCENT, padding=(20, 11),
                    font=("Segoe UI", 11, "bold"))
    style.map("Primary.TButton",
              background=[("active", "#7FC967"), ("disabled", ACCENT_DIM)],
              foreground=[("disabled", MUTED)])
    style.configure("TCheckbutton", background=BG, foreground=TEXT,
                    focuscolor=BG)
    style.configure("TRadiobutton", background=BG, foreground=TEXT)
    style.configure("TScale", background=BG, troughcolor=CARD_HI,
                    bordercolor=LINE, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                    foreground=TEXT, arrowcolor=MUTED, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, selectbackground=ACCENT,
                    selectforeground=ON_BADGE, padding=6)
    style.map("TCombobox", fieldbackground=[("readonly", CARD)],
              foreground=[("readonly", TEXT)])
    style.configure("TListbox", background=CARD, foreground=TEXT,
                    selectbackground=ACCENT_DIM, selectforeground=TEXT,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                    activestyle="none")
    style.configure("TScrollbar", background=CARD_HI, troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, lightcolor=CARD_HI,
                    darkcolor=CARD_HI)
    style.configure("Horizontal.TScale", background=BG)
    # Tk widgets (not ttk) need telling individually.
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
    return style


def mono(size: int = 9):
    """A tabular-ish font for numbers that must not jitter."""
    for family in ("Consolas", "Cascadia Mono", "Courier New"):
        try:
            tkfont.Font(family=family, size=size)
            return (family, size)
        except tk.TclError:
            continue
    return FONT_STACK