"""Santa's Workshop scenes, drawn frame by frame with cairo.

Every scene is a seamless loop: each moving thing repeats a whole number of
times per loop (LOOP_S seconds), so the last frame flows into the first and
the stream can repeat a loop for minutes without a visible seam.

Original characters only: our own elves (no film, brand or trademarked
character), Santa's sleigh as folklore, generic reindeer with no names.
"""
from __future__ import annotations

import math
import random
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cairo

W, H = 1920, 1080
FPS = 24
GOP = 48                 # 2 s, as the cozy loops (live ingest wants 2-4 s keyframes)
LOOP_S = 16              # every scene loop; a multiple of the 2 s GOP
TAU = math.tau
FONT = "DejaVu Sans"

# --- colours ------------------------------------------------------------------------------------------


def rgb(h: str, a: float = 1.0) -> tuple[float, float, float, float]:
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


SKIN = ["#F7CBA4", "#E9AE84", "#C98B62", "#9C6644", "#FBD9BC"]
HAIR = ["#E8873A", "#7A4A24", "#F2C94C", "#3B2416", "#B5532A"]
HATS = ["#D7263D", "#1E9E5A", "#D7263D", "#2B7BD6", "#1E9E5A"]
TUNICS = ["#22A75F", "#D7263D", "#2B9E8C", "#E25B2A", "#7D4CC2"]
TOY = ["#E63946", "#F4A261", "#2A9D8F", "#457B9D", "#FFB703", "#8E5AE8", "#FF6FB5", "#3BB273"]
BULBS = ["#FF4D4D", "#FFD23F", "#3EC300", "#3FA7FF", "#FF7AE0"]


def fill(cr, color, a: float | None = None):
    c = rgb(color) if isinstance(color, str) else color
    if a is not None:
        c = (c[0], c[1], c[2], a)
    cr.set_source_rgba(*c)
    cr.fill()


def stroke(cr, color, width: float, a: float | None = None):
    c = rgb(color) if isinstance(color, str) else color
    if a is not None:
        c = (c[0], c[1], c[2], a)
    cr.set_source_rgba(*c)
    cr.set_line_width(width)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.stroke()


def rrect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -TAU / 4, 0)
    cr.arc(x + w - r, y + h - r, r, 0, TAU / 4)
    cr.arc(x + r, y + h - r, r, TAU / 4, TAU / 2)
    cr.arc(x + r, y + r, r, TAU / 2, 3 * TAU / 4)
    cr.close_path()


def ellipse(cr, x, y, rx, ry):
    cr.save()
    cr.translate(x, y)
    cr.scale(max(rx, 0.01), max(ry, 0.01))
    cr.arc(0, 0, 1, 0, TAU)
    cr.restore()


def star_path(cr, x, y, r_out, r_in, points=5, rot=-TAU / 4):
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = rot + i * TAU / (points * 2)
        (cr.move_to if i == 0 else cr.line_to)(x + r * math.cos(a), y + r * math.sin(a))
    cr.close_path()


def outline_fill(cr, color, line="#3A2318", width=4.0):
    cr.set_source_rgba(*rgb(color))
    cr.fill_preserve()
    stroke(cr, line, width)


def text(cr, s, x, y, size, color="#FFFFFF", *, outline="#5A1E12", ow=10.0, center=True, bold=True, max_w=None):
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    ext = cr.text_extents(s)
    if max_w and ext.width > max_w:
        size *= max_w / ext.width
        ow *= max_w / ext.width
        cr.set_font_size(size)
        ext = cr.text_extents(s)
    tx = x - (ext.width / 2 + ext.x_bearing) if center else x
    ty = y - (ext.height / 2 + ext.y_bearing)
    cr.move_to(tx, ty)
    cr.text_path(s)
    if outline:
        cr.set_source_rgba(*rgb(outline))
        cr.set_line_width(ow)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke_preserve()
    cr.set_source_rgba(*rgb(color))
    cr.fill()
    return ext.width


# --- time helpers: everything periodic in LOOP_S -------------------------------------------------------

def ph(t: float, n: float = 1, off: float = 0.0) -> float:
    """Phase (radians) of something repeating n times per loop."""
    return TAU * (n * t / LOOP_S + off)


def frac(t: float, n: float = 1, off: float = 0.0) -> float:
    return (n * t / LOOP_S + off) % 1.0


def blink(t: float, n: int, off: float) -> float:
    """1 = open, dips to 0.1 briefly n times per loop."""
    f = frac(t, n, off)
    return 0.1 if f < 0.035 else 1.0


def hammer_swing(t: float, n: int, off: float = 0.0) -> tuple[float, bool]:
    """Arm angle for a hammering arm and whether it is the moment of impact."""
    f = frac(t, n, off)
    if f < 0.55:                       # slow lift
        a = 2.3 * (f / 0.55) ** 0.8
    elif f < 0.7:                      # fast strike
        a = 2.3 * (1 - (f - 0.55) / 0.15)
    else:
        a = 0.0
    return 0.35 + a, 0.7 <= f < 0.8


# --- the elf -------------------------------------------------------------------------------------------

@dataclass
class Look:
    skin: str
    hair: str
    hat: str
    tunic: str
    shoe: str = "#7A3E1D"
    legs: str = "#F2F2F2"
    stripe: str = "#D7263D"


def look(i: int) -> Look:
    return Look(SKIN[i % len(SKIN)], HAIR[(i * 2 + 1) % len(HAIR)], HATS[i % len(HATS)],
                TUNICS[(i * 3) % len(TUNICS)], stripe=HATS[(i + 1) % len(HATS)])


def _arm(cr, sx, sy, angle, sleeve, skin, tool: Callable | None, length=64):
    cr.save()
    cr.translate(sx, sy)
    cr.rotate(-angle)
    cr.move_to(0, 0)
    cr.line_to(0, length)
    stroke(cr, "#3A2318", 30)
    cr.move_to(0, 0)
    cr.line_to(0, length)
    stroke(cr, sleeve, 23)
    if tool:
        cr.save()
        cr.translate(0, length + 6)
        tool(cr)
        cr.restore()
    cr.arc(0, length + 6, 13, 0, TAU)
    outline_fill(cr, skin, width=3.5)
    cr.restore()


def elf(cr, x, y, s, lk: Look, t: float, *, arm_l=0.25, arm_r=0.25, tool_l=None, tool_r=None,
        bob=0.0, tilt=0.0, blink_v=1.0, facing=1, sway_off=0.0, mouth="smile", hop=0.0):
    """An elf standing with feet at (x, y). Arm angles are radians from straight
    down, positive swings the hand forward (toward where the elf faces) and up."""
    cr.save()
    cr.translate(x, y - hop)
    cr.scale(s * facing, s)
    # shadow
    cr.save()
    cr.scale(1, 0.25)
    cr.arc(0, 4 * 4 + hop * 4 / s, 62, 0, TAU)
    cr.restore()
    fill(cr, "#000000", 0.18)
    cr.translate(0, bob)
    # legs with stripes
    for lx in (-18, 18):
        rrect(cr, lx - 9, -62, 18, 58, 8)
        outline_fill(cr, lk.legs, width=3.5)
        for k in range(3):
            cr.rectangle(lx - 8, -56 + k * 17, 16, 7)
            fill(cr, lk.stripe)
    # shoes with curled toes
    for lx, d in ((-20, -1), (20, 1)):
        cr.move_to(lx - 14 * d, 0)
        cr.curve_to(lx - 16 * d, -16, lx + 16 * d, -18, lx + 30 * d, -10)
        cr.curve_to(lx + 40 * d, -8, lx + 42 * d, -22, lx + 36 * d, -24)
        cr.curve_to(lx + 46 * d, -22, lx + 46 * d, 2, lx + 26 * d, 2)
        cr.close_path()
        outline_fill(cr, lk.shoe, width=3.5)
        cr.arc(lx + 37 * d, -24, 4.5, 0, TAU)
        fill(cr, "#FFD23F")
    # back arm
    _arm(cr, -36, -142, -arm_l, lk.tunic, lk.skin, tool_l)
    # tunic
    cr.move_to(-36, -152)
    cr.line_to(36, -152)
    cr.line_to(58, -64)
    for k in range(6):                      # zigzag hem
        cr.line_to(58 - (k * 2 + 1) * 116 / 12, -50)
        cr.line_to(58 - (k * 2 + 2) * 116 / 12, -64)
    cr.close_path()
    outline_fill(cr, lk.tunic)
    rrect(cr, -48, -106, 96, 16, 4)
    outline_fill(cr, "#3A2318", width=2)
    rrect(cr, -12, -110, 24, 24, 4)
    stroke(cr, "#FFD23F", 5)
    # collar
    cr.move_to(-40, -156)
    for k in range(5):
        cr.line_to(-40 + (k * 2 + 1) * 8, -136)
        cr.line_to(-40 + (k * 2 + 2) * 8, -156)
    cr.close_path()
    outline_fill(cr, "#FFFFFF", width=3)
    # head (tilted)
    cr.save()
    cr.translate(0, -200)
    cr.rotate(tilt)
    # ears
    for d in (-1, 1):
        cr.move_to(44 * d, -14)
        cr.curve_to(70 * d, -30, 86 * d, -40, 92 * d, -48)
        cr.curve_to(80 * d, -14, 66 * d, 6, 42 * d, 8)
        cr.close_path()
        outline_fill(cr, lk.skin)
        cr.move_to(50 * d, -8)
        cr.curve_to(66 * d, -20, 74 * d, -28, 80 * d, -36)
        stroke(cr, "#E59A8A", 4)
    cr.arc(0, 0, 50, 0, TAU)
    outline_fill(cr, lk.skin)
    # hair tufts
    for hx in (-38, -22, 22, 38):
        ellipse(cr, hx, -30, 14, 10)
        fill(cr, lk.hair)
    # cheeks
    for d in (-1, 1):
        ellipse(cr, 31 * d, 14, 11, 7)
        fill(cr, "#FF7F8E", 0.55)
    # eyes
    for d in (-1, 1):
        ellipse(cr, 17 * d + 4, -4, 8, 10.5 * blink_v)
        fill(cr, "#2B1A12")
        if blink_v > 0.5:
            cr.arc(17 * d + 6.5, -8, 3.2, 0, TAU)
            fill(cr, "#FFFFFF")
    # brows
    for d in (-1, 1):
        cr.move_to(17 * d - 5, -22)
        cr.curve_to(17 * d, -26, 17 * d + 8, -26, 17 * d + 13, -22)
        stroke(cr, lk.hair, 4)
    # nose + mouth
    cr.arc(6, 7, 7, 0, TAU)
    fill(cr, "#E8907A")
    if mouth == "open":
        ellipse(cr, 4, 24, 10, 8)
        outline_fill(cr, "#7A1E2C", width=3)
    else:
        cr.arc(4, 16, 15, 0.15 * math.pi, 0.85 * math.pi)
        stroke(cr, "#5A2A1C", 4.5)
    # hat: cone with a swaying, curling tip and a bell
    sway = 22 * math.sin(ph(t, 2, sway_off))
    tipx, tipy = 30 + sway, -150
    cr.move_to(-54, -26)
    cr.curve_to(-40, -90, -10, -130, tipx, tipy)
    cr.curve_to(tipx + 10, tipy + 30, 40, -70, 54, -26)
    cr.close_path()
    outline_fill(cr, lk.hat)
    rrect(cr, -60, -40, 120, 26, 13)
    outline_fill(cr, "#FFFFFF", width=3.5)
    cr.arc(tipx + 6, tipy + 6, 12, 0, TAU)
    outline_fill(cr, "#FFD23F", width=3)
    cr.arc(tipx + 6, tipy + 10, 3, 0, TAU)
    fill(cr, "#7A4A00")
    cr.restore()
    # front arm (over the body)
    _arm(cr, 36, -142, arm_r, lk.tunic, lk.skin, tool_r)
    cr.restore()


