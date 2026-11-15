"""The harvest-type model for image input. NOT CONNECTED YET: the team's model is still being trained.

To plug it in, load it at import time and make predict() return its result. Nothing else needs to change:
the API passes the return value straight through and the UI shows it.

Contract (adjust with the team once the model's inputs are final):
  rgb         HxWx3 uint8 array, the preview of the uploaded image (longest side <= 1024 px)
  metadata    {"location": {"lat", "lon", "bbox", "source"} | None, "date": {"value": "YYYY-MM-DD", "source"} | None, ...}
  parameters  {name: choice} as read from the image by Nemotron (see image_input.PARAMETERS), or None if that step failed
Return {"label": "silage" | "grain", "prob_silage": float 0-1}, or None while no model is connected.
"""
from __future__ import annotations

import numpy as np


def predict(rgb: np.ndarray, metadata: dict, parameters: dict | None) -> dict | None:
    return None  # no model yet: the UI says so instead of showing a made-up label
