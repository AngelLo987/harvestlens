"""Image input: read an uploaded field image, its location and its acquisition date, then have Nemotron
describe it with a fixed set of parameters for the harvest-type model.

Flow:  image file -> read_image (pixels + metadata) -> describe (Nemotron) -> image_model.predict

Location and date are taken from the file when it carries them (GeoTIFF georeferencing, EXIF GPS,
TIFF/EXIF timestamps, or a date in the file name) and otherwise from values typed in by the user.
Each one records where it came from, so a guessed or typed value is never mistaken for file metadata.
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import re

import httpx
import numpy as np
from PIL import ExifTags, Image, UnidentifiedImageError

from . import copilot

MAX_BYTES = 40 * 1024 * 1024
PREVIEW_PX = 1024  # longest side of the RGB preview sent to Nemotron and shown in the UI

# ---------------------------------------------------------------------------------------------
# PARAMETERS: what Nemotron reports about the image. PLACEHOLDERS until the team fixes the model's
# inputs. To change them, edit this list only: the prompt, the reply validation, the API response
# and the UI all follow from it. Every parameter is a fixed choice and "cannot_tell" is always allowed,
# so Nemotron reports what it can see and never supplies a measurement.
# ---------------------------------------------------------------------------------------------
PARAMETERS: list[dict] = [
    {"name": "canopy_state", "label": "Canopy state",
     "question": "What does the crop canopy in the field look like?",
     "values": ["green", "yellowing", "dry_brown", "removed"]},
    {"name": "harvest_progress", "label": "Harvest progress",
     "question": "How much of the field has been harvested?",
     "values": ["not_started", "in_progress", "complete"]},
    {"name": "residue", "label": "Residue after harvest",
     "question": "If any part is harvested, what is left on the ground there?",
     "values": ["bare_soil", "stubble_or_stover", "not_harvested"]},
]
CANNOT_TELL = "cannot_tell"

# Cornea covers the United States only. These boxes are coarse (west, south, east, north): they also
# take in strips of Canada and Mexico near the border, so this is a sanity check, not a border test.
US_BOXES = {
    "contiguous US": (-125.0, 24.4, -66.9, 49.4),
    "Alaska": (-180.0, 51.2, -129.9, 71.5),
    "Hawaii": (-160.3, 18.9, -154.8, 22.3),
    "Puerto Rico": (-67.3, 17.8, -65.2, 18.6),
}
OUTSIDE_US = "This image is located outside the United States. Cornea covers US fields only, so it was not analyzed."


def in_us(lat: float, lon: float) -> bool:
    return any(w <= lon <= e and s <= lat <= n for w, s, e, n in US_BOXES.values())


class ImageError(Exception):
    """A problem the user can act on (bad file, Nemotron failure). The message is shown as is."""


# ------------------------------------------------------------------------------- reading the file

def _filename_date(name: str) -> str | None:
    for m in re.finditer(r"(?<!\d)(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)", name or ""):
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            continue
    return None


def _tag_date(value) -> str | None:
    """TIFF and EXIF timestamps look like '2025:09:14 10:31:02'."""
    m = re.match(r"\s*(\d{4})[:\-](\d{2})[:\-](\d{2})", str(value or ""))
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat() if m else None
    except ValueError:
        return None


def _stretch(rgb: np.ndarray) -> np.ndarray:
    """Scale numeric bands to 8-bit with one 2-98 percentile stretch shared by all three bands
    (satellite bands are rarely 0-255). A shared stretch keeps the colors true; stretching each
    band on its own shifts hues, e.g. brown stubble turns pink."""
    vals = rgb.astype(float)
    ok = np.isfinite(vals)
    if not ok.any():
        return np.zeros(rgb.shape, np.uint8)
    lo, hi = np.percentile(vals[ok], [2, 98])
    if hi <= lo:
        return np.zeros(rgb.shape, np.uint8)
    return np.clip((np.nan_to_num(vals, nan=lo) - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)


def _read_geotiff(data: bytes) -> tuple[Image.Image, dict]:
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.warp import transform_bounds

    with MemoryFile(data) as mem, mem.open() as src:
        scale = max(1, int(np.ceil(max(src.width, src.height) / PREVIEW_PX)))
        shape = (max(1, src.height // scale), max(1, src.width // scale))
        # ASSUMPTION: bands 1-3 are red, green, blue. Adjust here if the team's files order bands differently.
        idx = [1, 2, 3] if src.count >= 3 else [1, 1, 1]
        arr = np.moveaxis(src.read(idx, out_shape=(3, *shape)), 0, -1)
        if src.nodata is not None:
            arr = np.where(arr == src.nodata, np.nan, arr.astype(float))
        img = Image.fromarray(arr if arr.dtype == np.uint8 else _stretch(arr), "RGB")
        loc = None
        if src.crs and not src.transform.is_identity:
            w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
            loc = {"lat": round((s + n) / 2, 6), "lon": round((w + e) / 2, 6),
                   "bbox": [round(v, 6) for v in (w, s, e, n)], "source": f"GeoTIFF georeferencing ({src.crs.to_string()})"}
        date = _tag_date(src.tags().get("TIFFTAG_DATETIME"))
        return img, {"format": "GeoTIFF", "width": src.width, "height": src.height, "bands": src.count,
                     "location": loc, "date": {"value": date, "source": "TIFF timestamp"} if date else None}


def _dms(v, ref) -> float:
    d, m, s = (float(x) for x in v)
    return round((d + m / 60 + s / 3600) * (-1 if ref in ("S", "W") else 1), 6)


def _read_photo(data: bytes) -> tuple[Image.Image, dict]:
    img = Image.open(io.BytesIO(data))
    img.load()
    exif = img.getexif()
    loc = date = None
    try:
        gps = {ExifTags.GPSTAGS.get(k, k): v for k, v in exif.get_ifd(ExifTags.IFD.GPSInfo).items()}
        if "GPSLatitude" in gps and "GPSLongitude" in gps:
            loc = {"lat": _dms(gps["GPSLatitude"], gps.get("GPSLatitudeRef")),
                   "lon": _dms(gps["GPSLongitude"], gps.get("GPSLongitudeRef")), "bbox": None, "source": "EXIF GPS"}
        taken = exif.get_ifd(ExifTags.IFD.Exif).get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime)
        if _tag_date(taken):
            date = {"value": _tag_date(taken), "source": "EXIF timestamp"}
    except (TypeError, ValueError, ZeroDivisionError, KeyError):
        pass  # malformed EXIF: treat as absent
    meta = {"format": img.format or "image", "width": img.width, "height": img.height, "bands": len(img.getbands()),
            "location": loc, "date": date}
    return img.convert("RGB"), meta


def read_image(data: bytes, filename: str, lat: float | None = None, lon: float | None = None, date: str | None = None) -> dict:
    """Decode the file and resolve its location and date. Typed-in values override what the file says."""
    if len(data) > MAX_BYTES:
        raise ImageError(f"File is larger than {MAX_BYTES // (1024 * 1024)} MB")
    is_tiff = data[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
    try:
        img, meta = _read_geotiff(data) if is_tiff else _read_photo(data)
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ImageError("Could not read this file as an image (JPEG, PNG and TIFF/GeoTIFF are supported)") from e
    except Exception as e:  # rasterio raises its own error types for unreadable TIFFs
        if type(e).__module__.startswith("rasterio"):
            raise ImageError("Could not read this TIFF file") from e
        raise

    if meta["date"] is None and _filename_date(filename):
        meta["date"] = {"value": _filename_date(filename), "source": "date in file name"}
    if lat is not None or lon is not None:
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ImageError("Give both latitude (-90 to 90) and longitude (-180 to 180)")
        meta["location"] = {"lat": lat, "lon": lon, "bbox": None, "source": "entered by hand"}
    if date:
        try:
            meta["date"] = {"value": dt.date.fromisoformat(date).isoformat(), "source": "entered by hand"}
        except ValueError as e:
            raise ImageError("Date must look like 2025-09-14") from e
    meta["missing"] = [k for k in ("location", "date") if meta[k] is None]
    meta["in_us"] = in_us(meta["location"]["lat"], meta["location"]["lon"]) if meta["location"] else None  # None: location unknown
    meta["filename"] = filename

    img.thumbnail((PREVIEW_PX, PREVIEW_PX))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return {"metadata": meta, "preview_jpeg": buf.getvalue(), "rgb": np.asarray(img)}


# ------------------------------------------------------------------------------ Nemotron parameters

PROMPT = """You are the image-reading step of Cornea, which decides whether a corn field is being harvested for silage or for grain.
You are given one overhead image of a field in the United States, with its location and date when known. Report what is visible, using only the choices below.

