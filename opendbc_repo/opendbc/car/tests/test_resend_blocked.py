import unittest

from opendbc.car import structs
from opendbc.car.can_definitions import CanData
from opendbc.car.rivian.interface import CarInterface
from opendbc.car.rivian.values import CAR


class _ScriptedCC:
  def __init__(self, frames):
    self.frames = iter(frames)

  def update(self, CC, CC_SP, CS, now_nanos):
    return None, next(self.frames)


def msg(counter, bus=0):
  return CanData(0x110, bytes([counter]), bus)


class TestResendBlocked(unittest.TestCase):
  """CarInterfaceBase.RESEND_BLOCKED: a frame the panda blocked goes out again, byte for byte, on the next apply()."""

  def _run(self, frames, blocked_echoes, enabled=True):
    # frames: what the controller sends each cycle. blocked_echoes: per cycle, frames the panda echoed back as blocked
    CI = CarInterface(CarInterface.get_non_essential_params(CAR.RIVIAN_R1), structs.CarParamsSP())
    CI.RESEND_BLOCKED = enabled
    CI.CC = _ScriptedCC(frames)
    cc, cc_sp = structs.CarControl().as_reader(), structs.CarControlSP()
    out = []
    for echoes in blocked_echoes:
      CI.update([(0, [CanData(m.address, m.dat, m.src + 192) for m in echoes])])
      out.append(CI.apply(cc, cc_sp, 0)[1])
    return out

  def test_resent_ahead_of_newer_frame(self):
    # 100 Hz: c5 blocked -> next cycle sends c5 again before c6
    out = self._run([[msg(4)], [msg(5)], [msg(6)], [msg(7)]], [[], [], [msg(5)], []])
    assert out == [[msg(4)], [msg(5)], [msg(5), msg(6)], [msg(7)]]

  def test_resent_on_off_frame(self):
    # 50 Hz: c5 blocked -> resent on the cycle that sends nothing for it
    out = self._run([[msg(4)], [msg(5)], [], [msg(6)]], [[], [], [msg(5)], []])
    assert out == [[msg(4)], [msg(5)], [msg(5)], [msg(6)]]

  def test_retried_once(self):
    # the retry is blocked too: it is not sent again (a newer frame may already be on the bus)
    out = self._run([[msg(4)], [msg(5)], [msg(6)], [msg(7)]], [[], [], [msg(5)], [msg(5)]])
    assert out == [[msg(4)], [msg(5)], [msg(5), msg(6)], [msg(7)]]

  def test_only_latest_sends(self):
    # an echo that arrives late, after a newer frame already went out, is ignored
    out = self._run([[msg(4)], [msg(5)], [msg(6)], [msg(7)]], [[], [], [], [msg(5)]])
    assert out == [[msg(4)], [msg(5)], [msg(6)], [msg(7)]]

  def test_bus_kept(self):
    out = self._run([[msg(4, bus=2)], [msg(5, bus=2)], []], [[], [msg(4, bus=2)], []])
    assert out[1] == [msg(4, bus=2), msg(5, bus=2)]

  def test_off_by_default(self):
    out = self._run([[msg(4)], [msg(5)], [msg(6)]], [[], [msg(4)], []], enabled=False)
    assert out == [[msg(4)], [msg(5)], [msg(6)]]