# tools, drawn in the hand's frame (pointing down the arm's direction)

def tool_hammer(cr):
    cr.rectangle(-5, -6, 10, 46)
    outline_fill(cr, "#A0662E", width=3)
    rrect(cr, -24, 34, 48, 22, 5)
    outline_fill(cr, "#8A8F99", width=3)


def tool_brush(color):
    def draw(cr):
        cr.rectangle(-4, -4, 8, 40)
        outline_fill(cr, "#C98B4A", width=3)
        cr.move_to(-7, 36)
        cr.line_to(7, 36)
        cr.line_to(3, 58)
        cr.line_to(-3, 58)
        cr.close_path()
        outline_fill(cr, color, width=3)
    return draw


def tool_flag(cr):
    cr.rectangle(-3, -60, 6, 110)
    outline_fill(cr, "#5A3A20", width=2)
    for i in range(4):
        for j in range(3):
            cr.rectangle(3 + i * 14, 20 + j * 14, 14, 14)
            fill(cr, "#222222" if (i + j) % 2 == 0 else "#FFFFFF")
    cr.rectangle(3, 20, 56, 42)
    stroke(cr, "#222222", 2)


def tool_mug(cr):
    rrect(cr, -18, -4, 36, 40, 6)
    outline_fill(cr, "#D7263D", width=3)
    cr.arc(22, 16, 10, -TAU / 4, TAU / 4)
    stroke(cr, "#3A2318", 5)
    cr.rectangle(-18, 8, 36, 6)
    fill(cr, "#FFFFFF")


def tool_letter(cr):
    cr.save()
    cr.rotate(0.2)
    rrect(cr, -26, -2, 52, 36, 3)
    outline_fill(cr, "#FFF6E0", width=3)
    cr.move_to(-26, -2)
    cr.line_to(0, 18)
    cr.line_to(26, -2)
    stroke(cr, "#C9A87A", 3)
    cr.arc(14, 24, 5, 0, TAU)
    fill(cr, "#D7263D")
    cr.restore()


def tool_needle(cr):
    cr.move_to(0, 0)
    cr.line_to(0, 40)
    stroke(cr, "#B0B6C0", 3)
    cr.move_to(0, 0)
    cr.curve_to(30, 10, -20, 40, 20, 70)
    stroke(cr, "#D7263D", 2.5)


# --- toys ----------------------------------------------------------------------------------------------

def toy_car(cr, x, y, s, color, t, spin=True):
    cr.save()
    cr.translate(x, y)
    cr.scale(s, s)
    rrect(cr, -50, -40, 100, 32, 12)
    outline_fill(cr, color)
    rrect(cr, -26, -64, 50, 30, 12)
    outline_fill(cr, color)
    rrect(cr, -18, -58, 16, 18, 4)
    fill(cr, "#BFE6FF")
    rrect(cr, 4, -58, 14, 18, 4)
    fill(cr, "#BFE6FF")
    for wx in (-28, 28):
        cr.arc(wx, -8, 14, 0, TAU)
        outline_fill(cr, "#333333")
        cr.save()
        cr.translate(wx, -8)
        cr.rotate(ph(t, 8) if spin else 0)
        cr.move_to(-8, 0)
        cr.line_to(8, 0)
        stroke(cr, "#BBBBBB", 3)
        cr.restore()
    cr.restore()


def toy_bear(cr, x, y, s, color="#B07A45", wave=0.0, blink_v=1.0):
    cr.save()
    cr.translate(x, y)
    cr.scale(s, s)
    ellipse(cr, 0, -40, 34, 40)
    outline_fill(cr, color)
    ellipse(cr, 0, -36, 20, 24)
    fill(cr, "#E9C9A0")
    for d in (-1, 1):
        ellipse(cr, 24 * d, -4, 14, 10)
        outline_fill(cr, color)
    cr.save()
    cr.translate(30, -62)
    cr.rotate(-0.4 - wave)
    ellipse(cr, 0, 14, 10, 18)
    outline_fill(cr, color)
    cr.restore()
    ellipse(cr, -30, -48, 10, 18)
    outline_fill(cr, color)
    cr.arc(0, -100, 30, 0, TAU)
    outline_fill(cr, color)
    for d in (-1, 1):
        cr.arc(24 * d, -124, 11, 0, TAU)
        outline_fill(cr, color)
        cr.arc(24 * d, -124, 5, 0, TAU)
        fill(cr, "#E9C9A0")
    ellipse(cr, 0, -90, 12, 9)
    fill(cr, "#E9C9A0")
    cr.arc(0, -94, 4.5, 0, TAU)
    fill(cr, "#2B1A12")
    for d in (-1, 1):
        ellipse(cr, 11 * d, -106, 4, 5 * blink_v)
        fill(cr, "#2B1A12")
    rrect(cr, -18, -74, 36, 10, 5)
    fill(cr, "#D7263D")
    cr.restore()


def toy_block(cr, x, y, s, color, letter):
    cr.save()
    cr.translate(x, y)
    cr.scale(s, s)
    rrect(cr, -30, -60, 60, 60, 8)
    outline_fill(cr, color)
    rrect(cr, -22, -52, 44, 44, 6)
    stroke(cr, "#FFFFFF", 3, 0.7)
    text(cr, letter, 0, -30, 36, "#FFFFFF", outline="#3A2318", ow=6)
    cr.restore()


def toy_robot(cr, x, y, s, color, t):
    cr.save()
    cr.translate(x, y)
    cr.scale(s, s)
    rrect(cr, -28, -70, 56, 56, 8)
    outline_fill(cr, color)
    rrect(cr, -18, -112, 36, 36, 8)
    outline_fill(cr, "#B9C3CF")
    cr.move_to(0, -112)
    cr.line_to(0, -128)
    stroke(cr, "#3A2318", 3)
    on = 0.5 + 0.5 * math.sin(ph(t, 8))
    cr.arc(0, -132, 6, 0, TAU)
    fill(cr, (1, 0.3 + 0.5 * on, 0.3, 1))
    for d in (-1, 1):
        cr.arc(9 * d, -96, 5, 0, TAU)
        fill(cr, "#3FA7FF")
        rrect(cr, 28 * d - (6 if d > 0 else 6), -64, 12, 34, 5)
        outline_fill(cr, "#B9C3CF", width=3)
    for d in (-1, 1):
        cr.rectangle(12 * d - 7, -14, 14, 14)
        outline_fill(cr, "#666E78", width=3)
    cr.arc(0, -44, 8, 0, TAU)
    fill(cr, "#FFD23F")
    cr.restore()


def toy_ball(cr, x, y, r, c1, c2, rot=0.0):
    cr.save()
    cr.translate(x, y)
    cr.arc(0, 0, r, 0, TAU)
    outline_fill(cr, c1)
    cr.arc(0, 0, r, 0, TAU)
    cr.clip()
    cr.rotate(rot)
    cr.rectangle(-r, -r * 0.25, 2 * r, r * 0.5)
    fill(cr, c2)
    cr.reset_clip()
    cr.arc(-r * 0.35, -r * 0.4, r * 0.18, 0, TAU)
    fill(cr, "#FFFFFF", 0.6)
    cr.restore()


def toy_drum(cr, x, y, s, color):
    cr.save()
    cr.translate(x, y)
    cr.scale(s, s)
    cr.rectangle(-36, -50, 72, 46)
    outline_fill(cr, color)
    for k in range(5):
        cr.move_to(-36 + k * 18, -50)
        cr.line_to(-27 + k * 18, -4)
        stroke(cr, "#FFD23F", 3)
    ellipse(cr, 0, -50, 36, 10)
    outline_fill(cr, "#FFF6E0", width=3)
    cr.restore()


def gift(cr, x, y, w, h, color, ribbon, bow=True, lid_lift=0.0):
    cr.save()
    cr.translate(x, y)
    rrect(cr, -w / 2, -h, w, h, 6)
    outline_fill(cr, color)
    cr.rectangle(-w * 0.08, -h, w * 0.16, h)
    fill(cr, ribbon)
    cr.rectangle(-w / 2 - 4, -h - 12 - lid_lift, w + 8, 18)
    outline_fill(cr, color)
    cr.rectangle(-w * 0.08, -h - 12 - lid_lift, w * 0.16, 18)
    fill(cr, ribbon)
    if bow:
        by = -h - 14 - lid_lift
        for d in (-1, 1):
            cr.move_to(0, by)
            cr.curve_to(22 * d, by - 26, 40 * d, by - 6, 0, by)
            outline_fill(cr, ribbon, width=3)
        cr.arc(0, by, 6, 0, TAU)
        fill(cr, ribbon)
    cr.restore()


GIFT_COLORS = [("#E63946", "#FFD23F"), ("#2A9D8F", "#FFFFFF"), ("#457B9D", "#FF6FB5"),
               ("#8E5AE8", "#FFD23F"), ("#3BB273", "#E63946"), ("#F4A261", "#457B9D")]


# --- room pieces ---------------------------------------------------------------------------------------

