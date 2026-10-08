"""Image and motion prompts for new scenes, per theme.

A scene prompt is a ROOM (a cozy place with a big view of the night sky and
its own warm light source) dressed with a theme's DECOR and SKY. The motion
prompt names only what the picture has -- the room's light source, the
theme's weather, the sky -- because a video model asked to animate snow that
is not there invents some.

The rules every prompt keeps (they are what made the starter scenes work):
  - locked-off camera, nothing moves but small natural motion
  - no people, no animals, no text or logos (moderation, and policy: a
    "real person" or a brand in an AI scene is trouble we do not need)
  - warm interior light against a deep night sky
Variants are picked by index, so the generator can walk them in order and
never make the same room twice for a theme.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Room:
    key: str
    title: str      # "Observatory", "Log Cabin"
    image: str      # the place, before decor
    motion: str     # its light source's motion


ROOMS = [
    Room("cabin", "Log Cabin",
         "A cozy log cabin living room at night with a crackling stone fireplace, a deep armchair with a "
         "knitted blanket, a steaming mug on a wooden side table, warm string lights, and a huge window "
         "looking out over a forest",
         "the fireplace flames flicker and dance gently, the string lights glow softly"),
    Room("observatory", "Observatory",
         "Inside a cozy old stone observatory at night, the dome's slit open to the sky, a large antique "
         "brass telescope on a wooden mount, a small cast-iron wood stove glowing, a reading chair with "
         "a blanket, a cup of tea on a crate, warm lantern light",
         "the fire in the wood stove flickers gently, the lantern flames flicker softly"),
    Room("lighthouse", "Lighthouse",
         "A cozy round room at the top of an old lighthouse at night, tall curved windows over a calm dark "
         "sea, a small black wood stove glowing, a padded window seat with blankets and pillows, an old "
         "brass lantern, a teapot and cup on a tray",
         "the fire in the wood stove flickers softly, the lantern flame flickers, the sea swells very slowly"),
    Room("spaceship", "Starship Lounge",
         "A cozy lounge aboard a quiet spaceship, a huge curved panoramic window onto deep space, a soft "
         "curved sofa with blankets and pillows, a steaming mug on a side table, warm amber lamps, wood "
         "and soft fabric textures",
         "the amber lamps glow warmly, the stars drift almost imperceptibly past the window"),
    Room("deck", "Stargazing Deck",
         "A covered wooden stargazing deck on a mountainside at night, a glowing fire pit, two deep chairs "
         "with thick blankets, warm string lights along the roof beams, a thermos and two mugs on a small "
         "table, a pair of binoculars",
         "the fire pit flames flicker and dance gently with a few rising sparks, the string lights twinkle softly"),
    Room("library", "Library",
         "A cozy old library tower room at night with tall bookshelves, a crackling fireplace, a leather "
         "armchair with a blanket, a brass telescope by a tall arched window, candles on the mantel, a "
         "steaming cup of tea",
         "the fireplace flames flicker gently, the candle flames flicker softly"),
    Room("greenhouse", "Glass Greenhouse",
         "A cozy glass greenhouse at night filled with potted plants and hanging lanterns, a small wood "
         "stove, a wicker chair with a blanket, a steaming mug, the whole glass roof open to the sky",
         "the lantern flames flicker softly, the wood stove glows and flickers gently"),
    Room("treehouse", "Treehouse",
         "A cozy treehouse room at night with a round window and an open balcony door, a small wood stove, "
         "a bed of cushions and quilts, fairy lights, a steaming mug, a small telescope on the balcony",
         "the wood stove fire flickers gently, the fairy lights twinkle softly"),
]


@dataclass(frozen=True)
class Decor:
    title: str     # prefix for the scene title: "Halloween", "" for none
    decor: str     # what is in the room
    sky: str       # what is out the window
    motion: str    # the theme's own motion (weather, sky)


DECOR = {
    "halloween": Decor("Halloween", "carved jack-o'-lanterns with candles, a few scattered autumn leaves, small gourds",
                       "a starry autumn night sky with a glowing orange-and-violet nebula and a big harvest moon",
                       "the candles inside the jack-o'-lanterns flicker softly, the stars twinkle subtly"),
    "thanksgiving": Decor("Harvest", "a harvest table with pumpkins, apples and a pie, a wool throw, dried corn and autumn leaves",
                          "a clear autumn night sky with the Milky Way over amber-leaved trees",
                          "a few autumn leaves drift slowly down outside, the stars twinkle subtly"),
    "autumn": Decor("Autumn", "a wool throw, a stack of books, a few autumn leaves on the sill",
                    "a clear autumn night sky with the Milky Way over amber-leaved trees",
                    "a few autumn leaves drift slowly down outside, the stars twinkle subtly"),
    "christmas": Decor("Christmas", "a small Christmas tree with warm white lights, a pine garland, wrapped presents, knitted stockings",
                       "a snowy night with the Milky Way and a soft green aurora",
                       "the Christmas lights twinkle softly, snow falls slowly and gently outside, the aurora ripples slowly"),
    "new_year": Decor("New Year's", "a bottle of sparkling cider and two glasses, gold string lights, a wool blanket",
                      "a clear winter night sky full of stars with distant soft fireworks low on the horizon",
                      "the distant fireworks bloom and fade softly on the horizon, the stars twinkle subtly"),
    "winter": Decor("Winter", "a thick wool blanket, a steaming mug of cocoa, a pair of snow boots by the door",
                    "a snowy night with the Milky Way over snow-covered pines",
                    "snow falls slowly and gently outside, the stars twinkle subtly"),
    "valentines": Decor("Valentine's", "a vase of red roses, two candles, a heart-shaped box of chocolates, a soft pink blanket",
                        "a clear night sky full of stars with a crescent moon",
                        "the candle flames flicker softly, the stars twinkle subtly"),
    "easter": Decor("Spring", "a vase of tulips and daffodils, a basket of pastel eggs, a light knit blanket",
                    "a clear spring night sky with the Milky Way over blossoming trees",
                    "a few petals drift slowly past the window, the stars twinkle subtly"),
    "spring": Decor("Spring", "potted flowers on the sill, a light knit blanket, a rain-speckled window",
                    "a spring night sky clearing after rain, stars and the Milky Way over blossoming trees",
                    "a few raindrops slide slowly down the glass, the stars twinkle subtly"),
    "summer": Decor("Summer", "an open window with a light curtain, a glass of iced tea, a woven blanket",
                    "a warm summer night sky with the bright Milky Way core over a lake",
                    "the curtain stirs gently in a breeze, fireflies glow and drift outside, the stars twinkle subtly"),
    "independence_day": Decor("Fourth of July", "a small flag bunting, a glass of lemonade, a woven blanket",
                              "a warm summer night over a lake with soft distant fireworks under the Milky Way",
                              "distant fireworks bloom and fade softly over the lake, fireflies drift, the stars twinkle"),
    "meteors": Decor("Meteor Shower", "a pair of binoculars, a star chart, a thick blanket",
                     "an extraordinarily clear dark sky with the Milky Way and several bright meteor streaks",
                     "bright meteors streak slowly across the sky now and then, the stars twinkle subtly"),
    "eclipse": Decor("Eclipse", "a star chart and a notebook, a thick blanket",
                     "a deep night sky with a coppery red eclipsed full moon among the stars",
                     "the stars twinkle subtly, the red moon glows softly"),
    "launch": Decor("Launch Night", "a radio, a mission patch on the wall, a thick blanket",
                    "a clear night sky full of stars with the bright arc of a distant rocket climbing toward orbit",
                    "the distant rocket's glowing trail climbs slowly across the sky, the stars twinkle subtly"),
    "evergreen": Decor("", "a wool blanket, a stack of books, a steaming mug",
                       "a clear night sky with the Milky Way and a colorful nebula",
                       "the stars twinkle subtly, the nebula glows softly"),
}

STYLE = ("Warm golden interior light against the deep blue night. Calm, peaceful, inviting, highly detailed, "
         "cinematic wide shot, no people, no animals, no text, no logos.")
MOTION_HEAD = "Locked-off static camera, no camera movement at all."
MOTION_TAIL = "Everything else stays perfectly still. Calm, peaceful, seamless ambient loop."


@dataclass(frozen=True)
class ScenePrompt:
    id: str
    title: str
    themes: list[str]
    image: str
    motion: str


def scene_prompt(theme: str, variant: int) -> ScenePrompt:
    """The variant-th room for a theme, dressed for it. Raises KeyError for
    a theme with no decor (add one to DECOR)."""
    d = DECOR[theme]
    room = ROOMS[variant % len(ROOMS)]
    title = f"{d.title} {room.title}".strip()
    image = f"{room.image}, with {d.decor}. Outside: {d.sky}. {STYLE}"
    motion = f"{MOTION_HEAD} {room.motion.capitalize()}, {d.motion}. {MOTION_TAIL}"
    themes = [theme]
    return ScenePrompt(f"{theme.replace('_', '-')}-{room.key}", title, themes, image[:1000], motion[:1000])
