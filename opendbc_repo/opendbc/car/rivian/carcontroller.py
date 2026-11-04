import numpy as np
from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.rivian.ext_controller import ExternalController, get_safety_CP  # noqa: F401
from opendbc.car.rivian.riviancan import create_lka_steering, create_longitudinal, create_wheel_touch, create_adas_status, create_acm_status
from opendbc.car.rivian.values import CarControllerParams, RivianFlags

from opendbc.sunnypilot.car.rivian.mads import MadsCarController

GearShifter = structs.CarState.GearShifter

ACM_ENGAGE_TIMEOUT_FRAMES = 50  # the stock ACM takes an ACC press within ~30 ms, after 0.5 s it declined
ACM_MIN_ENGAGE_SPEED = 20 * CV.MPH_TO_MS  # the ACM refuses ACC below this without a lead

class CarController(CarControllerBase, MadsCarController):
  def __init__(self, dbc_names, CP, CP_SP):
    CarControllerBase.__init__(self, dbc_names, CP, CP_SP)
    MadsCarController.__init__(self)
    self.apply_torque_last = 0
    self.packer = CANPacker(dbc_names[Bus.pt])

    self.cancel_frames = 0
    self.long_cmd_counter = 0
    # set by card: False while selfdrived has a NO_ENTRY event (openpilot would refuse to engage)
    self.openpilot_engageable = True
    # why an ACC press did nothing, for card to show: 'blocked' (hidden from the ACM), 'notReady' or 'lowSpeed' (ACM declined)
    self.engage_feedback = None
    self.adas_request_prev = False
    self.acm_watch_frames = 0
    self.acm_not_ready = self.acm_low_speed = False
    self.erc = ExternalController()

  def update(self, CC, CC_SP, CS, now_nanos):
    MadsCarController.update(self, CC, CC_SP, CS)
    actuators = CC.actuators
    can_sends = []

    apply_torque = 0
    steer_max = round(float(np.interp(CS.out.vEgoRaw, CarControllerParams.STEER_MAX_LOOKUP[0],
                                      CarControllerParams.STEER_MAX_LOOKUP[1])))

    self.erc.update(CS, self.mads.lat_active, actuators)
    apply_torque = self.erc.apply_torque_last

    # send steering command, torque is 0 and toi_act_cmd low during a blip
    self.apply_torque_last = apply_torque
    can_sends.append(create_lka_steering(self.packer, self.frame, CS.acm_lka_hba_cmd, apply_torque, CC.enabled, self.erc.toi_act_cmd, self.mads))

    feature_status = (1 if self.erc.torque_active else 2) if self.mads.lat_active else 0
    can_sends.append(create_acm_status(self.packer, self.frame, feature_status))

    if self.frame % 5 == 0 and not (self.CP.flags & RivianFlags.GEN2):
      can_sends.append(create_wheel_touch(self.packer, CS.sccm_wheel_touch, self.mads.lat_active))

    # Stock ACC cancel: openpilot declined (noEntry) or dropped (soft/immediate disable) an engagement while
    # the ACM is in ACC. Spoof a driver cancel press to the ACM so it exits ACC cleanly, instead of sitting in
    # ACC with no enabled long controller behind it and timing out into an ACC fault.
    # The ACM needs to see "available" before it will accept "unavailable"; the VDM takes a few frames to ack.
    interface_status = None
    if CC.cruiseControl.cancel:
      interface_status = 1 if self.cancel_frames < 5 else 0
      self.cancel_frames += 1
    else:
      self.cancel_frames = 0

    # Longitudinal control
    if self.CP.openpilotLongitudinalControl:
      # CC.enabled lags the ACM clearing its feature status by the selfdrived/controlsd round trip
      long_enabled = CC.enabled and CS.out.cruiseState.enabled
      # the panda blocks accel the moment a pedal is pressed, zero it then too instead of when carControl catches up
      pedal = CS.out.gasPressed or CS.out.brakePressed
      accel = float(np.clip(actuators.accel, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX)) if long_enabled and not pedal else 0.0
      self.long_cmd_counter += 1
      can_sends.append(create_longitudinal(self.packer, self.long_cmd_counter, accel, long_enabled))

      # If openpilot would refuse to engage (NO_ENTRY), hide the driver's ACC engage request from the ACM so it never
      # enters ACC. Once the ACM is in ACC with nothing accepting its long request, it latches a fault that only
      # clears when the car sleeps; a spoofed cancel is not enough to prevent it. Cancel requests still pass through.
      block_engage = not self.openpilot_engageable and not CS.out.cruiseState.enabled
      self.update_engage_feedback(CS, block_engage)

      # keep the stock ACM from winding up its unactuated request
      for msg in CS.vdm_adas_status:
        can_sends.append(create_adas_status(self.packer, msg, interface_status, 0 if CC.enabled else None, block_engage))
    else:
      for msg in CS.vdm_adas_status:
        can_sends.append(create_adas_status(self.packer, msg, interface_status))

    new_actuators = actuators.as_builder()
    new_actuators.torque = apply_torque / steer_max
    new_actuators.torqueOutputCan = apply_torque

    self.frame += 1
    return new_actuators, can_sends

  def update_engage_feedback(self, CS, block_engage):
    # VDM_UserAdasRequest 0=idle, 1/2=up, 3/4=down. The stalk is also the gear selector: only a fresh down in Drive is an ACC press
    if CS.vdm_adas_status:
      requests = [m["VDM_UserAdasRequest"] for m in CS.vdm_adas_status]
      acc_press = not self.adas_request_prev and any(r in (3, 4) for r in requests)
      self.adas_request_prev = any(r not in (0, 1) for r in requests)
      if acc_press and block_engage and CS.out.gearShifter == GearShifter.drive:
        self.engage_feedback = 'blocked'
      elif acc_press and not block_engage and not CS.out.cruiseState.enabled:
        self.acm_watch_frames, self.acm_not_ready = 1, False
        self.acm_low_speed = CS.out.vEgo < ACM_MIN_ENGAGE_SPEED

    # a press passed to the ACM that hasn't put it in ACC: the truck only says ACC is unavailable, give the likely reason
    if self.acm_watch_frames:
      self.acm_not_ready |= CS.acm_fault_supervisor_state != 0
      self.acm_watch_frames = 0 if CS.out.cruiseState.enabled else self.acm_watch_frames + 1
      if self.acm_watch_frames > ACM_ENGAGE_TIMEOUT_FRAMES:
        self.acm_watch_frames = 0
        self.engage_feedback = 'notReady' if self.acm_not_ready else 'lowSpeed' if self.acm_low_speed else None
