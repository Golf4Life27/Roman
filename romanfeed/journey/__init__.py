"""Fly-into journeys: one real telescope image, flown into with depth parallax.

  depth.py     how far away each part of the picture looks (model or brightness)
  layers.py    stars lifted off the gas; bright regions worth flying toward
  camera.py    where the camera is at every moment (passes, reveals, Shorts)
  render.py    frames from the scene, piped into ffmpeg, one clip per pass
  short.py     the 20-30 s vertical cut
  metadata.py  titles and honest descriptions
  run.py       the whole job, behind the journey.enabled publish switch
"""
