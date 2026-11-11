"""Calibrations for the calibration menu:
    motion   px/mm per axis, camera angle and skew from machine moves (both cameras)
    xyerror  repeatability / backlash test; error map (grid plate) with a correction table
    board    fiducial transform with scale (2 marks) or full affine (3+ marks)
    settle   camera settling: grab until the image stops moving (instead of a fixed delay)
    feeder   feeder pick-location calibration from the tape's sprocket holes
"""
from .board import BoardTransform
from .feeder import FeederCal, calibrate_feeder, calibrate_feeders, find_holes
from .settle import SettleResult, grab_settled, settle_profile
from .motion import (CalibrationError, CalResult, blob_centroid, calibrate, default_pattern, describe,
                     fit_matrix, make_grab, shift_blob, shift_phase)
from .xyerror import ErrorMap, RepeatResult, XYCorrection, make_measure, mark_finder, measure_grid, repeatability

__all__ = ["FeederCal", "SettleResult", "calibrate_feeder", "calibrate_feeders", "find_holes", "grab_settled",
           "settle_profile",
           "BoardTransform", "CalibrationError", "CalResult", "ErrorMap", "RepeatResult", "XYCorrection", "blob_centroid",
           "calibrate", "default_pattern", "describe", "fit_matrix", "make_grab", "make_measure",
           "mark_finder", "measure_grid", "repeatability", "shift_blob", "shift_phase"]
