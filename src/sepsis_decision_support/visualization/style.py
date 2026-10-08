"""One place for chart colours, line weights and fonts.

The three series colours are the first three slots of a palette validated for colour
vision deficiency on every pair (blue, orange, aqua; OKLab ΔE >= 9 under protanopia and
deuteranopia). The project's navy and copper brand colours failed that check for series
use, so they stay in page chrome only. Aqua sits below 3:1 contrast on white, so every
chart that uses it also carries direct labels and a table version in the report.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # files only; no window is ever opened
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#ffffff"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

SERIES_COLORS = {
    "d1": "#2a78d6",  # landmark model (the subject of most charts)
    "news2": "#eb6834",
    "sofa": "#1baf7a",
    "development_cv": "#2a78d6",
    "temporal_holdout": "#eb6834",
}
REFERENCE_COLOR = INK_MUTED  # "treat everyone", perfect calibration and other references

SERIES_LABELS = {
    "d1": "Landmark model (D1)",
    "news2": "NEWS2",
    "sofa": "SOFA",
    "development_cv": "Development (cross-validated)",
    "temporal_holdout": "Temporal hold-out (2020 - 2022)",
}

LINE_WIDTH = 2.0
MARKER_SIZE = 6.5  # points; about 9 px at 100 dpi
MARKER_RING = 1.5  # surface-coloured ring around markers


def apply_style() -> None:
    """Set matplotlib defaults: quiet axes, hairline solid grid, system sans."""
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "font.family": "sans-serif",
            "font.size": 10,
            "axes.edgecolor": BASELINE,
            "axes.linewidth": 0.8,
            "axes.labelcolor": INK_SECONDARY,
            "axes.titlecolor": INK_PRIMARY,
            "axes.titlesize": 11,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRIDLINE,
            "grid.linewidth": 0.8,
            "grid.linestyle": "-",
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
            "xtick.labelcolor": INK_SECONDARY,
            "ytick.labelcolor": INK_SECONDARY,
            "legend.frameon": False,
            "legend.fontsize": 9,
            "legend.labelcolor": INK_SECONDARY,
            "lines.linewidth": LINE_WIDTH,
            "lines.solid_capstyle": "round",
            "lines.solid_joinstyle": "round",
        }
    )


def series_line(axis, x, y, key: str, label: str | None = None, with_markers: bool = True) -> None:
    """A 2-px series line with ringed markers, coloured by its fixed series key."""
    axis.plot(
        x,
        y,
        color=SERIES_COLORS[key],
        linewidth=LINE_WIDTH,
        marker="o" if with_markers else None,
        markersize=MARKER_SIZE,
        markeredgecolor=SURFACE,
        markeredgewidth=MARKER_RING,
        label=label or SERIES_LABELS.get(key, key),
        zorder=3,
    )


def end_label(axis, x, y, text: str) -> None:
    """Direct label at a series end, in secondary ink (text never wears the series colour)."""
    axis.annotate(
        text,
        (x, y),
        xytext=(6, 0),
        textcoords="offset points",
        va="center",
        fontsize=9,
        color=INK_SECONDARY,
    )
