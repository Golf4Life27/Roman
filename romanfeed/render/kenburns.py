"""Ken Burns segment builder: one still -> one slow-zoom video clip.

Each image alternates zoom-in / zoom-out and drifts toward a different
anchor so back-to-back clips never feel identical. Fades in and out so
the concat step needs no cross-fade graph (which is O(n) filters and
fragile at 80+ clips)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from romanfeed.render import ffmpeg

ANCHORS = [
    ("iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),  # centre
    ("iw/2-(iw/zoom/2)*1.3", "ih/2-(ih/zoom/2)*0.7"),  # drift right/up
    ("iw/2-(iw/zoom/2)*0.7", "ih/2-(ih/zoom/2)*1.3"),  # drift left/down
    ("iw/2-(iw/zoom/2)*1.2", "ih/2-(ih/zoom/2)*1.2"),  # drift right/down
]


@dataclass
class TimedOverlay:
    """A transparent PNG shown from `start` to `end` seconds (None = to the
    end of the clip), faded in and out on its alpha so it never pops."""

    png: Path
    start: float = 0.0
    end: float | None = None
    fade: float = 1.0

    def alpha_filter(self) -> str:
        f = f"format=rgba,fade=t=in:st={self.start:.3f}:d={self.fade:.3f}:alpha=1"
        if self.end is not None:
            f += f",fade=t=out:st={max(self.end - self.fade, self.start):.3f}:d={self.fade:.3f}:alpha=1"
        return f


@dataclass
class Segment:
    index: int
    frame_png: Path
    out_mp4: Path
    duration: float
    fps: int
    width: int
    height: int
    max_zoom: float = 1.18
    fade: float = 2.0
    crf: int = 20
    preset: str = "medium"
    caption_png: Path | None = None
    # Extra cards (intro, subscribe prompt) on top of the caption. With any
    # of these present the caption itself waits for `caption_from` seconds,
    # so the intro card never shares the screen with the lower third.
    timed: list[TimedOverlay] = field(default_factory=list)
    caption_from: float = 0.0

    def zoom_expr(self) -> str:
        frames = max(int(self.duration * self.fps), 1)
        step = (self.max_zoom - 1.0) / frames
        if self.index % 2 == 0:
            return f"min(zoom+{step:.6f},{self.max_zoom})"
        return f"if(eq(on,1),{self.max_zoom},max(zoom-{step:.6f},1.0))"

    def zoompan_filter(self) -> str:
        frames = max(int(self.duration * self.fps), 1)
        x, y = ANCHORS[self.index % len(ANCHORS)]
        return (
            f"zoompan=z='{self.zoom_expr()}':x='{x}':y='{y}':d={frames}:"
            f"s={self.width}x{self.height}:fps={self.fps}"
        )

    def fade_filter(self) -> str:
        fade_out_start = max(self.duration - self.fade, 0)
        return f"fade=t=in:st=0:d={self.fade},fade=t=out:st={fade_out_start:.3f}:d={self.fade},format=yuv420p"

    def filter_graph(self) -> str:
        """Filter graph. With a caption layer this is a filter_complex: the
        caption is overlaid after zoompan so it is never cropped by the zoom.
        Inputs are numbered frame=0, caption=1 (if any), then the timed cards."""
        if self.caption_png is None and not self.timed:
            return f"{self.zoompan_filter()},{self.fade_filter()}"
        if not self.timed and self.caption_from <= 0:
            return f"[0:v]{self.zoompan_filter()}[z];[z][1:v]overlay=0:0:format=auto,{self.fade_filter()}[out]"
        layers: list[TimedOverlay | None] = []
        if self.caption_png is not None:
            # None = shown from the first frame, exactly as without cards.
            layers.append(TimedOverlay(self.caption_png, start=self.caption_from, fade=1.5) if self.caption_from > 0 else None)
        layers += self.timed
        parts, prev = [f"[0:v]{self.zoompan_filter()}[z]"], "[z]"
        for k, layer in enumerate(layers, start=1):
            if layer is None:
                parts.append(f"{prev}[{k}:v]overlay=0:0:format=auto[v{k}]")
            else:
                parts.append(f"[{k}:v]{layer.alpha_filter()}[o{k}]")
                parts.append(f"{prev}[o{k}]overlay=0:0:format=auto[v{k}]")
            prev = f"[v{k}]"
        parts.append(f"{prev}{self.fade_filter()}[out]")
        return ";".join(parts)

    def ffmpeg_args(self) -> list[str]:
        inputs = ["-loop", "1", "-framerate", str(self.fps), "-i", str(self.frame_png)]
        if self.caption_png is None and not self.timed:
            graph = ["-vf", self.filter_graph()]
        else:
            pngs = ([self.caption_png] if self.caption_png is not None else []) + [t.png for t in self.timed]
            for png in pngs:
                inputs += ["-loop", "1", "-framerate", str(self.fps), "-i", str(png)]
            graph = ["-filter_complex", self.filter_graph(), "-map", "[out]"]
        return [
            *inputs, *graph,
            "-t", f"{self.duration:.3f}",
            "-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf),
            "-r", str(self.fps), "-an",
            str(self.out_mp4),
        ]

    def render(self) -> Path:
        self.out_mp4.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.run(self.ffmpeg_args())
        return self.out_mp4