def wall(cr, top="#9A6234", bottom="#6E4120", floor_y=800):
    g = cairo.LinearGradient(0, 0, 0, floor_y)
    g.add_color_stop_rgba(0, *rgb(top))
    g.add_color_stop_rgba(1, *rgb(bottom))
    cr.rectangle(0, 0, W, floor_y)
    cr.set_source(g)
    cr.fill()
    rnd = random.Random(7)
    for x in range(0, W, 96):
        cr.move_to(x, 0)
        cr.line_to(x, floor_y)
        stroke(cr, "#4A2A12", 3, 0.45)
        for _ in range(2):
            ky = rnd.uniform(60, floor_y - 60)
            ellipse(cr, x + rnd.uniform(25, 70), ky, 9, 4)
            stroke(cr, "#4A2A12", 2, 0.35)
    # wainscot
    cr.rectangle(0, floor_y - 150, W, 150)
    fill(cr, "#5B3418", 0.55)
    cr.move_to(0, floor_y - 150)
    cr.line_to(W, floor_y - 150)
    stroke(cr, "#3A2010", 8)
    # floor
    g = cairo.LinearGradient(0, floor_y, 0, H)
    g.add_color_stop_rgba(0, *rgb("#B5753A"))
    g.add_color_stop_rgba(1, *rgb("#7E4B22"))
    cr.rectangle(0, floor_y, W, H - floor_y)
    cr.set_source(g)
    cr.fill()
    for k in range(-10, 30):
        cr.move_to(W / 2 + k * 60, floor_y)
        cr.line_to(W / 2 + (k * 60) * 3.2, H)
        stroke(cr, "#5A3416", 2.5, 0.5)
    for y in (850, 920, 1010):
        cr.move_to(0, y)
        cr.line_to(W, y)
        stroke(cr, "#5A3416", 2, 0.35)
    # ceiling beam
    cr.rectangle(0, 0, W, 54)
    fill(cr, "#4A2A12")
    cr.rectangle(0, 50, W, 8)
    fill(cr, "#2E1A0A")


def garland(cr, t, y=70, sag=60, n_bulbs=26):
    """A pine garland across the top with twinkling bulbs."""
    def curve_y(x):
        u = (x % (W / 3)) / (W / 3)
        return y + sag * 4 * u * (1 - u)
    pts = [(x, curve_y(x)) for x in range(0, W + 1, 12)]
    for w, col in ((40, "#1B5E33"), (28, "#2E8B4A")):
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        stroke(cr, col, w)
    rnd = random.Random(3)
    for p in pts[::3]:
        for _ in range(2):
            cr.move_to(p[0] + rnd.uniform(-10, 10), p[1] + rnd.uniform(-14, 14))
            cr.rel_line_to(rnd.uniform(-14, 14), rnd.uniform(-10, 10))
            stroke(cr, "#174D2A", 4)
    for i in range(n_bulbs):
        x = (i + 0.5) * W / n_bulbs
        by = curve_y(x) + 18
        col = BULBS[i % len(BULBS)]
        on = 0.55 + 0.45 * math.sin(ph(t, 4, i * 0.37))
        glow = cairo.RadialGradient(x, by, 2, x, by, 34)
        c = rgb(col)
        glow.add_color_stop_rgba(0, c[0], c[1], c[2], 0.55 * on)
        glow.add_color_stop_rgba(1, c[0], c[1], c[2], 0)
        cr.arc(x, by, 34, 0, TAU)
        cr.set_source(glow)
        cr.fill()
        ellipse(cr, x, by, 8, 12)
        cr.set_source_rgba(c[0] * (0.6 + 0.4 * on), c[1] * (0.6 + 0.4 * on), c[2] * (0.6 + 0.4 * on), 1)
        cr.fill()
        cr.rectangle(x - 5, by - 16, 10, 6)
        fill(cr, "#666666")
    # bows at the swag tops
    for k in range(4):
        x = k * W / 3
        for d in (-1, 1):
            cr.move_to(x, y)
            cr.curve_to(x + 34 * d, y - 30, x + 44 * d, y + 18, x, y)
            outline_fill(cr, "#D7263D", width=3)
        cr.move_to(x - 4, y)
        cr.line_to(x - 18, y + 46)
        cr.move_to(x + 4, y)
        cr.line_to(x + 18, y + 46)
        stroke(cr, "#D7263D", 9)


class Sky:
    """Night sky seen through a window or door: stars, aurora, falling snow.
    All drawn inside whatever clip the caller set."""

    def __init__(self, seed=1, n_stars=90, n_snow=120):
        rnd = random.Random(seed)
        self.stars = [(rnd.random(), rnd.random() * 0.7, rnd.uniform(1, 2.6), rnd.random()) for _ in range(n_stars)]
        self.snow = [(rnd.random(), rnd.random(), rnd.uniform(2, 6), rnd.choice((1, 1, 2)), rnd.random())
                     for _ in range(n_snow)]

    def draw(self, cr, x, y, w, h, t, aurora=True, hills=True):
        g = cairo.LinearGradient(0, y, 0, y + h)
        g.add_color_stop_rgba(0, *rgb("#071033"))
        g.add_color_stop_rgba(0.6, *rgb("#16306E"))
        g.add_color_stop_rgba(1, *rgb("#2B4C8F"))
        cr.rectangle(x, y, w, h)
        cr.set_source(g)
        cr.fill()
        for sx, sy, r, o in self.stars:
            tw = 0.5 + 0.5 * math.sin(ph(t, 3, o))
            cr.arc(x + sx * w, y + sy * h, r, 0, TAU)
            fill(cr, "#FFFFFF", 0.35 + 0.6 * tw)
        if aurora:
            for band, (col, amp, base) in enumerate((("#3DFFB0", 0.07, 0.28), ("#7A6CFF", 0.05, 0.2),
                                                     ("#2EE6D6", 0.06, 0.36))):
                cr.move_to(x, y + h * base)
                steps = 40
                for i in range(steps + 1):
                    u = i / steps
                    yy = y + h * (base + amp * math.sin(TAU * u * 1.5 + ph(t, 1, band * 0.3))
                                  + 0.02 * math.sin(TAU * u * 5 - ph(t, 2, band * 0.2)))
                    cr.line_to(x + u * w, yy)
                c = rgb(col)
                cr.set_line_width(h * 0.07)
                cr.set_source_rgba(c[0], c[1], c[2], 0.22)
                cr.stroke()
        if hills:
            cr.move_to(x, y + h)
            for i in range(41):
                u = i / 40
                cr.line_to(x + u * w, y + h * (0.82 - 0.08 * math.sin(u * 7.1) - 0.05 * math.sin(u * 13)))
            cr.line_to(x + w, y + h)
            cr.close_path()
            fill(cr, "#E8F1FF")
            rnd = random.Random(11)
            for _ in range(9):                         # little pines on the hills
                px = x + rnd.uniform(0.05, 0.95) * w
                py = y + h * rnd.uniform(0.8, 0.9)
                ps = rnd.uniform(0.6, 1.1) * h / 400
                for k in range(3):
                    cr.move_to(px, py - (60 - k * 16) * ps * 1.4)
                    cr.line_to(px - (22 + k * 8) * ps, py - (24 - k * 16) * ps)
                    cr.line_to(px + (22 + k * 8) * ps, py - (24 - k * 16) * ps)
                    cr.close_path()
                    fill(cr, "#2F6B4F")
        for sx, sy, r, n, o in self.snow:
            yy = (sy + frac(t, n)) % 1.0
            xx = sx + 0.015 * math.sin(ph(t, 2, o))
            cr.arc(x + (xx % 1) * w, y + yy * h, r, 0, TAU)
            fill(cr, "#FFFFFF", 0.85)


def window(cr, sky: Sky, x, y, w, h, t, *, arch=True, panes=True):
    cr.save()
    cr.new_path()
    if arch:
        cr.move_to(x, y + w / 2)
        cr.arc(x + w / 2, y + w / 2, w / 2, TAU / 2, TAU)
        cr.line_to(x + w, y + h)
        cr.line_to(x, y + h)
        cr.close_path()
    else:
        rrect(cr, x, y, w, h, 14)
    path = cr.copy_path()
    cr.clip()
    sky.draw(cr, x, y, w, h, t)
    cr.reset_clip()
    cr.append_path(path)
    stroke(cr, "#3A2010", 26)
    cr.append_path(path)
    stroke(cr, "#8A5A2E", 14)
    if panes:
        cr.move_to(x + w / 2, y + 8)
        cr.line_to(x + w / 2, y + h)
        cr.move_to(x, y + h * 0.55)
        cr.line_to(x + w, y + h * 0.55)
        stroke(cr, "#8A5A2E", 10)
    # snowy sill
    rrect(cr, x - 30, y + h - 6, w + 60, 26, 8)
    outline_fill(cr, "#8A5A2E", line="#3A2010")
    cr.move_to(x - 30, y + h - 2)
    for i in range(9):
        cr.curve_to(x - 30 + (i + 0.3) * (w + 60) / 9, y + h - 24, x - 30 + (i + 0.7) * (w + 60) / 9, y + h - 24,
                    x - 30 + (i + 1) * (w + 60) / 9, y + h - 2)
    cr.close_path()
    fill(cr, "#FFFFFF")
    cr.restore()


def shelf(cr, x, y, w):
    rrect(cr, x, y, w, 22, 4)
    outline_fill(cr, "#8A5A2E", line="#3A2010")
    for bx in (x + 30, x + w - 30):
        cr.move_to(bx, y + 22)
        cr.line_to(bx, y + 60)
        cr.line_to(bx - 26, y + 22)
        stroke(cr, "#3A2010", 7)


def sparkle(cr, x, y, r, a=1.0, color="#FFF6B0"):
    star_path(cr, x, y, r, r * 0.28, points=4, rot=0)
    fill(cr, color, a)


def vignette(cr, strength=0.55):
    g = cairo.RadialGradient(W / 2, H / 2, H * 0.35, W / 2, H / 2, H * 0.95)
    g.add_color_stop_rgba(0, 0, 0, 0, 0)
    g.add_color_stop_rgba(1, 0.05, 0.02, 0, strength)
    cr.rectangle(0, 0, W, H)
    cr.set_source(g)
    cr.fill()


