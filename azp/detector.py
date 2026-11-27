"""Optional lightweight ONNX YOLO (YOLOv8 / YOLO11) 3D object detector plugin & dataset pipeline.

Zero-PyTorch runtime design:
- Runs exported Ultralytics YOLOv8/YOLO11 ``.onnx`` models directly via ``onnxruntime``
  (CUDA / DirectML / CPUExecutionProvider), achieving ~5-15ms/frame on CPU and ~2-4ms on GPU.
- Gracefully falls back to calibrated classical CV + Part-1 template matching via
  ``HybridDetector`` when ``models/azp_yolo.onnx`` has not yet been trained/placed.
- Includes cold-start dataset scaffolding (``init_yolo_dataset`` with automatic UID masking)
  and automatic pre-labeling (``prelabel_yolo_dataset``) from existing ``snaps/`` screenshots.
"""
from __future__ import annotations

import ast
import json
import os
import random
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image
from scipy import ndimage

from . import ocr_tpl, vision

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

try:
    import onnxruntime as ort  # type: ignore
except Exception:  # pragma: no cover
    ort = None  # type: ignore


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODEL_PATH = os.path.join(ROOT_DIR, "models", "azp_yolo.onnx")
DEFAULT_DATASET_DIR = os.path.join(ROOT_DIR, "yolo_dataset")

# Canonical 10-class AzurPromilia 3D open-world & minigame detection schema
DEFAULT_CLASSES: Tuple[str, ...] = (
    "chest",         # 0: ordinary / exquisite / luxury open-world chest
    "chest_locked",  # 1: purple-sealed / puzzle-locked chest
    "star_melody",   # 2: floating Star Melody (星律)
    "wumi",          # 3: Wumi collectible (乌咪)
    "ore_stone",     # 4: minable stone / dust-stone ore outcrop (尘石矿脉)
    "kibo_egg",      # 5: wild Kibo Egg (奇波蛋)
    "puzzle_prop",   # 6: Kibo puzzle prop (stump / ice crystal / dry leaves / light monolith)
    "race_ring",     # 7: Mount Race blue waypoint ring (坐骑挑战蓝标)
    "cabbage_seed",  # 8: Cabbage Chicken glowing green seed (变身菜鸡种子)
    "enemy_wild",    # 9: wild Kibo / hostile mob / Shiny Kibo
)


