"""Runway developer API: a still from text, then a seamless loop from the still.

The developer API is billed separately from the Runway app plan: prepaid
credits at $0.01 each, bought at dev.runwayml.com. The key is the repository
secret RUNWAYML_API_SECRET. Costs (docs, 2026-10):

  gemini_image3_pro  2752:1536 still            20 credits
  seedance2          1920:1080, 10 s, first =   40 credits/s -> 400
                     last frame (a true loop)

So one scene is ~420 credits ($4.20). The generator checks the monthly cap
in scenes.json before every scene, and the API's own balance as well.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import time
from dataclasses import dataclass

import requests
from PIL import Image

log = logging.getLogger(__name__)

API = "https://api.dev.runwayml.com"
VERSION = "2024-11-06"

IMAGE_RATIOS = {"gemini_image3_pro": "2752:1536", "gemini_image3.1_flash": "2752:1536", "gen4_image": "1920:1080"}
IMAGE_CREDITS = {"gemini_image3_pro": 20, "gemini_image3.1_flash": 11, "gen4_image": 8}
VIDEO_CREDITS_PER_S = {"seedance2": 40, "gemini_omni_flash_1.1": 15}
LOOP_SECONDS = 10


def scene_cost(image_model: str, video_model: str, seconds: int = LOOP_SECONDS) -> int:
    return IMAGE_CREDITS.get(image_model, 20) + VIDEO_CREDITS_PER_S.get(video_model, 40) * seconds


class RunwayError(RuntimeError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code

    @property
    def retryable(self) -> bool:
        return bool(self.code) and (self.code.startswith("INTERNAL") or self.code == "THIRD_PARTY.UNAVAILABLE")


def api_secret() -> str | None:
    return os.environ.get("RUNWAYML_API_SECRET", "").strip() or None


@dataclass
class Client:
    secret: str
    session: requests.Session | None = None
    poll_s: float = 10.0
    timeout_s: float = 30 * 60

    def __post_init__(self):
        self.session = self.session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.secret}", "X-Runway-Version": VERSION,
                                     "Content-Type": "application/json"})

    def _post(self, path: str, body: dict) -> str:
        r = self.session.post(API + path, json=body, timeout=60)
        if r.status_code >= 400:
            raise RunwayError(f"{path} {r.status_code}: {r.text[:300]}")
        return r.json()["id"]

    def balance(self) -> int | None:
        r = self.session.get(API + "/v1/organization", timeout=30)
        return r.json().get("creditBalance") if r.ok else None

    def wait(self, task_id: str) -> str:
        """The task's first output URL, once it succeeds."""
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            r = self.session.get(f"{API}/v1/tasks/{task_id}", timeout=30)
            r.raise_for_status()
            t = r.json()
            if t["status"] == "SUCCEEDED":
                return t["output"][0]
            if t["status"] in {"FAILED", "CANCELLED"}:
                raise RunwayError(f"task {task_id} {t['status']}: {t.get('failure')}", t.get("failureCode"))
            time.sleep(self.poll_s)
        raise RunwayError(f"task {task_id} still running after {self.timeout_s:.0f} s")

    def image(self, prompt: str, *, model: str = "gemini_image3_pro") -> str:
        task = self._post("/v1/text_to_image", {"model": model, "promptText": prompt[:1000],
                                                "ratio": IMAGE_RATIOS.get(model, "1920:1080")})
        return self.wait(task)

    def loop(self, image_uri: str, prompt: str, *, model: str = "seedance2", seconds: int = LOOP_SECONDS) -> str:
        """A clip that starts and ends on the same image, so it loops."""
        body = {"model": model, "promptText": prompt[:1000], "ratio": "1920:1080", "duration": seconds,
                "promptImage": [{"uri": image_uri, "position": "first"}, {"uri": image_uri, "position": "last"}]}
        if model.startswith("seedance"):
            body["audio"] = False
        return self.wait(self._post("/v1/image_to_video", body))


def data_uri(image_bytes: bytes, *, max_bytes: int = 3_000_000) -> str:
    """A JPEG data URI under the API's 5 MB data-URI limit (a 2K PNG is ~8 MB)."""
    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    for q in (92, 85, 75):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q)
        if buf.tell() <= max_bytes:
            break
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