def warm_light(cr, x, y, r, a=0.25, color="#FFC46B"):
    c = rgb(color)
    g = cairo.RadialGradient(x, y, 0, x, y, r)
    g.add_color_stop_rgba(0, c[0], c[1], c[2], a)
    g.add_color_stop_rgba(1, c[0], c[1], c[2], 0)
    cr.arc(x, y, r, 0, TAU)
    cr.set_source(g)
    cr.fill()


def lantern(cr, x, y, t, off=0.0):
    flick = 0.8 + 0.2 * math.sin(ph(t, 16, off)) * math.sin(ph(t, 5, off + 0.2))
    warm_light(cr, x, y + 40, 220, 0.28 * flick)
    cr.move_to(x, 54)
    cr.line_to(x, y)
    stroke(cr, "#2E1A0A", 4)
    rrect(cr, x - 22, y, 44, 64, 8)
    outline_fill(cr, "#3A3A44", line="#1E1E24")
    rrect(cr, x - 15, y + 8, 30, 48, 6)
    fill(cr, (1, 0.78 * flick + 0.1, 0.35, 1))


def badge(cr, label: str, t):
    """Small corner card naming the room, with a live dot."""
    cr.save()
    rrect(cr, 34, H - 96, 34 + len(label) * 19 + 70, 60, 30)
    fill(cr, "#1A0E08", 0.55)
    on = 0.6 + 0.4 * math.sin(ph(t, 8))
    cr.arc(70, H - 66, 10, 0, TAU)
    fill(cr, (1, 0.2, 0.2, on))
    text(cr, label, 96, H - 66, 30, "#FFFFFF", outline=None, center=False)
    cr.restore()


def fireplace(cr, x, y, w, h, t):
    rrect(cr, x - 30, y - h - 40, w + 60, 50, 8)
    outline_fill(cr, "#8A5A2E", line="#3A2010")
    rrect(cr, x, y - h, w, h, 10)
    outline_fill(cr, "#A8483A", line="#4A1E16")
    for r in range(6):                                  # bricks
        for c in range(6):
            bx = x + c * w / 6 + (w / 12 if r % 2 else 0)
            if bx + w / 6 > x + w:
                continue
            cr.rectangle(bx + 3, y - h + r * h / 6 + 3, w / 6 - 6, h / 6 - 6)
            stroke(cr, "#7A2E24", 2, 0.6)
    mx, mw, mh = x + w * 0.2, w * 0.6, h * 0.62
    cr.move_to(mx, y)
    cr.line_to(mx, y - mh + mw / 2)
    cr.arc(mx + mw / 2, y - mh + mw / 2, mw / 2, TAU / 2, TAU)
    cr.line_to(mx + mw, y)
    cr.close_path()
    fill(cr, "#1A0A06")
    warm_light(cr, mx + mw / 2, y - 40, mw * 1.5, 0.35)
    for i in range(7):
        fx = mx + mw * (0.15 + 0.7 * i / 6)
        fh = mh * 0.5 * (0.6 + 0.4 * math.sin(ph(t, 8, i * 0.21)) * math.sin(ph(t, 3, i * 0.13)) ** 2 + 0.25)
        for col, k in (("#FF5A1F", 1.0), ("#FFB627", 0.65), ("#FFF1A8", 0.35)):
            cr.move_to(fx - 26 * k, y - 18)
            cr.curve_to(fx - 26 * k, y - 18 - fh * k * 0.6, fx + 6 * math.sin(ph(t, 4, i * 0.3)), y - 18 - fh * k,
                        fx, y - 18 - fh * k)
            cr.curve_to(fx + 6, y - 18 - fh * k * 0.7, fx + 26 * k, y - 18 - fh * k * 0.5, fx + 26 * k, y - 18)
            cr.close_path()
            fill(cr, col, 0.9)
    for lx in (-1, 1):
        cr.save()
        cr.translate(mx + mw / 2, y - 12)
        cr.rotate(0.25 * lx)
        rrect(cr, -70, -12, 140, 24, 10)
        outline_fill(cr, "#6B3E1C", line="#2E1A0A")
        cr.restore()


def steam(cr, x, y, t, off=0.0, h=70):
    for k in range(3):
        f = frac(t, 4, off + k / 3)
        yy = y - f * h
        cr.move_to(x + 6 * math.sin(ph(t, 4, off + k * 0.3)), yy)
        cr.curve_to(x + 14, yy - 12, x - 14, yy - 24, x + 4, yy - 36)
        stroke(cr, "#FFFFFF", 6, 0.45 * (1 - f))


def conveyor(cr, x0, x1, y, t, speed_n=2):
    rrect(cr, x0, y, x1 - x0, 36, 18)
    outline_fill(cr, "#4A4F58", line="#22252A", width=5)
    step = 60
    off = frac(t, speed_n * (x1 - x0) / step / 8) * step
    for k in range(int((x1 - x0) / step) + 2):
        xx = x0 + 18 + (k * step + off) % (x1 - x0 - 36)
        cr.move_to(xx, y + 6)
        cr.line_to(xx, y + 30)
        stroke(cr, "#6B727D", 4)
    cr.rectangle(x0 + 10, y + 30, x1 - x0 - 20, 140)
    outline_fill(cr, "#7A4A24", line="#3A2010")
    for k in range(1, 12):
        cr.move_to(x0 + 10 + k * (x1 - x0 - 20) / 12, y + 34)
        cr.line_to(x0 + 10 + k * (x1 - x0 - 20) / 12, y + 166)
        stroke(cr, "#5A3416", 3, 0.6)
    for gx in range(int(x0) + 40, int(x1), 120):
        cr.save()
        cr.translate(gx, y + 66)
        cr.rotate(ph(t, 4))
        for i in range(8):
            cr.rotate(TAU / 8)
            cr.rectangle(-7, -34, 14, 14)
        cr.arc(0, 0, 26, 0, TAU)
        fill(cr, "#B98A2E")
        cr.arc(0, 0, 9, 0, TAU)
        fill(cr, "#5A3A10")
        cr.restore()
    for lx in (x0 + 30, x1 - 30):
        cr.rectangle(lx - 10, y + 36, 20, 110)
        outline_fill(cr, "#4A4F58", line="#22252A", width=4)


# --- scenes --------------------------------------------------------------------------------------------

@dataclass
class Scene:
    id: str
    title: str          # shown on the corner card and used in Shorts titles
    short_line: str     # Shorts hook: "Watch an elf ..."
    draw: Callable      # (cr, t) -> None
    base: Callable | None = None    # static background, cached once


def _cached(base: Callable) -> Callable:
    cache: dict = {}

    def paint(cr):
        if "s" not in cache:
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
            base(cairo.Context(surf))
            cache["s"] = surf
        cr.set_source_surface(cache["s"], 0, 0)
        cr.paint()
    return paint


# 1. toy assembly line

_sky1 = Sky(1)


def _assembly_base(cr):
    wall(cr)
    shelf(cr, 120, 330, 520)
    shelf(cr, 1290, 330, 520)
    for i, x in enumerate(range(170, 640, 92)):
        toy_drum(cr, x, 330, 0.8, TOY[i % 8]) if i % 2 else toy_block(cr, x, 330, 0.85, TOY[(i + 3) % 8], "ABCDE"[i % 5])
    for i, x in enumerate(range(1340, 1810, 92)):
        toy_ball(cr, x, 300, 28, TOY[i % 8], "#FFFFFF") if i % 2 else toy_block(cr, x, 330, 0.85, TOY[(i + 5) % 8], "NOELX"[i % 5])


def assembly(cr, t):
    _assembly_base_p(cr)
    window(cr, _sky1, 760, 150, 400, 400, t)
    garland(cr, t)
    lantern(cr, 380, 120, t, 0.1)
    lantern(cr, 1540, 120, t, 0.6)
    # elves behind the belt: hammering, painting, checking
    a, hit = hammer_swing(t, 8)
    elf(cr, 520, 705, 1.15, look(0), t, arm_r=a, tool_r=tool_hammer, arm_l=0.6, tilt=-0.05,
        blink_v=blink(t, 3, 0.1), bob=3 * math.sin(ph(t, 8)))
    brush = 0.9 + 0.35 * math.sin(ph(t, 8, 0.25))
    elf(cr, 1000, 705, 1.15, look(1), t, arm_r=brush, tool_r=tool_brush("#FF6FB5"), arm_l=0.3, facing=-1,
        blink_v=blink(t, 3, 0.5), tilt=0.06 * math.sin(ph(t, 4)), sway_off=0.3)
    nod = 0.08 * math.sin(ph(t, 4, 0.1))
    elf(cr, 1460, 705, 1.15, look(2), t, arm_r=1.2 + 0.2 * math.sin(ph(t, 4)), tool_r=tool_letter, arm_l=0.2,
        tilt=nod, blink_v=blink(t, 2, 0.8), mouth="open" if frac(t, 4) < 0.5 else "smile", sway_off=0.6)
    conveyor(cr, 120, 1800, 640, t)
    # toys ride the belt: 2 laps of the belt per loop
    kinds = ["car", "robot", "bear", "block", "car", "drum", "robot", "ball"]
    span = 1800 - 120 + 240
    for i, k in enumerate(kinds):
        x = 120 - 120 + (i * span / len(kinds) + frac(t, 2) * span) % span
        if x < 60 or x > 1860:
            continue
        y = 640
        c = TOY[i % 8]
        if k == "car":
            toy_car(cr, x, y, 0.9, c, t)
        elif k == "robot":
            toy_robot(cr, x, y, 0.9, c, t)
        elif k == "bear":
            toy_bear(cr, x, y, 0.75)
        elif k == "block":
            toy_block(cr, x, y, 1.0, c, "ABC"[i % 3])
        elif k == "drum":
            toy_drum(cr, x, y, 1.0, c)
        else:
            toy_ball(cr, x, y - 32, 32, c, "#FFFFFF", rot=-ph(t, 6))
    if hit:
        for k in range(5):
            ang = k * TAU / 5
            sparkle(cr, 640 + 50 * math.cos(ang), 625 + 30 * math.sin(ang), 16)
    vignette(cr)
    badge(cr, "Toy Assembly Line", t)


_assembly_base_p = _cached(_assembly_base)


# 2. gift wrapping room

_sky2 = Sky(2)


