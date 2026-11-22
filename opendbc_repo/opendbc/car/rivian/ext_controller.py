import math
import numpy as np

from opendbc.car.lateral import apply_driver_steer_torque_limits, common_fault_avoidance
from opendbc.car.rivian.values import CarControllerParams as CCP

# light-torsion presence, bridges capacitive dropouts while hands slide on the wheel
PRESENCE_TORQUE_THRESHOLD = 1.5
PRESENCE_MIN_FRAMES = 30      # frames above threshold to latch (0.3s)
PRESENCE_HOLD_FRAMES = 100    # latch hold (1.0s)
HANDS_OFF_EXIT_FRAMES = 75    # diagnostic only on this build, no angle mode to hand back to

# blip the TOI request bit at high angle so the EPAS does not latch ToiFlt
TOI_MAX_ANGLE_DEG = 90
TOI_MAX_ANGLE_FRAMES = 89        # frames held high before a blip (~0.9s)
TOI_BLIP_FRAMES = 2              # frames to release ACM_lkaActToi


def get_safety_CP():
  # the panda safety still checks 0x110 angle limits, its tests build the vehicle model from this
  from opendbc.car.rivian.interface import CarInterface
  return CarInterface.get_non_essential_params("RIVIAN_R1")


class ExternalController:
  def __init__(self):
    # hands-on (diagnostic only on this build, kept for telemetry/future use)
    self.wheel_touch_cnt = 0
    self.torsion_cnt = 0
    self.hands_on = False
    self.presence_cnt = 0
    self.presence_hold = 0
    self.hands_off_frames = 0

    # torque is the only actuation channel on this build: no dual intercept extreme kit / angle actuator
    self.torque_active = False
    self.torque_active_frames = 0

    # cooperative torque
    self.apply_torque_last = 0
    self.toi_angle_limit_counter = 0
    self.toi_act_cmd = False     # sent into ACM_lkaActToi, low for 2 frames during a blip

  def update(self, CS, lat_active: bool, actuators):
    self._update_hands_on(CS)
    self._update_torque_active(lat_active)
    self._update_torque(CS, actuators)

  def _update_wheel_touched(self, wheel_touched, wheel_touched_min_count):
    self.wheel_touch_cnt += 1 if wheel_touched else -1
    self.wheel_touch_cnt = int(np.clip(self.wheel_touch_cnt, 0, wheel_touched_min_count * 2 + 1))
    return self.wheel_touch_cnt > wheel_touched_min_count

  def _update_torsion(self, torque, torque_threshold, torsion_min_count):
    abs_torque = abs(torque)
    self.torsion_cnt += max(1, math.ceil(abs_torque / torque_threshold)) if abs_torque > torque_threshold else -1
    self.torsion_cnt = int(np.clip(self.torsion_cnt, 0, torsion_min_count * 2 + 1))
    return self.torsion_cnt > torsion_min_count

  def _update_torsion_presence(self, torque):
    self.presence_cnt = self.presence_cnt + 1 if abs(torque) > PRESENCE_TORQUE_THRESHOLD else 0
    if self.presence_cnt >= PRESENCE_MIN_FRAMES:
      self.presence_hold = PRESENCE_HOLD_FRAMES
    elif self.presence_hold > 0:
      self.presence_hold -= 1
    return self.presence_hold > 0

  def _update_hands_on(self, CS):
    # NOTE: diagnostic only on this build, nothing here gates steering behavior anymore
    driver_torque = CS.out.steeringTorque
    calibration = CS.sccm_wheel_touch["SCCM_WheelTouch_Calibration"]
    wheel_touch = self._update_wheel_touched(CS.sccm_wheel_touch["SCCM_WheelTouch_CapacitiveValue"] > calibration * 0.9, 25)
    torsion = self._update_torsion(driver_torque, 3.0, 9)
    presence = self._update_torsion_presence(driver_torque)
    self.hands_on = wheel_touch or torsion or CS.hands_on_level > 1
    self.hands_off_frames = 0 if self.hands_on or presence else self.hands_off_frames + 1

  def _update_torque_active(self, lat_active: bool):
    self.torque_active_frames = self.torque_active_frames + 1 if self.torque_active else 0
    # torque is the only channel on this build: active whenever lat_active, no angle mode to hand back to
    self.torque_active = lat_active

  def _update_torque(self, CS, actuators):
    if not self.torque_active:
      self.apply_torque_last = 0
      self.toi_act_cmd = False
      self.toi_angle_limit_counter = 0
      return

    v_ego = CS.out.vEgoRaw
    steer_max = round(float(np.interp(v_ego, CCP.STEER_MAX_LOOKUP[0], CCP.STEER_MAX_LOOKUP[1])))
    new_torque = int(round(float(actuators.torque) * steer_max))
    apply_torque = apply_driver_steer_torque_limits(new_torque, self.apply_torque_last,
                                                    CS.out.steeringTorque, CCP, steer_max)

    # blip the TOI request when held at high angle, torque drops to 0 so the rate limiter ramps back from 0
    self.toi_angle_limit_counter, toi_act = common_fault_avoidance(
      abs(CS.out.steeringAngleDeg) >= TOI_MAX_ANGLE_DEG, self.torque_active,
      self.toi_angle_limit_counter, TOI_MAX_ANGLE_FRAMES, TOI_BLIP_FRAMES)
    if not toi_act:
      apply_torque = 0

    self.toi_act_cmd = toi_act
    self.apply_torque_last = apply_torque