{questions}

Rules:
- Answer from the image only. Use the location and date as context, not as a reason to assume an answer.
- If the image does not show it clearly, answer "{cannot_tell}". Do not guess.
- Do not report numbers or measurements, and do not say whether the field is silage or grain. A separate model decides that.

Respond with JSON only, no prose:
{{{fields}, "notes": "one short sentence on what you see, or what stops you seeing it"}}"""


def _prompt() -> str:
    qs = "\n".join(f'- {p["name"]}: {p["question"]} One of {json.dumps(p["values"] + [CANNOT_TELL])}.' for p in PARAMETERS)
    return PROMPT.format(questions=qs, cannot_tell=CANNOT_TELL, fields=", ".join(f'"{p["name"]}": "..."' for p in PARAMETERS))


def describe(preview_jpeg: bytes, meta: dict) -> dict:
    """Ask Nemotron for the PARAMETERS. Raises ImageError unless every parameter comes back as an allowed choice."""
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise ImageError("Nemotron is offline: NVIDIA_API_KEY is not set on the server")
    loc, date = meta["location"], meta["date"]
    context = (f"Location: {loc['lat']}, {loc['lon']}" if loc else "Location: unknown") + \
              (f". Date: {date['value']}." if date else ". Date: unknown.")
    image_url = "data:image/jpeg;base64," + base64.b64encode(preview_jpeg).decode()
    r = None
    for attempt in (1, 2):  # NVIDIA's hosted API drops the occasional image request; retry once
        try:
            r = httpx.post(copilot.NIM_URL, timeout=httpx.Timeout(45, connect=10), headers={"Authorization": f"Bearer {key}"}, json={
                "model": copilot.NIM_MODEL, "temperature": 0.1, "max_tokens": 400,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": _prompt()},
                             {"role": "user", "content": [{"type": "text", "text": context},
                                                          {"type": "image_url", "image_url": {"url": image_url}}]}]})
            break
        except httpx.HTTPError as e:
            if attempt == 2:
                raise ImageError("Could not reach the NVIDIA API, or the request timed out (tried twice)") from e
    if r.status_code != 200:
        raise ImageError(f"NVIDIA API returned HTTP {r.status_code}. Check the key, model access and that the model accepts images.")
    text = re.sub(r"<think>.*?</think>", "", r.json()["choices"][0]["message"].get("content") or "", flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        reply = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        reply = None
    if not isinstance(reply, dict):
        raise ImageError("Nemotron did not return a readable answer for this image")
    bad = [p["name"] for p in PARAMETERS if reply.get(p["name"]) not in p["values"] + [CANNOT_TELL]]
    if bad:
        raise ImageError(f"Nemotron returned a value outside the allowed choices for: {', '.join(bad)}")
    return {"values": {p["name"]: reply[p["name"]] for p in PARAMETERS},
            "notes": str(reply.get("notes") or "").strip()[:300], "model": copilot.NIM_MODEL}


def parameter_spec() -> list[dict]:
    return [{"name": p["name"], "label": p["label"], "values": p["values"] + [CANNOT_TELL]} for p in PARAMETERS]