def _wrap_base(cr):
    wall(cr, "#A3643A", "#723F22")
    shelf(cr, 1300, 300, 540)
    # big ribbon spools on the wall
    for i, (x, c) in enumerate(((180, "#E63946"), (310, "#FFD23F"), (440, "#2A9D8F"))):
        cr.move_to(x, 140)
        cr.line_to(x, 250)
        stroke(cr, "#3A2010", 6)
    # gift pile on the right floor
    rnd = random.Random(4)
    for row, (y, n) in enumerate(((900, 6), (790, 5), (690, 3))):
        for k in range(n):
            col, rib = GIFT_COLORS[(k + row * 2) % len(GIFT_COLORS)]
            gift(cr, 1320 + k * 90 + row * 45 + rnd.uniform(-8, 8), y, 84, 80 + rnd.uniform(-10, 10), col, rib)


def wrapping(cr, t):
    _wrap_base_p(cr)
    window(cr, _sky2, 640, 150, 360, 380, t)
    garland(cr, t, sag=50)
    for i, (x, c) in enumerate(((180, "#E63946"), (310, "#FFD23F"), (440, "#2A9D8F"))):
        cr.save()
        cr.translate(x, 290)
        cr.rotate(ph(t, 2, i * 0.2))
        cr.arc(0, 0, 44, 0, TAU)
        outline_fill(cr, c)
        cr.arc(0, 0, 12, 0, TAU)
        fill(cr, "#3A2010")
        for k in range(4):
            cr.rotate(TAU / 4)
            cr.move_to(16, 0)
            cr.line_to(40, 0)
            stroke(cr, "#FFFFFF", 3, 0.5)
        cr.restore()
        # ribbon unrolling down to the table
        cr.move_to(x + 44, 290)
        cr.curve_to(x + 60, 420, x + 20 + 30 * math.sin(ph(t, 2, i * 0.3)), 520, x + 120, 640)
        stroke(cr, c, 10)
    # elves: one folding paper, one tying, behind the table
    fold = 0.9 + 0.5 * math.sin(ph(t, 8))
    elf(cr, 420, 735, 1.1, look(3), t, arm_r=fold, arm_l=fold * 0.8, blink_v=blink(t, 3, 0.2), sway_off=0.1)
    tie = 1.3 + 0.25 * math.sin(ph(t, 4, 0.5))
    elf(cr, 780, 735, 1.1, look(4), t, arm_r=tie, arm_l=tie, facing=-1, blink_v=blink(t, 2, 0.6),
        tilt=0.06 * math.sin(ph(t, 4)), sway_off=0.4)
    # wrapping table with a cloth that hides the elves' legs
    rrect(cr, 120, 640, 1000, 40, 10)
    outline_fill(cr, "#8A5A2E", line="#3A2010")
    cr.rectangle(140, 676, 960, 200)
    outline_fill(cr, "#B3121F", line="#5A0E16")
    for k in range(16):
        star_path(cr, 170 + k * 60, 730 + (k % 2) * 60, 14, 6)
        fill(cr, "#FFF6E0", 0.8)
    for lx in (170, 1070):
        cr.rectangle(lx - 14, 680, 28, 240)
        outline_fill(cr, "#6B3E1C", line="#3A2010")
    # paper roll across the table, pattern scrolling
    cr.save()
    rrect(cr, 300, 600, 560, 44, 6)
    cr.clip()
    cr.rectangle(300, 600, 560, 44)
    fill(cr, "#D7263D")
    o = frac(t, 4) * 60
    for k in range(12):
        star_path(cr, 300 + k * 60 + o - 60, 622, 12, 5)
        fill(cr, "#FFD23F")
    cr.restore()
    # boxes: one gets its lid popped and a bow, four times a loop
    f = frac(t, 4)
    lift = 40 * math.sin(math.pi * min(f / 0.4, 1)) if f < 0.4 else 0
    col, rib = GIFT_COLORS[int(t / LOOP_S * 4) % len(GIFT_COLORS)]
    gift(cr, 980, 640, 120, 100, col, rib, lid_lift=lift)
    if 0.35 < f < 0.6:
        for k in range(6):
            ang = TAU * k / 6 + f * 4
            sparkle(cr, 980 + 90 * math.cos(ang), 500 + 50 * math.sin(ang), 14, 1 - abs(f - 0.47) * 6)
    # carrier walks across the front, 1 trip per loop, with hops
    u = frac(t, 1)
    cx = -150 + u * (W + 300)
    hop = 14 * abs(math.sin(ph(t, 16)))
    col2, rib2 = GIFT_COLORS[3]
    elf(cr, cx, 1040, 1.25, look(5), t, arm_r=1.9, arm_l=1.9, hop=hop, blink_v=blink(t, 2, 0.3), sway_off=0.8)
    cr.save()
    cr.translate(cx + 30, 1040 - hop - 1.25 * 240)
    gift(cr, 0, 0, 110, 90, col2, rib2)
    cr.restore()
    vignette(cr)
    badge(cr, "Gift Wrapping Room", t)


_wrap_base_p = _cached(_wrap_base)


# 3. toy train test track

_sky3 = Sky(3)


def _train_base(cr):
    wall(cr, "#93603A", "#6A3E22", floor_y=640)
    shelf(cr, 140, 280, 500)
    shelf(cr, 1280, 280, 500)
    for i, x in enumerate(range(190, 640, 110)):
        toy_bear(cr, x, 280, 0.62, ["#B07A45", "#D9A066", "#8C5A3C", "#E8B27A"][i % 4])
    for i, x in enumerate(range(1330, 1780, 110)):
        toy_robot(cr, x, 280, 0.7, TOY[(i + 2) % 8], 0)


def _track_point(u):
    """Point on the oval track for u in [0, 1)."""
    a = TAU * u
    return W / 2 + 720 * math.cos(a), 840 + 150 * math.sin(a)


def train(cr, t):
    _train_base_p(cr)
    window(cr, _sky3, 760, 120, 400, 380, t)
    garland(cr, t, sag=40)
    lantern(cr, 700, 110, t, 0.3)
    lantern(cr, 1220, 110, t, 0.8)
    # track
    for w, col in ((46, "#5A3A20"), (30, "#8A5A2E")):
        ellipse(cr, W / 2, 840, 720, 150)
        stroke(cr, col, w)
    for k in range(60):
        x, y = _track_point(k / 60)
        cr.arc(x, y, 5, 0, TAU)
        fill(cr, "#3A2010")
    ellipse(cr, W / 2, 840, 705, 138)
    stroke(cr, "#B9C3CF", 4)
    ellipse(cr, W / 2, 840, 735, 162)
    stroke(cr, "#B9C3CF", 4)
    # little snowy town in the middle
    for i, (x, c) in enumerate(((800, "#E63946"), (930, "#2A9D8F"), (1060, "#FFB703"), (1170, "#457B9D"))):
        rrect(cr, x - 50, 760, 100, 90, 6)
        outline_fill(cr, c)
        cr.move_to(x - 62, 762)
        cr.line_to(x, 700)
        cr.line_to(x + 62, 762)
        cr.close_path()
        outline_fill(cr, "#FFFFFF")
        lit = 0.6 + 0.4 * math.sin(ph(t, 2, i * 0.25))
        rrect(cr, x - 16, 786, 32, 30, 4)
        fill(cr, (1, 0.85, 0.4, lit))
    # train: engine + 4 cars, 2 laps per loop; cars behind the town when on the far side
    cars = [("engine", "#D7263D"), ("car", "#2B7BD6"), ("car", "#FFB703"), ("car", "#3BB273"), ("car", "#8E5AE8")]
    items = []
    for i, (kind, col) in enumerate(cars):
        u = (frac(t, 2) - i * 0.035) % 1.0
        x, y = _track_point(u)
        items.append((y, kind, col, x, u, i))
    for y, kind, col, x, u, i in sorted(items):
        d = -1 if math.sin(TAU * u) < 0 else 1       # far side: moving left
        s = 0.8 + 0.25 * (y - 690) / 300
        cr.save()
        cr.translate(x, y)
        cr.scale(s * d, s)
        rrect(cr, -50, -60, 100, 54, 8)
        outline_fill(cr, col)
        if kind == "engine":
            rrect(cr, -50, -110, 52, 54, 6)
            outline_fill(cr, col)
            rrect(cr, -40, -100, 30, 26, 4)
            fill(cr, "#BFE6FF")
            cr.rectangle(24, -100, 20, 40)
            outline_fill(cr, "#333333")
            # puffs
            for k in range(3):
                f = frac(t, 8, k / 3)
                cr.arc(34 - f * 40, -110 - f * 70, 10 + f * 22, 0, TAU)
                fill(cr, "#FFFFFF", 0.7 * (1 - f))
        else:
            col2, rib = GIFT_COLORS[i % len(GIFT_COLORS)]
            gift(cr, 0, -60, 60, 46, col2, rib)
        for wx in (-30, 30):
            cr.arc(wx, -6, 13, 0, TAU)
            outline_fill(cr, "#333333")
            cr.save()
            cr.translate(wx, -6)
            cr.rotate(ph(t, 16))
            cr.move_to(-8, 0)
            cr.line_to(8, 0)
            stroke(cr, "#BBBBBB", 3)
            cr.restore()
        cr.restore()
    # elves: flag waver and a jumping cheerer
    wave = 1.7 + 0.5 * math.sin(ph(t, 8))
    elf(cr, 260, 700, 1.1, look(6), t, arm_r=wave, tool_r=tool_flag, arm_l=0.3, blink_v=blink(t, 3, 0.4),
        mouth="open", sway_off=0.2)
    j = abs(math.sin(ph(t, 8, 0.1)))
    elf(cr, 1680, 700, 1.1, look(7), t, arm_r=2.4 * j + 0.3, arm_l=2.4 * j + 0.3, hop=40 * j, facing=-1,
        blink_v=blink(t, 2, 0.7), mouth="open" if j > 0.5 else "smile", sway_off=0.5)
    vignette(cr)
    badge(cr, "Toy Train Test Track", t)


_train_base_p = _cached(_train_base)


# 4. teddy bear sewing corner

_sky4 = Sky(4)


def _bear_base(cr):
    wall(cr, "#A86E44", "#77462A")
    shelf(cr, 120, 300, 640)
    shelf(cr, 120, 520, 640)
    # yarn basket
    rrect(cr, 1500, 760, 260, 120, 30)
    outline_fill(cr, "#C98B4A", line="#5A3A20")
    for k in range(6):
        cr.move_to(1510 + k * 44, 770)
        cr.line_to(1530 + k * 44, 870)
        stroke(cr, "#8A5A2E", 3)
    for i, (x, c) in enumerate(((1560, "#E63946"), (1640, "#3FA7FF"), (1710, "#FFD23F"))):
        cr.arc(x, 750, 40, 0, TAU)
        outline_fill(cr, c)
        for k in range(4):
            cr.arc(x, 750, 40 - k * 9, 0.3 + k, 2.6 + k)
            stroke(cr, "#FFFFFF", 2, 0.4)


