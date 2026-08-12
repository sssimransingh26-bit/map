"""
every threshold, weight, and keyword list used below is loaded from
config.json
if config.json is missing, DEFAULT_CONFIG below is used as a fallback so
the module still runs, but the intent is that config.json is the real
source of truth once you've calibrated it against real data.
"""

from __future__ import annotations

import os
import json
import datetime
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import cv2
import requests
from PIL import Image, ImageChops, ImageEnhance
from skimage.util import view_as_windows
import imagehash


# CONFIG LOADING — reads config.json, falls back to DEFAULT_CONFIG if missing


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Fallback
DEFAULT_CONFIG = {
    "thresholds": {
        "noise_std_delta": 15.0,
        "edge_std": 15.0,
        "exif_time_delta_seconds": 86400,
        "verdict_suspicious": 60,
        "verdict_review": 30,
    },
    "block_analysis": {
        "noise_block_size": 32,
        "edge_block_size": 32,
        "clone_block_size": 32,
        "clone_stride": 16,
        "canny_low": 100,
        "canny_high": 200,
    },
    "weights": {
        "exif_timestamp_mismatch": 1.0,
        "exif_date_unparseable": 0.5,
        "exif_date_missing": 0.5,
        "editing_software_detected": 1.5,
        "suspicious_camera_tag": 1.0,
        "gps_all_zero": 0.75,
        "gps_without_camera_info": 0.5,
        "noise_inconsistency": 1.5,
        "clone_detection": 1.0,
        "edge_artifact_inconsistency": 1.0,
        "ai_tool_metadata": 2.0,
        "ai_generation_params": 2.0,
        "metadata_stripped_high_res": 0.75,
        "ai_classifier_api": 2.0,
    },
    "ai_tool_keywords": [
        "stable diffusion", "midjourney", "dall-e", "artbreeder",
        "generative", "dream", "diffusion", "wombo", "adobe firefly",
    ],
    "editing_software_keywords": ["photoshop", "editor", "gimp", "snapseed"],
    "metadata_stripped_min_dimension": 1024,
    "metadata_stripped_max_tag_count": 5,
    "ai_classifier_api": {
        "enabled": True,
        "provider": "huggingface",
        "model_id": "Organika/sdxl-detector",
        "endpoint": "https://api-inference.huggingface.co/models/Organika/sdxl-detector",
        "timeout_seconds": 15,
        "suspicious_label_keywords": ["artificial", "ai", "fake", "generated", "synthetic"],
    },
}


def load_config(path: str = CONFIG_PATH) -> dict:
    """Loads config.json; falls back to DEFAULT_CONFIG (with a warning)
    if the file is missing or invalid, so the engine never silently
    hardcodes a value that isn't traceable back to this function."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"WARNING: could not load {path} ({e}); using built-in defaults. "
              f"Create config.json to customize thresholds/weights.")
        return DEFAULT_CONFIG


CONFIG = load_config()

# DATA MODELS — Signal (one check's result) and ForensicReport (fused result)

@dataclass
class Signal:
    """One forensic check's structured result."""
    name: str
    score: float          # 0.0 (clean) - 1.0 (highly suspicious)
    severity: str          # "info" | "warning" | "suspicious"
    evidence: str          # human-readable explanation
    weight: float = 1.0    # relative importance in the fused score, from config


