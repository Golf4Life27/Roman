"""Where the camera is at every moment of a journey.

The scene is the picture on a stack of depths: Z = 1 for the nearest part,
Z = 1 + depth_range for the farthest. The camera starts where the telescope
was (so frame one is the picture exactly) and travels `travel` units forward
toward the point `(cx, cy)`; a point at depth Z then looks Z / (Z - travel)
times bigger, so near parts grow and slide outward faster than far ones.
That difference is the parallax. `zoom` is a lens push on top, the same for
every layer.

A journey is a run of passes ("legs"). Each leg flies toward one bright
region and settles there: the arrival is the reveal, every few minutes. Early
legs are shorter and push harder; from `calm_at` seconds on they are long and
gentle, in step with the music settling. Legs fade in and out through black,
so each is its own clip and the long cuts can reuse them."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from romanfeed.journey.layers import Target


@dataclass
class Pose:
    cx: float        # point flown toward, source pixels
    cy: float
    travel: float    # 0 = the telescope's own view; must stay below 1 (the nearest layer)
    zoom: float      # lens push, >= 1
    fade: float = 1.0  # brightness, 0 = black


def smoother(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * x * (x * (x * 6 - 15) + 10)


def ease_out(x: float, p: float = 2.2) -> float:
    """Fast at first, gliding to a stop: a camera already in motion."""
    x = min(1.0, max(0.0, x))
    return 1.0 - (1.0 - x) ** p


CREEP = 0.05   # extra travel while settling, as a fraction of the approach


def energy_at(t: float, calm_at: float) -> float:
    """1 at the start of the journey, 0 from `calm_at` seconds on."""
    if calm_at <= 0:
        return 0.0
    return 1.0 - smoother(t / calm_at)


@dataclass
class Leg:
    index: int
    start: float          # seconds into the journey
    duration: float
    energy: float         # 0 calm .. 1 lively, at the leg's start
    c0: tuple[float, float]
    c1: tuple[float, float]
    travel0: float
    travel1: float
    zoom0: float
    zoom1: float
    arrive: float = 0.72  # fraction of the leg spent flying in; the rest settles
    fade_in: float = 1.5
    fade_out: float = 1.5
    drift: tuple[float, float] = (0.0, 0.0)   # px/s while settling, so it never freezes
    curve: str = "smooth"   # "smooth": eases in and out; "out": starts at speed (a Short's opening)

    @property
    def reveal_at(self) -> float:
        """Seconds into the journey when this leg arrives at its target."""
        return self.start + self.duration * self.arrive

    def pose(self, s: float) -> Pose:
        """Pose `s` seconds into this leg."""
        u = s / self.duration if self.duration > 0 else 1.0
        curve = ease_out if self.curve == "out" else smoother
        a = curve(u / self.arrive)
        # The look-at point turns a little ahead of the flight, so the camera
        # is already facing the target when it gets close.
        aim = curve(min(1.0, u / (self.arrive * 0.8)))
        settle = max(0.0, s - self.duration * self.arrive)
        settle_len = max(1.0, self.duration * (1 - self.arrive))
        cx = self.c0[0] + (self.c1[0] - self.c0[0]) * aim + self.drift[0] * settle
        cy = self.c0[1] + (self.c1[1] - self.c0[1]) * aim + self.drift[1] * settle
        # Settling keeps a slow creep forward, so the picture never freezes.
        travel = self.travel0 + (self.travel1 - self.travel0) * (a + CREEP * settle / settle_len)
        zoom = self.zoom0 + (self.zoom1 - self.zoom0) * a
        fade = min(1.0, s / self.fade_in if self.fade_in > 0 else 1.0,
                   (self.duration - s) / self.fade_out if self.fade_out > 0 else 1.0)
        return Pose(cx, cy, travel, zoom, max(0.0, fade))

    @property
    def peak_travel(self) -> float:
        return self.travel0 + (self.travel1 - self.travel0) * (1 + CREEP)


@dataclass
class Frame:
    """What the camera can see without leaving the picture."""
    src_w: int
    src_h: int
    out_w: int
    out_h: int

    @property
    def cover(self) -> float:
        """Output pixels per source pixel when the picture just fills the frame."""
        return max(self.out_w / self.src_w, self.out_h / self.src_h)

    def clamp(self, pose: Pose) -> Pose:
        """Keep the view inside the picture. Every layer is magnified at
        least as much as the lens (travel >= 0), so the lens view's bounds
        bound what any layer samples."""
        k = self.cover * pose.zoom
        hx, hy = self.out_w / 2 / k, self.out_h / 2 / k
        cx = min(max(pose.cx, hx), self.src_w - hx) if hx * 2 < self.src_w else self.src_w / 2
        cy = min(max(pose.cy, hy), self.src_h - hy) if hy * 2 < self.src_h else self.src_h / 2
        return Pose(cx, cy, pose.travel, pose.zoom, pose.fade)

    def max_zoom(self, min_detail: float = 0.6) -> float:
        """Largest total magnification before the picture runs out of detail:
        at least `min_detail` source pixels behind every output pixel."""
        return 1.0 / (min_detail * self.cover)


def magnification(pose: Pose) -> float:
    """How much the nearest layer is enlarged, lens included."""
    return pose.zoom / max(1e-3, 1.0 - pose.travel)


def plan_journey(seconds: float, frame: Frame, targets: list[Target], *, seed: int = 0, calm_at: float = 1200.0,
                 depth_range: float = 1.5, lively_leg: float = 150.0, calm_leg: float = 285.0,
                 min_detail: float = 0.6) -> list[Leg]:
    """Legs covering `seconds`, each flying to the next target in turn."""
    rng = np.random.default_rng(seed)
    cap = frame.max_zoom(min_detail)
    legs: list[Leg] = []
    t = 0.0
    order = list(range(len(targets)))
    prev = (frame.src_w / 2, frame.src_h / 2)
    i = 0
    while t < seconds - 1.0:
        e = energy_at(t, calm_at)
        dur = lively_leg + (calm_leg - lively_leg) * (1 - e)
        dur *= float(rng.uniform(0.9, 1.1))
        if seconds - (t + dur) < 0.5 * calm_leg:     # don't leave a stub at the end
            dur = seconds - t
        tgt = targets[order[i % len(order)]]
        if i and i % len(order) == 0:
            rng.shuffle(order)
        # Lively legs fly deeper (more parallax); calm ones drift in gently.
        travel1 = 0.30 + 0.22 * e
        zoom0 = 1.08 if i else 1.0
        zoom1 = zoom0 + 0.18 + 0.22 * e
        # Respect the picture's resolution: shrink the push until the nearest
        # layer still has detail behind every output pixel.
        while zoom1 / (1 - travel1 * (1 + CREEP)) > cap and travel1 > 0.12:
            travel1 -= 0.02
            zoom1 = max(zoom0, zoom1 - 0.02)
        if zoom1 / (1 - travel1 * (1 + CREEP)) > cap:
            zoom1 = max(1.0, cap * (1 - travel1 * (1 + CREEP)))
            zoom0 = min(zoom0, zoom1)
        # Start each pass a little off the target, on the side we came from,
        # so the camera visibly turns toward it.
        start_c = (prev[0] + (tgt.x - prev[0]) * 0.35, prev[1] + (tgt.y - prev[1]) * 0.35) if i else prev
        # While settling, drift roughly back toward the middle of the picture
        # (never off its edge, where the view would pin and stall).
        ang = math.atan2(frame.src_h / 2 - tgt.y, frame.src_w / 2 - tgt.x) + float(rng.uniform(-1.0, 1.0))
        speed = (6.0 + 10.0 * e) / (frame.cover * zoom1)   # ~ output px/s turned into source px/s
        legs.append(Leg(
            index=i, start=t, duration=dur, energy=e, c0=start_c, c1=(tgt.x, tgt.y),
            travel0=0.0 if i == 0 else 0.04, travel1=travel1, zoom0=zoom0, zoom1=zoom1,
            arrive=0.62 + 0.12 * (1 - e), drift=(speed * math.cos(ang), speed * math.sin(ang)),
            fade_in=2.0 if i == 0 else 1.5, fade_out=1.5,
        ))
        prev = (tgt.x, tgt.y)
        t += dur
        i += 1
    return legs


def short_legs(seconds: float, frame: Frame, targets: list[Target], *, payoff_by: float = 3.0,
               reveal_for: float = 5.0, min_detail: float = 0.55) -> list[Leg]:
    """A Short's three moves, back to back with no fades between them:

      1. opens already moving (travel > 0, no fade from black) and lands on
         the first bright region by `payoff_by` seconds;
      2. a slow drift across toward the brightest region;
      3. the final reveal: a push onto it over the last `reveal_for` seconds,
         ending close in."""
    cap = frame.max_zoom(min_detail)
    first = targets[1] if len(targets) > 1 else targets[0]
    best = targets[0]
    mid = seconds - payoff_by - reveal_for
    if mid <= 0:
        raise ValueError(f"a {seconds:g} s Short has no room for a {payoff_by:g} s payoff and {reveal_for:g} s reveal")

    def fit(travel: float, zoom: float) -> tuple[float, float]:
        while zoom / (1 - travel) > cap and travel > 0.1:
            travel -= 0.02
        return travel, min(zoom, max(1.0, cap * (1 - travel)))

    t1, z1 = fit(0.30, 1.25)
    t2, z2 = fit(0.36, 1.30)
    t3, z3 = fit(0.55, 1.6)
    # Open off to one side of the first region, already flying toward it.
    open_c = ((first.x + frame.src_w / 2) / 2, (first.y + frame.src_h / 2) / 2)
    t0, z0 = min(0.12, 0.4 * t1), min(1.12, 1 + 0.4 * (z1 - 1))     # already part-way in
    return [
        Leg(0, 0.0, payoff_by, 1.0, open_c, (first.x, first.y), t0, t1, z0, z1,
            arrive=0.999, fade_in=0.0, fade_out=0.0, curve="out"),
        Leg(1, payoff_by, mid, 0.4, (first.x, first.y), ((first.x + best.x) / 2, (first.y + best.y) / 2), t1, t2, z1, z2,
            arrive=0.999, fade_in=0.0, fade_out=0.0),
        Leg(2, payoff_by + mid, reveal_for, 1.0, ((first.x + best.x) / 2, (first.y + best.y) / 2), (best.x, best.y),
            t2, t3, z2, z3, arrive=0.85, fade_in=0.0, fade_out=0.0),
    ]