def bears(cr, t):
    _bear_base_p(cr)
    window(cr, _sky4, 980, 140, 380, 380, t)
    garland(cr, t, sag=55)
    lantern(cr, 880, 120, t, 0.2)
    bear_cols = ["#B07A45", "#D9A066", "#8C5A3C", "#E8B27A", "#C47B3B"]
    for row, y in enumerate((300, 520)):
        for i in range(5):
            x = 190 + i * 128
            wave = 0.6 * max(0.0, math.sin(ph(t, 2, i * 0.2 + row * 0.5))) ** 3
            toy_bear(cr, x, y, 0.7, bear_cols[(i + row) % 5], wave=wave, blink_v=blink(t, 2, i * 0.13 + row * 0.4))
    stitch = 1.2 + 0.55 * math.sin(ph(t, 8))
    elf(cr, 900, 775, 1.1, look(8), t, arm_r=stitch, tool_r=tool_needle, arm_l=0.9, blink_v=blink(t, 3, 0.6),
        tilt=0.05, sway_off=0.4)
    # a little elf hugging a finished bear, swaying
    sway = 0.12 * math.sin(ph(t, 4))
    elf(cr, 1300, 765, 1.0, look(9), t, arm_r=1.5, arm_l=1.5, tilt=sway, facing=-1, blink_v=blink(t, 2, 0.1),
        sway_off=0.7)
    # sewing table with a bear being stitched
    rrect(cr, 760, 700, 640, 36, 10)
    outline_fill(cr, "#8A5A2E", line="#3A2010")
    for lx in (800, 1360):
        cr.rectangle(lx - 12, 736, 24, 200)
        outline_fill(cr, "#6B3E1C", line="#3A2010")
    cr.rectangle(780, 732, 600, 190)
    outline_fill(cr, "#2E7D4F", line="#174D2A")
    for k in range(10):
        cr.arc(810 + k * 60, 760, 8, 0, TAU)
        fill(cr, "#FFF6E0", 0.7)
    toy_bear(cr, 1080, 700, 0.9, "#D9A066", wave=0.3 * max(0, math.sin(ph(t, 4))), blink_v=blink(t, 4, 0.3))
    toy_bear(cr, 1250, 700, 0.55, "#8C5A3C", wave=0.5 * max(0, math.sin(ph(t, 2, 0.5))))
    # yarn ball rolling back and forth on the floor
    rx = 1520 - 200 * (0.5 - 0.5 * math.cos(ph(t, 2)))
    toy_ball(cr, rx, 960, 30, "#FF6FB5", "#FFFFFF", rot=-rx / 30)
    vignette(cr)
    badge(cr, "Teddy Bear Workshop", t)


_bear_base_p = _cached(_bear_base)


# 5. sleigh loading bay

_sky5 = Sky(5, n_stars=140, n_snow=180)


def reindeer(cr, x, y, s, t, off=0.0, facing=-1):
    cr.save()
    cr.translate(x, y)
    cr.scale(s * facing, s)
    bob = 6 * math.sin(ph(t, 4, off))
    for lx in (-60, -36, 40, 64):
        cr.rectangle(lx - 7, -70, 14, 70)
        outline_fill(cr, "#7A4A24", width=3)
        cr.rectangle(lx - 8, -12, 16, 12)
        fill(cr, "#3A2010")
    ellipse(cr, 0, -96, 92, 44)
    outline_fill(cr, "#9C6234")
    ellipse(cr, 0, -84, 60, 22)
    fill(cr, "#E8C9A0")
    rrect(cr, -86, -132, 172, 16, 8)              # harness
    fill(cr, "#D7263D")
    for k in range(5):
        cr.arc(-60 + k * 30, -124, 7, 0, TAU)
        outline_fill(cr, "#FFD23F", width=2)
    cr.save()
    cr.translate(80, -120 + bob)
    cr.move_to(-10, 10)
    cr.line_to(20, -50)
    stroke(cr, "#9C6234", 34)
    ellipse(cr, 34, -70, 40, 28)
    outline_fill(cr, "#9C6234")
    cr.arc(70, -66, 9, 0, TAU)
    fill(cr, "#3A2010")
    eyeo = blink(t, 2, off)
    ellipse(cr, 40, -80, 5, 6 * eyeo)
    fill(cr, "#2B1A12")
    for d in (-1, 1):
        cr.move_to(20 + d * 6, -94)
        cr.curve_to(10 + d * 20, -140, 40 + d * 10, -150, 30 + d * 30, -170)
        cr.move_to(16 + d * 12, -122)
        cr.line_to(-4 + d * 22, -138)
        stroke(cr, "#E0C08A", 7)
    cr.restore()
    cr.restore()


def _sleigh_base(cr):
    wall(cr, "#875636", "#5E371E", floor_y=760)
    # sacks in the corner
    for i, (x, c) in enumerate(((180, "#C9A87A"), (320, "#B89466"), (250, "#D9B88A"))):
        y = 900 if i < 2 else 810
        cr.move_to(x - 70, y)
        cr.curve_to(x - 90, y - 140, x - 30, y - 170, x, y - 160)
        cr.curve_to(x + 30, y - 170, x + 90, y - 140, x + 70, y)
        cr.close_path()
        outline_fill(cr, c)
        cr.move_to(x - 30, y - 150)
        cr.line_to(x + 30, y - 150)
        stroke(cr, "#D7263D", 8)


def sleigh(cr, t):
    _sleigh_base_p(cr)
    # big open barn door onto the snowy night
    dx, dy, dw, dh = 560, 110, 1200, 660
    cr.save()
    rrect(cr, dx, dy, dw, dh, 30)
    cr.clip()
    _sky5.draw(cr, dx, dy, dw, dh, t)
    cr.restore()
    rrect(cr, dx, dy, dw, dh, 30)
    stroke(cr, "#3A2010", 30)
    rrect(cr, dx, dy, dw, dh, 30)
    stroke(cr, "#8A5A2E", 16)
    garland(cr, t, y=74, sag=30)
    lantern(cr, 470, 140, t, 0.4)
    # reindeer outside, waiting
    reindeer(cr, 1560, 760, 0.95, t, 0.0)
    reindeer(cr, 1330, 770, 0.9, t, 0.33)
    # the sleigh
    sx, sy = 980, 900
    cr.save()
    cr.translate(sx, sy)
    for rx in (-300, 300):
        pass
    cr.move_to(-360, 40)
    cr.curve_to(-200, 70, 200, 70, 380, 30)
    cr.curve_to(440, 20, 450, -40, 400, -30)
    stroke(cr, "#FFD23F", 16)
    for lx in (-220, 0, 220):
        cr.move_to(lx, 40)
        cr.line_to(lx + 20, -20)
        stroke(cr, "#FFD23F", 10)
    cr.move_to(-330, -20)
    cr.curve_to(-380, -260, -200, -250, -170, -200)
    cr.line_to(300, -170)
    cr.curve_to(360, -170, 360, -20, 300, -20)
    cr.close_path()
    outline_fill(cr, "#D7263D", line="#5A0E16", width=6)
    cr.move_to(-300, -60)
    cr.line_to(300, -60)
    stroke(cr, "#FFD23F", 10)
    for k in range(6):
        star_path(cr, -250 + k * 100, -110, 16, 7)
        fill(cr, "#FFD23F")
    cr.restore()
    # gifts already loaded (grow a little through the loop, then reset unseen behind the sleigh rim)
    for i in range(5):
        col, rib = GIFT_COLORS[i]
        gift(cr, sx - 200 + i * 95, sy - 170, 80, 70 + (i % 2) * 20, col, rib)
    # elves toss gifts into the sleigh in an arc, 4 tosses per loop
    f = frac(t, 4)
    throw = 0.6 + 2.0 * (1 - min(f / 0.25, 1)) if f < 0.25 else 0.6 + 1.6 * min((f - 0.25) / 0.2, 1)
    elf(cr, 380, 1000, 1.2, look(10), t, arm_r=throw, arm_l=throw * 0.9, blink_v=blink(t, 2, 0.2), sway_off=0.3,
        mouth="open" if 0.25 < f < 0.5 else "smile")
    if 0.25 <= f < 0.75:
        u = (f - 0.25) / 0.5
        gx = 470 + u * (sx - 470)
        gy = 760 - 420 * math.sin(math.pi * u) + u * (sy - 200 - 760)
        cr.save()
        cr.translate(gx, gy)
        cr.rotate(u * TAU)
        col, rib = GIFT_COLORS[int(t / LOOP_S * 4) % len(GIFT_COLORS)]
        gift(cr, 0, 30, 70, 60, col, rib)
        cr.restore()
        for k in range(3):
            sparkle(cr, gx - 40 * (k + 1), gy + 18 * k * math.sin(u * 9), 10 - 2 * k, 0.8)
    clap = abs(math.sin(ph(t, 8, 0.1)))
    elf(cr, 1500, 1040, 1.15, look(11), t, arm_r=0.9 + 0.8 * clap, arm_l=0.9 + 0.8 * clap, facing=-1,
        blink_v=blink(t, 3, 0.5), hop=10 * clap, sway_off=0.6, mouth="open")
    vignette(cr)
    badge(cr, "Sleigh Loading Bay", t)


_sleigh_base_p = _cached(_sleigh_base)


# 6. cocoa break by the fire

_sky6 = Sky(6)


def _cocoa_base(cr):
    wall(cr, "#9B5E3A", "#6B3A20")
    shelf(cr, 1340, 280, 460)
    for i, x in enumerate(range(1380, 1780, 90)):
        rrect(cr, x - 22, 222, 44, 58, 8)
        outline_fill(cr, TOY[i % 8])