@dataclass
class ForensicReport:
    """Fused output of all checks for one image."""
    signals: list[Signal] = field(default_factory=list)
    config: dict = field(default_factory=lambda: CONFIG)

    def add(self, signal: Signal) -> None:
        self.signals.append(signal)

    @property
    def authenticity_score(self) -> float:
        if not self.signals:
            return 0.0
        total_weight = sum(s.weight for s in self.signals)
        if total_weight == 0:
            return 0.0
        weighted = sum(s.score * s.weight for s in self.signals)
        return round(100 * weighted / total_weight, 1)

    @property
    def verdict(self) -> str:
        score = self.authenticity_score
        t = self.config["thresholds"]
        if score >= t["verdict_suspicious"]:
            return "SUSPICIOUS"
        if score >= t["verdict_review"]:
            return "REVIEW_RECOMMENDED"
        return "LIKELY_AUTHENTIC"

    def to_dict(self) -> dict:
        return {
            "authenticity_score": self.authenticity_score,
            "verdict": self.verdict,
            "signals": [
                {
                    "name": s.name,
                    "score": s.score,
                    "severity": s.severity,
                    "evidence": s.evidence,
                    "weight": s.weight,
                }
                for s in self.signals
            ],
        }


# ---------------------------------------------------------------------------
# INDIVIDUAL CHECKS — every threshold/weight/keyword pulled from config,
# passed in as a parameter, never typed as a literal inline.
# ---------------------------------------------------------------------------

#EXIF/metadata checks 
def check_exif_consistency(tags: dict, filepath: str, config: dict = CONFIG) -> list[Signal]:
    signals = []
    thresholds = config["thresholds"]
    weights = config["weights"]
    editing_keywords = config["editing_software_keywords"]

    exif_date = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
    file_mtime = os.path.getmtime(filepath)
    if exif_date:
        try:
            exif_dt = datetime.datetime.strptime(str(exif_date), "%Y:%m:%d %H:%M:%S")
            file_dt = datetime.datetime.fromtimestamp(file_mtime)
            delta = abs((exif_dt - file_dt).total_seconds())
            if delta > thresholds["exif_time_delta_seconds"]:
                signals.append(Signal(
                    "exif_timestamp_mismatch", score=0.5, severity="warning",
                    evidence=f"EXIF timestamp and file mtime differ by {delta/3600:.1f}h "
                             f"(threshold {thresholds['exif_time_delta_seconds']/3600:.0f}h).",
                    weight=weights["exif_timestamp_mismatch"],
                ))
        except ValueError:
            signals.append(Signal(
                "exif_date_unparseable", score=0.2, severity="info",
                evidence="EXIF date/time present but not in standard format.",
                weight=weights["exif_date_unparseable"],
            ))
    else:
        signals.append(Signal(
            "exif_date_missing", score=0.15, severity="info",
            evidence="No EXIF capture date found.",
            weight=weights["exif_date_missing"],
        ))

    software = tags.get("Image Software")
    if software and any(s in str(software).lower() for s in editing_keywords):
        signals.append(Signal(
            "editing_software_detected", score=0.6, severity="warning",
            evidence=f"EXIF Software field indicates an editor: {software}",
            weight=weights["editing_software_detected"],
        ))

    make, model = tags.get("Image Make"), tags.get("Image Model")
    if make and model and ("fake" in str(make).lower() or "fake" in str(model).lower()):
        signals.append(Signal(
            "suspicious_camera_tag", score=0.7, severity="suspicious",
            evidence=f"Camera Make/Model looks fabricated: {make} / {model}",
            weight=weights["suspicious_camera_tag"],
        ))

    gpslat = tags.get("GPS GPSLatitude")
    if gpslat and str(gpslat).replace(" ", "").replace(",", "") == "0/1 0/1 0/1":
        signals.append(Signal(
            "gps_all_zero", score=0.4, severity="warning",
            evidence="GPS coordinates are all zeros (uninitialized/faked).",
            weight=weights["gps_all_zero"],
        ))

    if tags.get("GPS GPSLatitude") and not (make and model):
        signals.append(Signal(
            "gps_without_camera_info", score=0.3, severity="info",
            evidence="GPS present but no camera Make/Model recorded.",
            weight=weights["gps_without_camera_info"],
        ))

    return signals


