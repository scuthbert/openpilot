"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from openpilot.cereal import log, custom
from opendbc.car import structs

from opendbc.car.chrysler.values import RAM_DT
from opendbc.sunnypilot.car.rivian.values import RivianFlagsSP
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.sunnypilot.mads.helpers import MadsSteeringModeOnBrake, read_steering_mode_param
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

EventName = log.OnroadEvent.EventName
EventNameSP = custom.OnroadEventSP.EventName
ButtonType = structs.CarState.ButtonEvent.Type
GearShifter = structs.CarState.GearShifter


class CarSpecificEventsSP:
  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP):
    self.CP = CP
    self.CP_SP = CP_SP

    self.low_speed_alert = False
    # "Use enhanced Rivian MADS", off MADS is stock
    self.rivian_enhanced_mads = CP.brand == 'rivian' and bool(CP_SP.flags & RivianFlagsSP.ENHANCED_MADS)
    if self.rivian_enhanced_mads:
      params = Params()
      self.pause_on_brake = read_steering_mode_param(CP, CP_SP, params) == MadsSteeringModeOnBrake.PAUSE
      self.min_engage_speed = params.get("MadsMinEngageSpeed", return_default=True) * CV.MPH_TO_MS
      self.up2_held = False
      self.prev_gear = None
      self.gear_disable_frames = 0

  def update(self, CS: structs.CarState, events: Events):
    events_sp = EventsSP()

    if self.CP.brand == 'chrysler':
      if self.CP.carFingerprint in RAM_DT:
        # remove belowSteerSpeed event from CarSpecificEvents as RAM_DT uses a different logic
        if events.has(EventName.belowSteerSpeed):
          events.remove(EventName.belowSteerSpeed)

        # TODO-SP: use if/elif to have the gear shifter condition takes precedence over the speed condition
        # TODO-SP: add 1 m/s hysteresis
        if CS.vEgo >= self.CP.minEnableSpeed:
          self.low_speed_alert = False
        if self.CP.minEnableSpeed >= 14.5 and CS.gearShifter != GearShifter.drive:
          self.low_speed_alert = True
      if self.low_speed_alert:
        events.add(EventName.belowSteerSpeed)

    elif self.CP.brand == 'toyota':
      if self.CP.openpilotLongitudinalControl:
        if CS.cruiseState.standstill and not CS.brakePressed and self.CP_SP.enableGasInterceptor:
          if events.has(EventName.resumeRequired):
            events.remove(EventName.resumeRequired)

    elif self.rivian_enhanced_mads:
      self._update_rivian_enhanced_mads(CS, events, events_sp)

    return events_sp

  def _update_rivian_enhanced_mads(self, CS: structs.CarState, events: Events, events_sp: EventsSP) -> None:
    up2 = [be.pressed for be in CS.buttonEvents if be.type == ButtonType.altButton2]
    self.up2_held = up2[-1] if up2 else self.up2_held

    # Park and Reverse switch MADS off on two frames: on the first wrongGear/reverseGear only pauses it
    if CS.gearShifter in (GearShifter.park, GearShifter.reverse):
      self.gear_disable_frames = 2 if CS.gearShifter != self.prev_gear else self.gear_disable_frames
    else:
      self.gear_disable_frames = 0
    self.prev_gear = CS.gearShifter

    # so does a full stalk pull (UP_2), and a held UP_2 or Park keeps cruise from engaging MADS
    full_disable = any(up2) or self.gear_disable_frames > 0
    self.gear_disable_frames = max(self.gear_disable_frames - 1, 0)
    if full_disable:
      events_sp.add(EventNameSP.lkasDisable)
    if self.up2_held or CS.gearShifter == GearShifter.park:
      events.remove(EventName.pcmEnable)

    # Pause mode holds the pause for the whole brake press, also at standstill. Not on a full
    # disable frame, there the state machine would turn lkasDisable into a pause
    if CS.brakePressed and self.pause_on_brake and not full_disable:
      events_sp.add(EventNameSP.silentLkasDisable)

    # below the minimum speed the stalk can't (re-)engage MADS, engaging cruise always can
    engaging = events.has(EventName.pcmEnable) or events.has(EventName.buttonEnable)
    if self.min_engage_speed > 0 and CS.vEgo < self.min_engage_speed and not engaging:
      events_sp.add(EventNameSP.belowMadsMinEngageSpeed)