def cocoa(cr, t):
    _cocoa_base_p(cr)
    window(cr, _sky6, 1360, 360, 360, 330, t, arch=False)
    garland(cr, t, sag=45)
    fireplace(cr, 160, 820, 560, 420, t)
    # stockings on the mantel
    for i, (x, c) in enumerate(((210, "#D7263D"), (340, "#1E9E5A"), (470, "#2B7BD6"), (600, "#D7263D"))):
        sw = 0.05 * math.sin(ph(t, 2, i * 0.25))
        cr.save()
        cr.translate(x, 360)
        cr.rotate(sw)
        cr.move_to(-20, 0)
        cr.line_to(20, 0)
        cr.line_to(20, 80)
        cr.curve_to(20, 120, -40, 120, -48, 100)
        cr.curve_to(-50, 86, -20, 80, -20, 70)
        cr.close_path()
        outline_fill(cr, c)
        rrect(cr, -26, -8, 52, 24, 8)
        outline_fill(cr, "#FFFFFF", width=3)
        cr.restore()
    # table with cookies
    ellipse(cr, 1080, 780, 300, 50)
    outline_fill(cr, "#8A5A2E", line="#3A2010")
    cr.rectangle(1065, 790, 30, 180)
    outline_fill(cr, "#6B3E1C", line="#3A2010")
    ellipse(cr, 1080, 760, 90, 20)
    outline_fill(cr, "#FFFFFF", width=3)
    for k in range(5):
        cr.arc(1030 + k * 26, 752 - (k % 2) * 6, 13, 0, TAU)
        outline_fill(cr, "#C98B4A", width=2.5)
        cr.arc(1030 + k * 26, 750 - (k % 2) * 6, 3, 0, TAU)
        fill(cr, "#5A2A10")
    # three elves sipping (mug rises to the mouth twice a loop each, staggered)
    for i, (x, facing) in enumerate(((860, 1), (1300, -1), (1080, 1))):
        f = frac(t, 2, i / 3)
        sip = 1.6 + 0.9 * (math.sin(math.pi * min(f / 0.35, 1)) if f < 0.35 else 0)
        y = 740 if i < 2 else 700
        s = 1.05 if i < 2 else 0.9
        elf(cr, x, y + 40, s, look(12 + i), t, arm_r=sip, tool_r=tool_mug, arm_l=0.4, facing=facing,
            tilt=-0.08 * (sip - 1.6) * facing, blink_v=0.1 if f < 0.3 else blink(t, 2, i * 0.3), sway_off=i * 0.3)
        hx = x + facing * s * (36 + 70 * math.sin(sip)) + facing * 20
        hy = y + 40 - s * (142 - 70 * math.cos(sip)) - 40
        steam(cr, hx, hy - 20, t, off=i * 0.2, h=60)
    # a sleepy cat by the fire, tail swishing
    cx, cy = 770, 930
    ellipse(cr, cx, cy, 70, 34)
    outline_fill(cr, "#E8873A")
    cr.arc(cx - 60, cy - 20, 28, 0, TAU)
    outline_fill(cr, "#E8873A")
    for d in (-1, 1):
        cr.move_to(cx - 60 + d * 10, cy - 44)
        cr.line_to(cx - 60 + d * 26, cy - 62)
        cr.line_to(cx - 60 + d * 26, cy - 36)
        cr.close_path()
        outline_fill(cr, "#E8873A", width=3)
    cr.move_to(cx - 70, cy - 20)
    cr.line_to(cx - 60, cy - 18)
    cr.move_to(cx - 52, cy - 20)
    cr.line_to(cx - 42, cy - 18)
    stroke(cr, "#3A2010", 3)
    cr.move_to(cx + 64, cy + 10)
    cr.curve_to(cx + 120, cy + 20, cx + 110, cy - 40 + 24 * math.sin(ph(t, 4)), cx + 140, cy - 50)
    stroke(cr, "#E8873A", 14)
    # zzz
    for k in range(3):
        f = frac(t, 2, k / 3)
        text(cr, "z", cx - 30 + f * 50, cy - 70 - f * 90, 22 + f * 20, "#FFFFFF", outline=None)
    warm_light(cr, 440, 700, 900, 0.12)
    vignette(cr, 0.6)
    badge(cr, "Cocoa Break", t)


_cocoa_base_p = _cached(_cocoa_base)


# 7. letters to Santa mail room

_sky7 = Sky(7)


def _mail_base(cr):
    wall(cr, "#A0693E", "#714226")
    # cubby wall
    for r in range(4):
        for c in range(6):
            x, y = 1180 + c * 105, 170 + r * 120
            rrect(cr, x, y, 96, 108, 6)
            outline_fill(cr, "#6B3E1C", line="#3A2010")
            for k in range(1 + (r * 7 + c * 3) % 3):
                rrect(cr, x + 10 + k * 8, y + 46 - k * 6, 70, 52, 3)
                outline_fill(cr, "#FFF6E0", line="#8A6A4A", width=2)
    # mail chute pipe
    cr.move_to(120, 0)
    cr.line_to(120, 300)
    cr.curve_to(120, 380, 160, 420, 260, 420)
    stroke(cr, "#3A3F48", 120)
    cr.move_to(120, 0)
    cr.line_to(120, 300)
    cr.curve_to(120, 380, 160, 420, 260, 420)
    stroke(cr, "#8A929E", 96)


def mail(cr, t):
    _mail_base_p(cr)
    window(cr, _sky7, 640, 150, 340, 360, t)
    garland(cr, t, sag=40)
    lantern(cr, 1060, 120, t, 0.5)
    # letters fly out of the chute in an arc and land in a big basket, 6 per loop
    rrect(cr, 380, 800, 320, 150, 40)
    outline_fill(cr, "#C98B4A", line="#5A3A20")
    for k in range(7):
        cr.move_to(390 + k * 44, 810)
        cr.line_to(410 + k * 44, 940)
        stroke(cr, "#8A5A2E", 3)
    for i in range(10):
        rrect(cr, 400 + (i * 29) % 260, 770 + (i * 13) % 30, 60, 40, 3)
        outline_fill(cr, "#FFF6E0", line="#8A6A4A", width=2)
    for k in range(6):
        u = frac(t, 6, k / 6)
        if u > 0.7:
            continue
        v = u / 0.7
        x = 300 + v * 240
        y = 420 - 200 * math.sin(math.pi * v * 0.8) + v * v * 380
        cr.save()
        cr.translate(x, y)
        cr.rotate(v * TAU * 1.5)
        tool_letter(cr)
        cr.restore()
    # elf sorting letters into cubbies: arm sweeps up, 4 per loop
    f = frac(t, 4)
    reach = 0.6 + 1.8 * math.sin(math.pi * f)
    elf(cr, 1120, 840, 1.15, look(14), t, arm_r=reach, tool_r=tool_letter if f < 0.5 else None, arm_l=0.4,
        facing=1, blink_v=blink(t, 3, 0.4), tilt=-0.1 * math.sin(math.pi * f), sway_off=0.2)
    # stamp elf: thump, thump
    a, hit = hammer_swing(t, 8, 0.3)
    rrect(cr, 760, 820, 260, 30, 8)
    outline_fill(cr, "#8A5A2E", line="#3A2010")
    cr.rectangle(780, 850, 24, 120)
    cr.rectangle(976, 850, 24, 120)
    fill(cr, "#6B3E1C")
    rrect(cr, 850, 790, 90, 34, 3)
    outline_fill(cr, "#FFF6E0", line="#8A6A4A", width=2)
    elf(cr, 820, 860, 1.0, look(15), t, arm_r=a * 0.8, arm_l=0.3, blink_v=blink(t, 2, 0.9), sway_off=0.5,
        mouth="open" if hit else "smile")
    if hit:
        star_path(cr, 900, 800, 30, 14, points=8)
        fill(cr, "#D7263D", 0.85)
        text(cr, "OK!", 900, 760, 34, "#FFD23F")
    vignette(cr)
    badge(cr, "Letters to Santa Mail Room", t)


_mail_base_p = _cached(_mail_base)


# 8. outside: the workshop under the northern lights

_sky8 = Sky(8, n_stars=200, n_snow=240)