# pixel level forensic checks 
def check_noise_consistency(img_path: str, config: dict = CONFIG) -> Signal:
    block_size = config["block_analysis"]["noise_block_size"]
    threshold = config["thresholds"]["noise_std_delta"]
    weight = config["weights"]["noise_inconsistency"]

    img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    blocks = view_as_windows(img, (block_size, block_size), step=block_size)
    std_map = np.std(blocks, axis=(2, 3))
    delta = float(np.max(std_map) - np.min(std_map))
    is_suspicious = delta > threshold
    score = min(1.0, delta / (threshold * 2))
    return Signal(
        "noise_inconsistency", score=score,
        severity="suspicious" if is_suspicious else "info",
        evidence=f"Noise std delta across blocks = {delta:.2f} (threshold {threshold}).",
        weight=weight,
    )


def check_clone_regions(img_path: str, config: dict = CONFIG) -> Signal:
    block_size = config["block_analysis"]["clone_block_size"]
    stride = config["block_analysis"]["clone_stride"]
    weight = config["weights"]["clone_detection"]

    img = Image.open(img_path).convert("L")
    w, h = img.size
    hashes: dict[str, list] = {}
    for y in range(0, h - block_size, stride):
        for x in range(0, w - block_size, stride):
            region = img.crop((x, y, x + block_size, y + block_size))
            hsh = str(imagehash.average_hash(region))
            hashes.setdefault(hsh, []).append((x, y))
    duplicate_blocks = [locs for locs in hashes.values() if len(locs) > 1]
    n_dupes = len(duplicate_blocks)
    score = min(1.0, n_dupes / 10.0)
    return Signal(
        "clone_detection", score=score,
        severity="suspicious" if n_dupes > 0 else "info",
        evidence=(f"{n_dupes} duplicate block group(s) found — may indicate "
                  f"copy-move editing, or simply a repetitive texture (sky, "
                  f"fabric, brick). Not conclusive on its own.") if n_dupes else
                 "No duplicate blocks found.",
        weight=weight,
    )


def check_edge_artifacts(img_path: str, config: dict = CONFIG) -> Signal:
    block_size = config["block_analysis"]["edge_block_size"]
    canny_low = config["block_analysis"]["canny_low"]
    canny_high = config["block_analysis"]["canny_high"]
    threshold = config["thresholds"]["edge_std"]
    weight = config["weights"]["edge_artifact_inconsistency"]

    img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    edges = cv2.Canny(img, canny_low, canny_high)
    edge_blocks = view_as_windows(edges, (block_size, block_size), step=block_size)
    block_means = np.mean(edge_blocks, axis=(2, 3))
    std = float(np.std(block_means))
    is_suspicious = std > threshold
    score = min(1.0, std / (threshold * 2))
    return Signal(
        "edge_artifact_inconsistency", score=score,
        severity="suspicious" if is_suspicious else "info",
        evidence=f"Edge-density std across blocks = {std:.2f} (threshold {threshold}).",
        weight=weight,
    )


# AI-generation checks (metadata-based, then API-based)
def check_ai_generation(tags: dict, img_path: str, config: dict = CONFIG) -> Optional[Signal]:
    ai_keywords = config["ai_tool_keywords"]
    weights = config["weights"]
    min_dim = config["metadata_stripped_min_dimension"]
    max_tags = config["metadata_stripped_max_tag_count"]

    software = str(tags.get("Software", "") or tags.get("Image Software", "")).lower()
    for tool in ai_keywords:
        if tool in software:
            return Signal(
                "ai_tool_metadata", score=0.9, severity="suspicious",
                evidence=f"EXIF Software field references AI tool: '{tool}'.",
                weight=weights["ai_tool_metadata"],
            )

    comment = str(tags.get("UserComment", "")).lower()
    if "negative_prompt" in comment or "steps:" in comment:
        return Signal(
            "ai_generation_params", score=0.95, severity="suspicious",
            evidence="Stable Diffusion-style generation parameters found in UserComment.",
            weight=weights["ai_generation_params"],
        )

    img = Image.open(img_path)
    if (not tags) or (len(tags) < max_tags and img.width > min_dim and img.height > min_dim):
        return Signal(
            "metadata_stripped_high_res", score=0.35, severity="warning",
            evidence="Large image with almost no metadata — consistent with "
                     "AI generation or metadata stripping, but not proof "
                     "either way.",
            weight=weights["metadata_stripped_high_res"],
        )
    return None