@dataclass
class Detection:
    cls_id: int
    label: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float
    source: str = "onnx_yolo"

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) * 0.5

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) * 0.5

    @property
    def w(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def h(self) -> float:
        return max(0.0, self.y2 - self.y1)

    def to_dict(self) -> Dict[str, object]:
        d = asdict(self)
        d["cx"] = round(self.cx, 1)
        d["cy"] = round(self.cy, 1)
        d["w"] = round(self.w, 1)
        d["h"] = round(self.h, 1)
        d["err_x"] = round(self.cx - 960.0, 1)
        d["score"] = round(float(self.score), 3)
        for k in ("x1", "y1", "x2", "y2"):
            d[k] = round(float(d[k]), 1)
        return d


def is_onnxruntime_available() -> bool:
    return ort is not None


def available_providers() -> List[str]:
    if ort is None:
        return []
    return list(ort.get_available_providers())


def _preferred_providers() -> List[str]:
    avail = available_providers()
    order = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    picked = [p for p in order if p in avail]
    return picked or avail


def letterbox_image(
    rgb: np.ndarray,
    new_shape: Tuple[int, int] = (640, 640),
    pad_color: Tuple[int, int, int] = (114, 114, 114),
) -> Tuple[np.ndarray, float, Tuple[float, float]]:
    """Resize RGB uint8 image with aspect-preserving letterbox into NCHW float32 [0..1] tensor."""
    h, w = rgb.shape[:2]
    nh, nw = int(new_shape[0]), int(new_shape[1])
    scale = min(nw / float(w), nh / float(h))
    rw, rh = int(round(w * scale)), int(round(h * scale))
    pad_x = (nw - rw) * 0.5
    pad_y = (nh - rh) * 0.5

    if cv2 is not None:
        resized = cv2.resize(rgb, (rw, rh), interpolation=cv2.INTER_LINEAR)
    else:
        resized = np.asarray(Image.fromarray(rgb).resize((rw, rh), Image.Resampling.BILINEAR), dtype=np.uint8)

    canvas = np.full((nh, nw, 3), pad_color, dtype=np.uint8)
    left, top = int(round(pad_x - 0.1)), int(round(pad_y - 0.1))
    canvas[top : top + rh, left : left + rw] = resized
    blob = canvas.astype(np.float32) / 255.0
    blob = np.transpose(blob, (2, 0, 1))[None, ...]  # (1, 3, nh, nw)
    return np.ascontiguousarray(blob), scale, (float(left), float(top))


def _nms_numpy(boxes: np.ndarray, scores: np.ndarray, iou_thres: float) -> List[int]:
    """Pure NumPy greedy NMS on [N, 4] (x1, y1, x2, y2) boxes."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: List[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        union = areas[i] + areas[rest] - inter + 1e-6
        iou = inter / union
        order = rest[iou <= iou_thres]
    return keep


def decode_yolo_output(
    raw: np.ndarray,
    orig_hw: Tuple[int, int],
    scale: float,
    pad_xy: Tuple[float, float],
    class_names: Sequence[str] = DEFAULT_CLASSES,
    conf_thres: float = 0.35,
    iou_thres: float = 0.45,
    source: str = "onnx_yolo",
) -> List[Detection]:
    """Decode Ultralytics YOLOv8/YOLO11 (or YOLOv5) ONNX raw output tensor into client-pixel Detections.

    Supports:
    - ``(1, 4 + C, N)`` (standard YOLOv8 / YOLO11 ONNX export)
    - ``(1, N, 4 + C)`` (transposed export)
    - ``(1, N, 5 + C)`` (YOLOv5 export with objectness score at index 4)
    - ``(1, N, 6)`` (end-to-end NMS-fused YOLO10 / YOLO26 export: x1, y1, x2, y2, score, cls)
    """
    arr = np.asarray(raw, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.ndim != 2 or arr.size == 0:
        return []

    oh, ow = orig_hw
    px, py = pad_xy
    n_cls = len(class_names)

    # Case 1: End-to-end NMS-fused [N, 6] => [x1, y1, x2, y2, score, cls]
    if arr.shape[1] == 6 and arr.shape[0] <= 1000:
        dets: List[Detection] = []
        for row in arr:
            sc = float(row[4])
            if sc < conf_thres:
                continue
            cid = int(round(float(row[5])))
            lbl = class_names[cid] if 0 <= cid < n_cls else f"cls_{cid}"
            x1 = max(0.0, min(float(ow), (float(row[0]) - px) / scale))
            y1 = max(0.0, min(float(oh), (float(row[1]) - py) / scale))
            x2 = max(0.0, min(float(ow), (float(row[2]) - px) / scale))
            y2 = max(0.0, min(float(oh), (float(row[3]) - py) / scale))
            dets.append(Detection(cid, lbl, sc, x1, y1, x2, y2, source=source))
        dets.sort(key=lambda d: d.score, reverse=True)
        return dets

    # Case 2: Standard [4+C, N] -> transpose to [N, 4+C]
    valid_feats = (4 + n_cls, 5 + n_cls, 84, 85)
    if arr.shape[0] in valid_feats and (arr.shape[1] not in valid_feats or arr.shape[1] > arr.shape[0]):
        preds = arr.T
    else:
        preds = arr

    cols = preds.shape[1]
    if cols < 5:
        return []

    has_obj = (cols == 5 + n_cls) or (cols == 85)
    if has_obj:
        obj = preds[:, 4:5]
        cls_scores = preds[:, 5:] * obj
    else:
        cls_scores = preds[:, 4:]

    cls_ids = np.argmax(cls_scores, axis=1)
    scores = cls_scores[np.arange(len(preds)), cls_ids]
    keep_mask = scores >= conf_thres
    if not np.any(keep_mask):
        return []

    sel = preds[keep_mask]
    cls_ids = cls_ids[keep_mask]
    scores = scores[keep_mask]

    # cx, cy, w, h -> x1, y1, x2, y2 in original image coordinates
    cx = (sel[:, 0] - px) / scale
    cy = (sel[:, 1] - py) / scale
    bw = sel[:, 2] / scale
    bh = sel[:, 3] / scale
    x1 = np.clip(cx - bw * 0.5, 0.0, float(ow))
    y1 = np.clip(cy - bh * 0.5, 0.0, float(oh))
    x2 = np.clip(cx + bw * 0.5, 0.0, float(ow))
    y2 = np.clip(cy + bh * 0.5, 0.0, float(oh))
    boxes = np.stack([x1, y1, x2, y2], axis=1)

    # Class-aware NMS
    dets_out: List[Detection] = []
    for cid in np.unique(cls_ids):
        c_idx = np.where(cls_ids == cid)[0]
        c_boxes = boxes[c_idx]
        c_scores = scores[c_idx]
        kept = _nms_numpy(c_boxes, c_scores, iou_thres=iou_thres)
        lbl = class_names[int(cid)] if 0 <= int(cid) < n_cls else f"cls_{int(cid)}"
        for ki in kept:
            b = c_boxes[ki]
            dets_out.append(
                Detection(
                    cls_id=int(cid),
                    label=lbl,
                    score=float(c_scores[ki]),
                    x1=float(b[0]),
                    y1=float(b[1]),
                    x2=float(b[2]),
                    y2=float(b[3]),
                    source=source,
                )
            )
    dets_out.sort(key=lambda d: d.score, reverse=True)
    return dets_out


def _parse_onnx_names(meta_map: Dict[str, str], sidecar_path: Optional[str] = None) -> List[str]:
    if "names" in meta_map:
        raw = meta_map["names"]
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, dict):
                return [str(parsed[k]) for k in sorted(parsed.keys(), key=int)]
            if isinstance(parsed, (list, tuple)):
                return [str(x) for x in parsed]
        except Exception:
            pass
    if sidecar_path and os.path.isfile(sidecar_path):
        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data]
    return list(DEFAULT_CLASSES)


class YoloOnnxDetector:
    """Zero-PyTorch ONNXRuntime YOLOv8/YOLO11 detector for 3D open-world targets & minigames."""

    def __init__(
        self,
        model_path: str = DEFAULT_MODEL_PATH,
        conf_thres: float = 0.35,
        iou_thres: float = 0.45,
        providers: Optional[Sequence[str]] = None,
    ) -> None:
        if ort is None:
            raise RuntimeError("onnxruntime is not installed. Run: pip install onnxruntime")
        if not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"ONNX YOLO weights not found at {model_path!r}. "
                "Export a trained model via `yolo export model=best.pt format=onnx imgsz=640` "
                "or run `azp.py yolo-init-dataset` to bootstrap training data."
            )
        self.model_path = os.path.abspath(model_path)
        self.conf_thres = float(conf_thres)
        self.iou_thres = float(iou_thres)
        provs = list(providers) if providers else _preferred_providers()
        self.session = ort.InferenceSession(self.model_path, providers=provs)
        self.active_providers = list(self.session.get_providers())
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        shape = inp.shape
        ih = int(shape[2]) if len(shape) >= 4 and isinstance(shape[2], int) else 640
        iw = int(shape[3]) if len(shape) >= 4 and isinstance(shape[3], int) else 640
        self.input_hw = (ih, iw)
        meta = self.session.get_modelmeta().custom_metadata_map or {}
        sidecar = os.path.splitext(self.model_path)[0] + "_classes.json"
        self.class_names = _parse_onnx_names(meta, sidecar_path=sidecar)

    def detect_rgb(
        self,
        rgb: np.ndarray,
        classes: Optional[Sequence[str]] = None,
        conf_thres: Optional[float] = None,
    ) -> Tuple[List[Detection], float]:
        t0 = time.perf_counter()
        blob, scale, pad_xy = letterbox_image(rgb, new_shape=self.input_hw)
        outs = self.session.run(None, {self.input_name: blob})
        dets = decode_yolo_output(
            outs[0],
            orig_hw=(rgb.shape[0], rgb.shape[1]),
            scale=scale,
            pad_xy=pad_xy,
            class_names=self.class_names,
            conf_thres=self.conf_thres if conf_thres is None else conf_thres,
            iou_thres=self.iou_thres,
            source="onnx_yolo",
        )
        if classes:
            wanted = set(classes)
            dets = [d for d in dets if d.label in wanted]
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return dets, ms

    def detect(
        self,
        img: Union[str, Image.Image, np.ndarray],
        classes: Optional[Sequence[str]] = None,
        conf_thres: Optional[float] = None,
    ) -> Dict[str, object]:
        rgb = ocr_tpl._to_rgb_1080(img)
        dets, ms = self.detect_rgb(rgb, classes=classes, conf_thres=conf_thres)
        return {
            "ok": True,
            "backend": "onnx_yolo",
            "model": self.model_path,
            "providers": self.active_providers,
            "input_hw": list(self.input_hw),
            "infer_ms": ms,
            "count": len(dets),
            "detections": [d.to_dict() for d in dets],
        }

    def benchmark(self, img: Union[str, Image.Image, np.ndarray], runs: int = 10) -> Dict[str, object]:
        rgb = ocr_tpl._to_rgb_1080(img)
        # Warmup
        self.detect_rgb(rgb)
        times: List[float] = []
        last_count = 0
        for _ in range(max(1, runs)):
            dets, ms = self.detect_rgb(rgb)
            times.append(ms)
            last_count = len(dets)
        return {
            "ok": True,
            "model": self.model_path,
            "providers": self.active_providers,
            "input_hw": list(self.input_hw),
            "runs": len(times),
            "avg_ms": round(float(np.mean(times)), 2),
            "min_ms": round(float(np.min(times)), 2),
            "max_ms": round(float(np.max(times)), 2),
            "est_fps": round(1000.0 / max(0.1, float(np.mean(times))), 1),
            "last_count": last_count,
        }


def detect_race_rings_cv(rgb: np.ndarray, roi: Tuple[int, int, int, int] = (80, 130, 1840, 780)) -> List[Detection]:
    """Upgraded classical CV detector for 坐骑挑战 blue waypoint rings with anti-crystal-mountain filtering.

    Requires BOTH:
    - Compact electric-blue circular ring pixels (rejects huge blue crystal walls via aspect/area bounds).
    - Bright white inner diamond core inside the ring bounding box (crystal facets lack a centered white core).
    """
    x1, y1, x2, y2 = roi
    sub_full = rgb[y1:y2, x1:x2]
    a = sub_full[::2, ::2].astype(np.int16)
    b_mask = (
        (a[:, :, 2] > 210)
        & (a[:, :, 0] < 90)
        & (a[:, :, 1] > 110)
        & (a[:, :, 1] < 218)
        & ((a[:, :, 2] - a[:, :, 0]) > 135)
    )
    if int(b_mask.sum()) < 8:
        return []
    lbl, n = ndimage.label(ndimage.binary_dilation(b_mask, iterations=1))
    dets: List[Detection] = []
    cls_id = DEFAULT_CLASSES.index("race_ring")
    for idx in range(1, n + 1):
        comp = (lbl == idx) & b_mask
        cnt = int(comp.sum())
        if cnt < 10 or cnt > 300:
            continue
        ys, xs = np.where(comp)
        bw = int(xs.max() - xs.min()) + 1
        bh = int(ys.max() - ys.min()) + 1
        if bw < 5 or bh < 5 or bw > 34 or bh > 34:
            continue
        aspect = bw / float(bh)
        if aspect < 0.55 or aspect > 1.85:
            continue
        fill = cnt / float(bw * bh)
        if fill < 0.18:
            continue

        # Check for bright white inner diamond core in full-res crop
        fx1, fy1 = int(xs.min()) * 2, int(ys.min()) * 2
        fx2, fy2 = min(sub_full.shape[1], int(xs.max() + 1) * 2), min(sub_full.shape[0], int(ys.max() + 1) * 2)
        patch = sub_full[fy1:fy2, fx1:fx2]
        white_core = int(((patch[:, :, 0] > 218) & (patch[:, :, 1] > 225) & (patch[:, :, 2] > 230)).sum())
        if white_core < 8:
            continue

        # Score favors compact rings with clear white core near central elevation
        score = min(0.95, 0.55 + min(0.25, white_core / 120.0) + min(0.15, cnt / 220.0))
        dets.append(
            Detection(
                cls_id=cls_id,
                label="race_ring",
                score=score,
                x1=float(x1 + fx1),
                y1=float(y1 + fy1),
                x2=float(x1 + fx2),
                y2=float(y1 + fy2),
                source="cv_race_v2",
            )
        )
    dets.sort(key=lambda d: d.score, reverse=True)
    return dets


def detect_cabbage_seeds_cv(rgb: np.ndarray, roi: Tuple[int, int, int, int] = (140, 180, 1780, 880)) -> List[Detection]:
    """Classical CV detector for 变身菜鸡 glowing neon lime-green seeds (bright lime core + vertical glow)."""
    x1, y1, x2, y2 = roi
    sub = rgb[y1:y2, x1:x2]
    a = sub[::2, ::2].astype(np.int16)
    # Glowing seeds have ultra-bright yellow-lime core (G > 235, R 155..245, B < 135, G - B > 115)
    # which separates cleanly from ordinary dark/mid-tone grass (G ~ 90..170).
    glow = (
        (a[:, :, 1] > 232)
        & (a[:, :, 0] > 145)
        & (a[:, :, 0] < 248)
        & (a[:, :, 2] < 135)
        & ((a[:, :, 1] - a[:, :, 2]) > 110)
    )
    if int(glow.sum()) < 6:
        return []
    lbl, n = ndimage.label(ndimage.binary_dilation(glow, iterations=1))
    dets: List[Detection] = []
    cls_id = DEFAULT_CLASSES.index("cabbage_seed")
    for idx in range(1, n + 1):
        comp = (lbl == idx) & glow
        cnt = int(comp.sum())
        if cnt < 6 or cnt > 260:
            continue
        ys, xs = np.where(comp)
        bw = int(xs.max() - xs.min()) + 1
        bh = int(ys.max() - ys.min()) + 1
        if bw > 36 or bh > 52:
            continue
        cx = x1 + float(xs.mean()) * 2.0
        cy = y1 + float(ys.mean()) * 2.0
        # Exclude player chicken's own head/leaves at bottom-center (x=760..1160, y>620)
        if 760 <= cx <= 1160 and cy > 620:
            continue
        score = min(0.92, 0.55 + min(0.35, cnt / 90.0))
        dets.append(
            Detection(
                cls_id=cls_id,
                label="cabbage_seed",
                score=score,
                x1=float(x1 + int(xs.min()) * 2),
                y1=float(y1 + int(ys.min()) * 2),
                x2=float(x1 + int(xs.max() + 1) * 2),
                y2=float(y1 + int(ys.max() + 1) * 2),
                source="cv_cabbage_seed",
            )
        )
    dets.sort(key=lambda d: d.score, reverse=True)
    return dets


class HybridDetector:
    """Unified 3D detector: runs ONNX YOLO when weights exist, otherwise uses template + upgraded CV fallback."""

    def __init__(
        self,
        model_path: Optional[str] = DEFAULT_MODEL_PATH,
        conf_thres: float = 0.35,
        iou_thres: float = 0.45,
        templates_dir: str = ocr_tpl.DEFAULT_TEMPLATES_DIR,
    ) -> None:
        self.model_path = model_path
        self.conf_thres = float(conf_thres)
        self.iou_thres = float(iou_thres)
        self.templates_dir = templates_dir
        self.yolo: Optional[YoloOnnxDetector] = None
        if model_path and os.path.isfile(model_path) and is_onnxruntime_available():
            self.yolo = YoloOnnxDetector(model_path=model_path, conf_thres=conf_thres, iou_thres=iou_thres)

    @property
    def backend_name(self) -> str:
        return "onnx_yolo" if self.yolo is not None else "hybrid_cv_template"

    def detect_rgb(
        self,
        rgb: np.ndarray,
        classes: Optional[Sequence[str]] = None,
        use_ocr: bool = False,
    ) -> Tuple[List[Detection], float]:
        if self.yolo is not None:
            return self.yolo.detect_rgb(rgb, classes=classes)

        t0 = time.perf_counter()
        wanted = set(classes) if classes else set(DEFAULT_CLASSES)
        dets: List[Detection] = []

        # 1. Floating HUD reward markers via Part-1 templates (chest / wumi / kibo_egg)
        tpl_kinds = []
        if "chest" in wanted:
            tpl_kinds.append("chest")
        if "wumi" in wanted:
            tpl_kinds.append("wumi")
        if "kibo_egg" in wanted:
            tpl_kinds.append("egg")
        if tpl_kinds and ocr_tpl.is_cv2_available():
            markers = ocr_tpl.detect_floating_markers(
                rgb,
                kinds=tpl_kinds,
                use_ocr=use_ocr,
                threshold=max(0.74, self.conf_thres),
                templates_dir=self.templates_dir,
            )
            kind_to_cls = {"chest": "chest", "wumi": "wumi", "egg": "kibo_egg"}
            for m in markers:
                lbl = kind_to_cls.get(str(m["kind"]), str(m["kind"]))
                cid = DEFAULT_CLASSES.index(lbl) if lbl in DEFAULT_CLASSES else 0
                bx1, by1, bx2, by2 = m["box"]  # type: ignore[misc]
                dets.append(
                    Detection(cid, lbl, float(m["score"]), float(bx1), float(by1), float(bx2), float(by2), source="template_hud")
                )

        # 2. Minigame 3D targets (race_ring & cabbage_seed)
        if "race_ring" in wanted:
            dets.extend(detect_race_rings_cv(rgb))
        if "cabbage_seed" in wanted:
            dets.extend(detect_cabbage_seeds_cv(rgb))

        dets.sort(key=lambda d: d.score, reverse=True)
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return dets, ms

    def detect(
        self,
        img: Union[str, Image.Image, np.ndarray],
        classes: Optional[Sequence[str]] = None,
        use_ocr: bool = False,
    ) -> Dict[str, object]:
        rgb = ocr_tpl._to_rgb_1080(img)
        dets, ms = self.detect_rgb(rgb, classes=classes, use_ocr=use_ocr)
        return {
            "ok": True,
            "backend": self.backend_name,
            "model_loaded": self.yolo is not None,
            "model_path": self.model_path,
            "ort_providers": available_providers(),
            "infer_ms": ms,
            "count": len(dets),
            "detections": [d.to_dict() for d in dets],
        }


def steer_target_dx(det: Detection, center_x: float = 960.0, gain: float = 0.28, max_dx: int = 260) -> int:
    """Convert a 3D Detection horizontal offset into a smooth relative camera yaw delta."""
    err_x = det.cx - center_x
    if abs(err_x) <= 18.0:
        return 0
    return int(max(-max_dx, min(max_dx, round(err_x * gain))))


def init_yolo_dataset(
    out_dir: str = DEFAULT_DATASET_DIR,
    snaps_dir: str = os.path.join(ROOT_DIR, "snaps"),
    val_ratio: float = 0.15,
    max_images: int = 120,
    mask_uid: bool = True,
    seed: int = 42,
) -> Dict[str, object]:
    """Bootstrap an Ultralytics YOLOv8/YOLO11 training dataset from existing UID-masked snaps/."""
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    # Collect candidate PNGs (prefer world gameplay frames; skip small grid/map-only snaps when possible)
    all_pngs: List[str] = []
    if os.path.isdir(snaps_dir):
        for name in sorted(os.listdir(snaps_dir)):
            if not name.lower().endswith(".png"):
                continue
            low = name.lower()
            if any(skip in low for skip in ("map", "menu", "f1", "f2", "f5", "valley", "codex", "ach", "panel", "idle_chk")):
                continue
            all_pngs.append(os.path.join(snaps_dir, name))

    rng = random.Random(seed)
    if len(all_pngs) > max_images:
        picked = sorted(rng.sample(all_pngs, max_images))
    else:
        picked = all_pngs

    rng.shuffle(picked)
    n_val = max(1, int(round(len(picked) * val_ratio))) if len(picked) >= 4 else 0
    val_set = set(picked[:n_val])

    copied_train = 0
    copied_val = 0
    for src in picked:
        split = "val" if src in val_set else "train"
        dst_img = os.path.join(out_dir, "images", split, os.path.basename(src))
        dst_lbl = os.path.join(out_dir, "labels", split, os.path.splitext(os.path.basename(src))[0] + ".txt")
        if mask_uid:
            vision.mask_uid_image(src, out_path=dst_img)
        else:
            Image.open(src).convert("RGB").save(dst_img)
        if not os.path.exists(dst_lbl):
            with open(dst_lbl, "w", encoding="utf-8") as f:
                f.write("")
        if split == "val":
            copied_val += 1
        else:
            copied_train += 1

    # Write Ultralytics data.yaml & classes.txt
    yaml_lines = [
        f"path: {os.path.abspath(out_dir).replace(os.sep, '/')}",
        "train: images/train",
        "val: images/val",
        "",
        "names:",
    ]
    for i, cname in enumerate(DEFAULT_CLASSES):
        yaml_lines.append(f"  {i}: {cname}")
    data_yaml = os.path.join(out_dir, "data.yaml")
    with open(data_yaml, "w", encoding="utf-8") as f:
        f.write("\n".join(yaml_lines) + "\n")

    classes_txt = os.path.join(out_dir, "classes.txt")
    with open(classes_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(DEFAULT_CLASSES) + "\n")

    readme = os.path.join(out_dir, "README_TRAIN.md")
    with open(readme, "w", encoding="utf-8") as f:
        f.write(
            "# AZP YOLO11n / YOLOv8n Training & ONNX Export Guide\n\n"
            "1. Pre-label images automatically using AZP templates + CV:\n"
            "   ```powershell\n"
            f"   python azp.py yolo-prelabel --dataset \"{os.path.abspath(out_dir)}\"\n"
            "   ```\n"
            "2. Review/refine labels in LabelImg / X-AnyLabeling / Roboflow.\n"
            "3. Train a lightweight nano model and export to ONNX (in an environment with `ultralytics`):\n"
            "   ```powershell\n"
            f"   yolo detect train model=yolo11n.pt data=\"{os.path.abspath(data_yaml)}\" imgsz=640 epochs=80 batch=16\n"
            "   yolo export model=runs/detect/train/weights/best.pt format=onnx imgsz=640 simplify=True\n"
            "   ```\n"
            "4. Copy the exported `best.onnx` to `models/azp_yolo.onnx`.\n"
            "5. Verify zero-PyTorch ONNXRuntime speed:\n"
            "   ```powershell\n"
            "   python azp.py yolo-bench --model models/azp_yolo.onnx --frame snaps/g3_bf4_chest3.png\n"
            "   ```\n"
        )

    return {
        "ok": True,
        "out_dir": os.path.abspath(out_dir),
        "train_images": copied_train,
        "val_images": copied_val,
        "classes": list(DEFAULT_CLASSES),
        "data_yaml": data_yaml,
        "uid_masked": mask_uid,
    }


def prelabel_yolo_dataset(
    dataset_dir: str = DEFAULT_DATASET_DIR,
    min_score: float = 0.76,
    classes: Sequence[str] = ("chest", "wumi", "kibo_egg", "race_ring", "cabbage_seed"),
) -> Dict[str, object]:
    """Auto-generate initial YOLO-format .txt bounding boxes across a dataset using HybridDetector."""
    det = HybridDetector(model_path=None, conf_thres=min_score)
    labeled_files = 0
    total_boxes = 0
    per_class: Dict[str, int] = {}

    for split in ("train", "val"):
        img_dir = os.path.join(dataset_dir, "images", split)
        lbl_dir = os.path.join(dataset_dir, "labels", split)
        if not os.path.isdir(img_dir):
            continue
        os.makedirs(lbl_dir, exist_ok=True)
        for fname in sorted(os.listdir(img_dir)):
            if not fname.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            fpath = os.path.join(img_dir, fname)
            rgb = ocr_tpl._to_rgb_1080(fpath)
            dets, _ = det.detect_rgb(rgb, classes=classes, use_ocr=False)
            dets = [d for d in dets if d.score >= min_score]
            lines: List[str] = []
            for d in dets:
                cx_n = max(0.0, min(1.0, d.cx / 1920.0))
                cy_n = max(0.0, min(1.0, d.cy / 1080.0))
                w_n = max(0.001, min(1.0, d.w / 1920.0))
                h_n = max(0.001, min(1.0, d.h / 1080.0))
                lines.append(f"{d.cls_id} {cx_n:.6f} {cy_n:.6f} {w_n:.6f} {h_n:.6f}")
                per_class[d.label] = per_class.get(d.label, 0) + 1
            lbl_path = os.path.join(lbl_dir, os.path.splitext(fname)[0] + ".txt")
            with open(lbl_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + ("\n" if lines else ""))
            if lines:
                labeled_files += 1
                total_boxes += len(lines)

    return {
        "ok": True,
        "dataset_dir": os.path.abspath(dataset_dir),
        "labeled_files": labeled_files,
        "total_boxes": total_boxes,
        "per_class": per_class,
    }
