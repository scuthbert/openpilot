"""
rx-wb branding: the EVPILOT logos replace the sunnypilot ones on the device screens.
"""
import math
import pyray as rl

from openpilot.system.ui.lib.application import gui_app


def brand_asset(name: str) -> str:
  return f"../../sunnypilot/selfdrive/assets/evpilot/{name}"


def brand_texture(name: str, max_width: float = math.inf, max_height: float = math.inf) -> rl.Texture:
  # scaled to fit the box, keeping the image's aspect ratio
  image = gui_app.texture(brand_asset(name))
  scale = min(max_width / image.width, max_height / image.height)
  return gui_app.texture(brand_asset(name), round(image.width * scale), round(image.height * scale))


class BrandLogo:
  """The wordmark in place of the brand name label, font_size keeps the label's line height."""

  def __init__(self, font_size: int, max_width: int, max_height: int):
    self.font_size = font_size
    self._tex = brand_texture("wordmark.png", max_width, max_height)
    self._pos = (0., 0.)

  def set_position(self, x: float, y: float) -> None:
    self._pos = (x, y)

  def render(self) -> None:
    # centred on the text line, which has its font's top padding
    x, y = self._pos
    y += (self.font_size - self._tex.height) / 2 + self.font_size * 0.12
    rl.draw_texture_ex(self._tex, rl.Vector2(x + 2, y), 0.0, 1.0, rl.WHITE)