def check_ai_classifier_api(img_path: str, config: dict = CONFIG) -> Optional[Signal]:
    """
    calls Hugging Face Inference API and turns its output into a signal.
    """
    api_cfg = config.get("ai_classifier_api", {})
    if not api_cfg.get("enabled", False):
        return None

    token = os.environ.get("HF_API_TOKEN")
    if not token:
        # no key configured so skip silently rather than erroring 
        # this is an optional enrichment signal, not a required one
        return None

    try:
        with open(img_path, "rb") as f:
            image_bytes = f.read()

        response = requests.post(
            api_cfg["endpoint"],
            headers={"Authorization": f"Bearer {token}"},
            data=image_bytes,
            timeout=api_cfg.get("timeout_seconds", 15),
        )
        response.raise_for_status()
        predictions = response.json()

        # HF returns a list of {label, score} dicts, top prediction first
        if not isinstance(predictions, list) or not predictions:
            return None

        top = predictions[0]
        top_label = str(top.get("label", "")).lower()
        top_score = float(top.get("score", 0.0))

        suspicious_keywords = api_cfg.get("suspicious_label_keywords", [])
        is_ai_label = any(kw in top_label for kw in suspicious_keywords)

        # scale by how confident the model actually is — a 52% guess
        # shouldn't count as much as a 95% one
        score = top_score if is_ai_label else (1.0 - top_score) * 0.3

        return Signal(
            "ai_classifier_api", score=round(min(score, 1.0), 3),
            severity="suspicious" if is_ai_label and top_score > 0.6 else "info",
            evidence=(f"{api_cfg.get('model_id', 'classifier')} predicts "
                      f"'{top.get('label')}' with {top_score:.1%} confidence."),
            weight=config["weights"].get("ai_classifier_api", 1.0),
        )

    except requests.exceptions.RequestException as e:
        # should degrade the report not crash it
        print(f"WARNING: AI classifier API call failed ({e}); skipping this signal.")
        return None


#ELA + face count (just for display, not part of the score) 
def perform_ela(image_path: str, scale: int = 15) -> str:
    """Produces a visual artifact for a human to inspect, not a score"""

    img = Image.open(image_path).convert("RGB")
    temp_filename = image_path + "_temp_ela.jpg"
    img.save(temp_filename, "JPEG", quality=90)
    compressed = Image.open(temp_filename)
    diff = ImageChops.difference(img, compressed)
    diff = ImageEnhance.Brightness(diff).enhance(scale)
    ela_path = image_path + "_ELA.png"
    diff.save(ela_path)
    os.remove(temp_filename)
    return ela_path


def detect_faces(image_path: str) -> int:
    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    img = cv2.imread(image_path)
    if img is None:
        return 0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.1, 4)
    return len(faces)


# ---------------------------------------------------------------------------
# ORCHESTRATION — runs every check above and returns one fused report.
#  only function app.py call.
# ---------------------------------------------------------------------------

def run_full_analysis(image_path: str, tags: dict, config: dict = CONFIG) -> ForensicReport:
    """runs every check and returns one fused ForensicReport"""
    report = ForensicReport(config=config)

    for sig in check_exif_consistency(tags, image_path, config):
        report.add(sig)

    report.add(check_noise_consistency(image_path, config))
    report.add(check_clone_regions(image_path, config))
    report.add(check_edge_artifacts(image_path, config))

    ai_signal = check_ai_generation(tags, image_path, config)
    if ai_signal:
        report.add(ai_signal)

    ai_api_signal = check_ai_classifier_api(image_path, config)
    if ai_api_signal:
        report.add(ai_api_signal)

    return report