def outside(cr, t):
    _sky8.draw(cr, 0, 0, W, H, t, hills=False)
    # snowy ground
    cr.move_to(0, 760)
    cr.curve_to(500, 700, 1300, 820, W, 730)
    cr.line_to(W, H)
    cr.line_to(0, H)
    cr.close_path()
    fill(cr, "#EEF5FF")
    # the workshop building
    bx, by, bw, bh = 640, 430, 640, 360
    cr.rectangle(bx, by, bw, bh)
    outline_fill(cr, "#8A3B2A", line="#3A1410", width=6)
    for r in range(8):
        cr.move_to(bx, by + r * bh / 8)
        cr.line_to(bx + bw, by + r * bh / 8)
        stroke(cr, "#6A2A1C", 3, 0.6)
    cr.move_to(bx - 60, by + 10)
    cr.line_to(bx + bw / 2, by - 210)
    cr.line_to(bx + bw + 60, by + 10)
    cr.close_path()
    outline_fill(cr, "#FFFFFF", line="#A9B8CC", width=6)
    # chimney + smoke
    cr.rectangle(bx + bw - 170, by - 200, 70, 130)
    outline_fill(cr, "#7A3020", line="#3A1410")
    for k in range(4):
        f = frac(t, 4, k / 4)
        cr.arc(bx + bw - 135 + 30 * math.sin(f * 4 + k), by - 210 - f * 220, 18 + f * 30, 0, TAU)
        fill(cr, "#DDE6F5", 0.55 * (1 - f))
    # glowing windows with little elf silhouettes passing
    for i, wx in enumerate((bx + 70, bx + 250, bx + 430)):
        warm_light(cr, wx + 60, by + 130, 160, 0.35)
        rrect(cr, wx, by + 70, 120, 110, 10)
        outline_fill(cr, "#FFD27A", line="#3A1410", width=6)
        cr.save()
        rrect(cr, wx, by + 70, 120, 110, 10)
        cr.clip()
        ex = wx - 40 + frac(t, 2, i * 0.37) * 200
        cr.arc(ex, by + 150, 22, 0, TAU)
        fill(cr, "#7A3E1D", 0.55)
        cr.move_to(ex - 20, by + 140)
        cr.line_to(ex + 6, by + 90)
        cr.line_to(ex + 22, by + 140)
        fill(cr, "#7A3E1D", 0.55)
        cr.restore()
        cr.move_to(wx + 60, by + 70)
        cr.line_to(wx + 60, by + 180)
        cr.move_to(wx, by + 125)
        cr.line_to(wx + 120, by + 125)
        stroke(cr, "#3A1410", 6)
    # door
    rrect(cr, bx + bw / 2 - 60, by + 220, 120, 140, 20)
    outline_fill(cr, "#5A2A10", line="#2A1008")
    cr.arc(bx + bw / 2 + 36, by + 300, 7, 0, TAU)
    fill(cr, "#FFD23F")
    # sign
    rrect(cr, bx + bw / 2 - 210, by - 70, 420, 70, 12)
    outline_fill(cr, "#D7263D", line="#5A0E16", width=6)
    text(cr, "SANTA'S WORKSHOP", bx + bw / 2, by - 35, 40, "#FFF6E0", outline="#5A0E16", ow=6)
    # string lights along the roof edges
    for side in (0, 1):
        for k in range(16):
            u = (k + 0.5) / 16
            if side == 0:
                x, y = bx - 60 + u * (bw / 2 + 60), by + 10 - u * 220
            else:
                x, y = bx + bw / 2 + u * (bw / 2 + 60), by - 210 + u * 220
            on = 0.5 + 0.5 * math.sin(ph(t, 4, k * 0.29 + side * 0.5))
            c = rgb(BULBS[k % len(BULBS)])
            warm_light(cr, x, y + 8, 26, 0.6 * on, BULBS[k % len(BULBS)])
            cr.arc(x, y + 8, 7, 0, TAU)
            cr.set_source_rgba(c[0], c[1], c[2], 0.5 + 0.5 * on)
            cr.fill()
    # Christmas tree beside the workshop
    tx, ty = 1500, 800
    for k in range(4):
        cr.move_to(tx, ty - 420 + k * 70)
        cr.line_to(tx - 90 - k * 40, ty - 280 + k * 80)
        cr.line_to(tx + 90 + k * 40, ty - 280 + k * 80)
        cr.close_path()
        outline_fill(cr, "#1E7A45", line="#0E3A20", width=5)
    cr.rectangle(tx - 22, ty - 40, 44, 40)
    fill(cr, "#5A3A20")
    rnd = random.Random(9)
    for k in range(26):
        lx = tx + rnd.uniform(-1, 1) * (40 + k * 7)
        ly = ty - 380 + k * 13
        on = 0.5 + 0.5 * math.sin(ph(t, 4, k * 0.41))
        col = BULBS[k % len(BULBS)]
        warm_light(cr, lx, ly, 22, 0.7 * on, col)
        cr.arc(lx, ly, 7, 0, TAU)
        fill(cr, col, 0.5 + 0.5 * on)
    star_path(cr, tx, ty - 430, 40, 17)
    fill(cr, "#FFD23F")
    warm_light(cr, tx, ty - 430, 90, 0.5 + 0.3 * math.sin(ph(t, 4)), "#FFE27A")
    # reindeer resting outside
    reindeer(cr, 420, 900, 0.8, t, 0.2, facing=1)
    # an elf walking in with a sack, once a loop
    u = frac(t, 1)
    ex = 1900 - u * 1000
    if 600 < ex < 1990:
        hop = 8 * abs(math.sin(ph(t, 16)))
        elf(cr, ex, 900, 0.9, look(16), t, arm_r=1.6, arm_l=0.3, hop=hop, facing=-1, sway_off=0.4,
            blink_v=blink(t, 2, 0.2))
        cr.move_to(ex - 40, 900 - hop - 180)
        cr.curve_to(ex - 120, 900 - hop - 260, ex - 20, 900 - hop - 330, ex + 20, 900 - hop - 220)
        cr.close_path()
        outline_fill(cr, "#C9A87A")
    vignette(cr, 0.4)
    badge(cr, "North Pole Tonight", t)


SCENES: dict[str, Scene] = {s.id: s for s in [
    Scene("assembly-line", "Toy Assembly Line", "Watch the elves build toys on the assembly line", assembly),
    Scene("gift-wrapping", "Gift Wrapping Room", "Watch the elves wrap presents for Christmas", wrapping),
    Scene("train-track", "Toy Train Test Track", "Watch the elves test a toy train", train),
    Scene("teddy-bears", "Teddy Bear Workshop", "Watch an elf sew a teddy bear", bears),
    Scene("sleigh-bay", "Sleigh Loading Bay", "Watch the elves load Santa's sleigh", sleigh),
    Scene("cocoa-break", "Cocoa Break", "The elves take a cozy cocoa break", cocoa),
    Scene("mail-room", "Letters to Santa Mail Room", "Watch the elves sort letters to Santa", mail),
    Scene("north-pole", "North Pole Tonight", "Santa's workshop under the northern lights", outside),
]}


# --- countdown cards -------------------------------------------------------------------------------------

def countdown_line(sleeps: int) -> tuple[str, str]:
    if sleeps <= 0:
        return "MERRY CHRISTMAS!", "from all the elves"
    if sleeps == 1:
        return "1 MORE SLEEP", "until Christmas!"
    return f"{sleeps} SLEEPS", "until Christmas!"


def countdown(sleeps: int) -> Callable:
    sky = Sky(20 + sleeps, n_snow=200)
    rnd = random.Random(sleeps)
    confetti = [(rnd.random(), rnd.random(), rnd.choice(TOY), rnd.uniform(0, TAU), rnd.choice((1, 2)))
                for _ in range(90)]
    big, small = countdown_line(sleeps)

    def draw(cr, t):
        sky.draw(cr, 0, 0, W, H, t)
        for x, y, col, rot, n in confetti:
            yy = (y + frac(t, n * 2)) % 1.0
            cr.save()
            cr.translate(x * W + 30 * math.sin(ph(t, 4, x)), yy * H)
            cr.rotate(rot + ph(t, 4, y))
            cr.rectangle(-9, -5, 18, 10)
            fill(cr, col)
            cr.restore()
        # two elves holding a big wooden sign that bounces gently
        b = 10 * math.sin(ph(t, 4))
        elf(cr, 250, 1010, 1.25, look(1), t, arm_r=2.3, arm_l=1.4, bob=b * 0.3, blink_v=blink(t, 2, 0.2),
            mouth="open", sway_off=0.1)
        elf(cr, 1670, 1010, 1.25, look(2), t, arm_r=2.3, arm_l=1.4, facing=-1, bob=-b * 0.3,
            blink_v=blink(t, 2, 0.6), mouth="open", sway_off=0.6)
        cr.save()
        cr.translate(W / 2, 470 + b)
        rrect(cr, -560, -230, 1120, 420, 40)
        outline_fill(cr, "#9A5E30", line="#3A2010", width=10)
        rrect(cr, -530, -200, 1060, 360, 30)
        stroke(cr, "#FFD23F", 6)
        for k in range(8):
            ang = TAU * k / 8 + ph(t, 1)
            sparkle(cr, 600 * math.cos(ang) * 0.95, 260 * math.sin(ang), 18, 0.8)
        pop = 1.0 + 0.04 * math.sin(ph(t, 8))
        cr.scale(pop, pop)
        text(cr, big, 0, -60, 150, "#FFFFFF", outline="#B3121F", ow=18, max_w=980)
        text(cr, small, 0, 95, 78, "#FFF2A8", outline="#5A1E12", ow=12, max_w=980)
        cr.restore()
        vignette(cr, 0.4)
    return draw


# --- encoding ------------------------------------------------------------------------------------------

def encode_command(dst: Path, *, width: int = W, height: int = H, fps: int = FPS, crf: int | None = None) -> list[str]:
    """Raw BGRA frames on stdin -> H.264 at live-ingest settings (2 s closed GOPs,
    at most 4.5 Mbps), all with the same settings, so loops and cards concatenate by copy."""
    rate = ["-crf", str(crf or 20), "-maxrate", "4500k", "-bufsize", "9000k"]
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "medium", "-tune", "animation", *rate,
            "-pix_fmt", "yuv420p", "-g", str(GOP), "-keyint_min", str(GOP), "-sc_threshold", "0",
            "-x264-params", "open-gop=0", "-movflags", "+faststart", str(dst)]


def render(draw: Callable, dst: Path, seconds: int = LOOP_S, *, fps: int = FPS, crf: int | None = None,
           vertical: bool = False) -> Path:
    """Render `seconds` of a scene to dst. vertical: a 1080x1920 crop for Shorts
    (the middle of the frame, scaled up so the elves fill it)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    out_w, out_h = (1080, 1920) if vertical else (W, H)
    proc = subprocess.Popen(encode_command(dst, width=out_w, height=out_h, fps=fps, crf=crf), stdin=subprocess.PIPE)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, out_w, out_h) if vertical else surf
    try:
        for i in range(seconds * fps):
            t = (i / fps) % LOOP_S
            cr = cairo.Context(surf)
            draw(cr, t)
            if vertical:
                oc = cairo.Context(out)
                oc.set_source_rgb(0, 0, 0)
                oc.paint()
                k = out_h / H
                oc.translate(out_w / 2 - W * k / 2, 0)
                oc.scale(k, k)
                oc.set_source_surface(surf, 0, 0)
                oc.paint()
            out.flush()
            proc.stdin.write(bytes(out.get_data()))
        proc.stdin.close()
    except BaseException:
        proc.kill()
        raise
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed encoding {dst}")
    return dst


def still(draw: Callable, dst: Path, t: float = 4.0) -> Path:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    draw(cairo.Context(surf), t)
    dst.parent.mkdir(parents=True, exist_ok=True)
    surf.write_to_png(str(dst))
    return dst


def thumbnail(dst: Path) -> Path:
    """The live thumbnail: the assembly line with a big title, 1280x720 JPEG."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(surf)
    assembly(cr, 6.0)
    g = cairo.LinearGradient(0, 0, 0, 420)
    g.add_color_stop_rgba(0, 0.05, 0.02, 0.0, 0.75)
    g.add_color_stop_rgba(1, 0.05, 0.02, 0.0, 0.0)
    cr.rectangle(0, 0, W, 420)
    cr.set_source(g)
    cr.fill()
    text(cr, "SANTA'S WORKSHOP", W / 2, 150, 170, "#FFFFFF", outline="#B3121F", ow=24, max_w=1760)
    rrect(cr, W / 2 - 180, 262, 360, 96, 48)
    fill(cr, "#E0182D")
    text(cr, "● LIVE", W / 2, 312, 72, "#FFFFFF", outline=None)
    rrect(cr, 60, 900, 760, 120, 30)
    fill(cr, "#1E9E5A")
    text(cr, "Christmas Countdown!", 440, 960, 66, "#FFFFFF", outline="#0E4A2A", ow=8, max_w=700)
    png = dst.with_suffix(".png")
    surf.write_to_png(str(png))
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(png), "-vf", "scale=1280:720",
                    "-q:v", "3", str(dst)], check=True)
    png.unlink(missing_ok=True)
    return dst
