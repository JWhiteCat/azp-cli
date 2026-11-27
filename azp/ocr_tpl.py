"""Template matching + RapidOCR (ONNX) semantic perception and anomaly recovery for AzurPromilia.

Provides fast 2D perception calibrated for 1920x1080 client screenshots:
- Lazy singleton RapidOCR (CPU ONNX, zero PyTorch) on targeted ROIs.
- OpenCV multi-scale template matching (TM_CCOEFF_NORMED + NMS) backed by ``templates/``.
- ``read_interact_prompt``: OCRs the active [F] prompt bar and classifies the interactable.
- ``detect_floating_markers``: Detects 3D floating distance icons (chest, challenge, wumi, egg)
  and parses meter distances via OCR.
- ``detect_world_map_pois``: Extracts world-map POIs (active/locked Star Veins, locked Booster
  Facilities, shops, red World Bosses) ready to feed directly into ``azp/navmesh.py``.
- ``detect_screen_anomaly`` & ``recover_anomaly``: Detects and recovers from death revive screens
  (``CHALLENGE FAILED / 复苏``), full-screen tutorials (``关闭`` / multi-page ``A/D``), repair/confirm
  modals (with material ``have/need`` check), and Booster Facility build panels.
"""
from __future__ import annotations

import os
import re
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image
from scipy import ndimage

from . import input_ctl as ctl
from . import vision
from .win import capture_client, focus_game

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

try:
    from rapidocr_onnxruntime import RapidOCR  # type: ignore
except Exception:  # pragma: no cover
    RapidOCR = None  # type: ignore


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TEMPLATES_DIR = os.path.join(ROOT_DIR, "templates")

# World-map UI overlay regions (1920x1080) to exclude when scanning map POIs
MAP_UI_EXCLUSION_BOXES: Tuple[Tuple[int, int, int, int], ...] = (
    (0, 0, 260, 95),
    (1810, 0, 1920, 95),
    (0, 815, 220, 1080),
    (220, 1025, 420, 1080),
    (715, 975, 1215, 1065),
    (1785, 560, 1920, 875),
    (1660, 880, 1920, 1080),
)

_OCR_SINGLETON = None
_TPL_CACHE: Dict[str, np.ndarray] = {}


def is_cv2_available() -> bool:
    return cv2 is not None


def is_ocr_available() -> bool:
    return RapidOCR is not None


def get_ocr():
    """Return a lazy-initialized RapidOCR instance (horizontal UI mode for speed)."""
    global _OCR_SINGLETON
    if _OCR_SINGLETON is None:
        if RapidOCR is None:
            raise RuntimeError("rapidocr_onnxruntime is not installed. Run: pip install rapidocr-onnxruntime")
        _OCR_SINGLETON = RapidOCR(use_cls=False)
    return _OCR_SINGLETON


def _to_rgb_1080(img: Union[str, Image.Image, np.ndarray]) -> np.ndarray:
    if isinstance(img, str):
        im = Image.open(img).convert("RGB")
        return vision._norm_arr(im)
    if isinstance(img, Image.Image):
        return vision._norm_arr(img)
    arr = np.asarray(img)
    if arr.ndim == 3 and arr.shape[2] == 4:
        arr = arr[:, :, :3]
    if arr.shape[0] != 1080 or arr.shape[1] != 1920:
        im = Image.fromarray(arr.astype(np.uint8)).resize((1920, 1080), Image.Resampling.BILINEAR)
        return np.asarray(im, dtype=np.uint8)
    return arr.astype(np.uint8, copy=False)


def ocr_roi(
    img: Union[str, Image.Image, np.ndarray],
    roi: Optional[Tuple[int, int, int, int]] = None,
    min_score: float = 0.48,
) -> Dict[str, object]:
    """Run RapidOCR on a 1920x1080 frame (or crop ROI) and return boxes in 1920x1080 coords."""
    arr = _to_rgb_1080(img)
    x1, y1, x2, y2 = (0, 0, 1920, 1080) if roi is None else roi
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(1920, int(x2)), min(1080, int(y2))
    crop = arr[y1:y2, x1:x2]
    if crop.size == 0:
        return {"ok": True, "elapsed_ms": 0.0, "items": [], "joined": ""}

    ocr = get_ocr()
    t0 = time.perf_counter()
    raw, _ = ocr(crop)
    ms = round((time.perf_counter() - t0) * 1000.0, 1)

    items: List[Dict[str, object]] = []
    for entry in raw or []:
        pts, text, score = entry[0], str(entry[1]).strip(), float(entry[2])
        if score < min_score or not text:
            continue
        xs = [float(p[0]) + x1 for p in pts]
        ys = [float(p[1]) + y1 for p in pts]
        bx1, by1, bx2, by2 = min(xs), min(ys), max(xs), max(ys)
        items.append(
            {
                "text": text,
                "score": round(score, 3),
                "box": [round(bx1, 1), round(by1, 1), round(bx2, 1), round(by2, 1)],
                "center": [round((bx1 + bx2) * 0.5, 1), round((by1 + by2) * 0.5, 1)],
            }
        )
    items.sort(key=lambda it: (it["box"][1], it["box"][0]))  # type: ignore[index]
    return {
        "ok": True,
        "elapsed_ms": ms,
        "roi": [x1, y1, x2, y2],
        "items": items,
        "joined": " | ".join(str(it["text"]) for it in items),
    }


def load_template(name_or_path: str, templates_dir: str = DEFAULT_TEMPLATES_DIR) -> np.ndarray:
    """Load an RGB template image by basename (inside templates/) or explicit path."""
    path = name_or_path
    if not os.path.isfile(path):
        cand = os.path.join(templates_dir, name_or_path)
        if not cand.lower().endswith(".png") and not os.path.isfile(cand):
            cand += ".png"
        path = cand
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Template not found: {name_or_path!r} (checked {path})")
    key = os.path.abspath(path)
    if key not in _TPL_CACHE:
        _TPL_CACHE[key] = np.asarray(Image.open(key).convert("RGB"), dtype=np.uint8)
    return _TPL_CACHE[key]


def list_templates(templates_dir: str = DEFAULT_TEMPLATES_DIR) -> List[str]:
    if not os.path.isdir(templates_dir):
        return []
    return sorted(f[:-4] for f in os.listdir(templates_dir) if f.lower().endswith(".png"))


def match_template(
    img: Union[str, Image.Image, np.ndarray],
    template: Union[str, np.ndarray],
    roi: Optional[Tuple[int, int, int, int]] = None,
    threshold: float = 0.74,
    scales: Sequence[float] = (1.0,),
    nms_dist: float = 28.0,
    max_results: int = 20,
    templates_dir: str = DEFAULT_TEMPLATES_DIR,
) -> List[Dict[str, object]]:
    """Multi-scale OpenCV TM_CCOEFF_NORMED template matcher with greedy NMS."""
    if cv2 is None:
        raise RuntimeError("opencv-python is not installed. Run: pip install opencv-python-headless")
    arr = _to_rgb_1080(img)
    tpl_base = load_template(template, templates_dir=templates_dir) if isinstance(template, str) else template
    x1, y1, x2, y2 = (0, 0, 1920, 1080) if roi is None else roi
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(1920, int(x2)), min(1080, int(y2))
    sub = arr[y1:y2, x1:x2]
    sh, sw = sub.shape[:2]

    candidates: List[Tuple[float, float, float, int, int, float]] = []
    for sc in scales:
        th = max(6, int(round(tpl_base.shape[0] * sc)))
        tw = max(6, int(round(tpl_base.shape[1] * sc)))
        if th >= sh or tw >= sw:
            continue
        tpl = tpl_base if abs(sc - 1.0) < 1e-3 else cv2.resize(tpl_base, (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= threshold)
        if len(xs) == 0:
            continue
        vals = res[ys, xs]
        # Keep top raw peaks per scale to avoid huge sorting cost on flat regions
        if len(vals) > 250:
            top_idx = np.argpartition(vals, -250)[-250:]
            xs, ys, vals = xs[top_idx], ys[top_idx], vals[top_idx]
        for px, py, val in zip(xs, ys, vals):
            cx = x1 + float(px) + tw * 0.5
            cy = y1 + float(py) + th * 0.5
            candidates.append((float(val), cx, cy, tw, th, float(sc)))

    candidates.sort(key=lambda c: c[0], reverse=True)
    kept: List[Dict[str, object]] = []
    nms_d2 = float(nms_dist) ** 2
    for score, cx, cy, tw, th, sc in candidates:
        if any((cx - float(k["x"])) ** 2 + (cy - float(k["y"])) ** 2 < nms_d2 for k in kept):
            continue
        kept.append(
            {
                "x": round(cx, 1),
                "y": round(cy, 1),
                "w": tw,
                "h": th,
                "box": [round(cx - tw * 0.5, 1), round(cy - th * 0.5, 1), round(cx + tw * 0.5, 1), round(cy + th * 0.5, 1)],
                "score": round(score, 3),
                "scale": round(sc, 2),
            }
        )
        if len(kept) >= max_results:
            break
    return kept


def _classify_interact_text(text: str) -> str:
    t = text.strip()
    if any(k in t for k in ("宝箱", "秘藏", "箱")):
        return "chest"
    if any(k in t for k in ("修复", "建造", "增效", "祭坛", "矿场")):
        return "facility"
    if any(k in t for k in ("星脉", "节点", "激活", "锚点")):
        return "star_vein"
    if any(k in t for k in ("挑战", "开启", "试炼", "祖赞卡", "菜鸡", "竞赛")):
        return "challenge"
    if any(k in t for k in ("乌咪", "星律", "调查", "奇波蛋")):
        return "collectible"
    if any(k in t for k in ("交谈", "对话", "商店", "购买")):
        return "npc"
    if t:
        return "gather"
    return "unknown"


def read_interact_prompt(img: Union[str, Image.Image, np.ndarray]) -> Dict[str, object]:
    """Check if the [F] interact pill is visible and OCR its Chinese prompt label(s)."""
    arr = _to_rgb_1080(img)
    im = Image.fromarray(arr)
    has_f, ratio = vision.detect_interact_prompt(im)
    if not has_f and ratio < 0.25:
        return {"has_interact_f": False, "ratio": ratio, "options": [], "primary": "", "category": "none"}

    # [F] prompt list sits at x=1140..1540, y=515..695
    ocr_res = ocr_roi(arr, roi=(1140, 515, 1540, 695), min_score=0.45)
    items = ocr_res["items"]  # type: ignore[assignment]
    options = [str(it["text"]) for it in items]
    primary = options[0] if options else ""
    return {
        "has_interact_f": has_f,
        "ratio": ratio,
        "options": options,
        "primary": primary,
        "category": _classify_interact_text(primary),
        "ocr_ms": ocr_res["elapsed_ms"],
        "raw": items,
    }


def _parse_meters(text: str) -> Optional[int]:
    m = re.search(r"(\d{1,4})\s*[mM米]", text)
    if m:
        return int(m.group(1))
    # Sometimes OCR drops 'm' on small distance pills: accept 1..999 pure digits
    m2 = re.fullmatch(r"\d{1,3}", text.strip())
    if m2:
        return int(m2.group(0))
    return None


def detect_floating_markers(
    img: Union[str, Image.Image, np.ndarray],
    kinds: Sequence[str] = ("chest", "challenge", "wumi", "egg"),
    use_ocr: bool = True,
    threshold: float = 0.74,
    templates_dir: str = DEFAULT_TEMPLATES_DIR,
) -> List[Dict[str, object]]:
    """Detect 3D floating HUD markers (chest/challenge/wumi/egg) and optionally OCR their distance (m)."""
    arr = _to_rgb_1080(img)
    tpl_map = {
        "chest": ("hud_chest", max(0.78, threshold)),
        "challenge": ("hud_challenge", max(0.78, threshold)),
        "wumi": ("hud_wumi", max(0.78, threshold)),
        "egg": ("hud_egg", max(0.79, threshold)),
    }
    # Exclude minimap (top-left), top HUD bar, right party list, bottom skill bar
    vp_roi = (120, 120, 1740, 910)
    found: List[Dict[str, object]] = []
    for kind in kinds:
        if kind not in tpl_map:
            continue
        tpl_name, th = tpl_map[kind]
        try:
            hits = match_template(
                arr,
                tpl_name,
                roi=vp_roi,
                threshold=th,
                scales=(0.95, 1.0, 1.05),
                nms_dist=36.0,
                max_results=8,
                templates_dir=templates_dir,
            )
        except FileNotFoundError:
            continue
        for h in hits:
            cx, cy = float(h["x"]), float(h["y"])
            # Skip minimap circle area even inside vp_roi
            if cx < 260 and cy < 235:
                continue
            dist_m: Optional[int] = None
            dist_text = ""
            if use_ocr and is_ocr_available():
                sub_roi = (int(cx - 46), int(cy + 12), int(cx + 46), int(cy + 56))
                ores = ocr_roi(arr, roi=sub_roi, min_score=0.40)
                dist_text = str(ores["joined"])
                for it in ores["items"]:  # type: ignore[attr-defined]
                    val = _parse_meters(str(it["text"]))
                    if val is not None:
                        dist_m = val
                        break
            err_x = round(cx - 960.0, 1)
            found.append(
                {
                    "kind": kind,
                    "x": round(cx, 1),
                    "y": round(cy, 1),
                    "box": h["box"],
                    "score": h["score"],
                    "distance_m": dist_m,
                    "distance_text": dist_text,
                    "err_x": err_x,
                    "steer_dx": int(round(err_x * 0.55)),
                }
            )
    found.sort(key=lambda m: (m["distance_m"] if m["distance_m"] is not None else 9999, -float(m["score"])))
    return found


def _in_ui_exclusion(x: float, y: float) -> bool:
    for x1, y1, x2, y2 in MAP_UI_EXCLUSION_BOXES:
        if x1 <= x <= x2 and y1 <= y <= y2:
            return True
    return False


def _detect_map_red_bosses(arr: np.ndarray) -> List[Dict[str, object]]:
    """Detect red World Boss danger circles on a 1920x1080 world-map screenshot."""
    a = arr.astype(np.int16)
    red = (a[:, :, 0] > 175) & (a[:, :, 1] < 85) & (a[:, :, 2] < 85) & ((a[:, :, 0] - a[:, :, 1]) > 95)
    # Mask out UI areas
    for x1, y1, x2, y2 in MAP_UI_EXCLUSION_BOXES:
        red[y1:y2, x1:x2] = False
    merged = ndimage.binary_dilation(red, iterations=3)
    labeled, n = ndimage.label(merged)
    bosses: List[Dict[str, object]] = []
    for idx in range(1, n + 1):
        comp = (labeled == idx) & red
        cnt = int(comp.sum())
        if cnt < 180 or cnt > 9000:
            continue
        ys, xs = np.where(comp)
        cx, cy = float(xs.mean()), float(ys.mean())
        w, h = float(xs.max() - xs.min()), float(ys.max() - ys.min())
        if w < 18 or h < 18 or w > 140 or h > 140:
            continue
        bosses.append(
            {
                "kind": "boss",
                "x": round(cx, 1),
                "y": round(cy, 1),
                "box": [round(float(xs.min()), 1), round(float(ys.min()), 1), round(float(xs.max()), 1), round(float(ys.max()), 1)],
                "score": round(min(0.99, 0.70 + cnt / 4000.0), 3),
                "safe_click": cy < 885.0,
                "needs_drag_dy": round(600.0 - cy, 1) if cy >= 885.0 else 0.0,
            }
        )
    return bosses


def detect_world_map_pois(
    img: Union[str, Image.Image, np.ndarray],
    use_ocr: bool = False,
    templates_dir: str = DEFAULT_TEMPLATES_DIR,
) -> Dict[str, object]:
    """Extract world-map POIs (Star Veins, locked Booster Facilities, shops, World Bosses).

    Output ``(x, y)`` coordinates align directly with ``azp/navmesh.py`` (1920x1080 map space)
    and include ``safe_click`` / ``needs_drag_dy`` guards for bottom zoom-bar protection.
    """
    arr = _to_rgb_1080(img)
    spec = [
        ("star_vein_active", "map_star_vein_active", 0.72),
        ("star_vein_locked", "map_star_vein_locked", 0.72),
        ("facility_locked", "map_padlock", 0.74),
        ("shop_bag", "map_bag_shop", 0.74),
    ]
    pois: List[Dict[str, object]] = []
    for kind, tpl_name, th in spec:
        try:
            hits = match_template(
                arr,
                tpl_name,
                threshold=th,
                scales=(0.95, 1.0, 1.05),
                nms_dist=34.0,
                max_results=16,
                templates_dir=templates_dir,
            )
        except FileNotFoundError:
            continue
        for h in hits:
            cx, cy = float(h["x"]), float(h["y"])
            if _in_ui_exclusion(cx, cy):
                continue
            pois.append(
                {
                    "kind": kind,
                    "x": round(cx, 1),
                    "y": round(cy, 1),
                    "box": h["box"],
                    "score": h["score"],
                    "safe_click": cy < 885.0,
                    "needs_drag_dy": round(600.0 - cy, 1) if cy >= 885.0 else 0.0,
                }
            )

    bosses = _detect_map_red_bosses(arr)
    if use_ocr and is_ocr_available():
        for b in bosses:
            cx, cy = float(b["x"]), float(b["y"])
            ores = ocr_roi(arr, roi=(int(cx - 90), int(cy + 15), int(cx + 90), int(cy + 65)), min_score=0.45)
            b["label"] = str(ores["joined"])
    pois.extend(bosses)

    # Deduplicate overlapping detections of different kinds (prefer higher score)
    pois.sort(key=lambda p: float(p["score"]), reverse=True)
    deduped: List[Dict[str, object]] = []
    for p in pois:
        if any(
            p["kind"] == q["kind"] and (float(p["x"]) - float(q["x"])) ** 2 + (float(p["y"]) - float(q["y"])) ** 2 < 32.0 ** 2
            for q in deduped
        ):
            continue
        deduped.append(p)

    counts: Dict[str, int] = {}
    for p in deduped:
        k = str(p["kind"])
        counts[k] = counts.get(k, 0) + 1
    return {"ok": True, "count": len(deduped), "counts": counts, "pois": deduped}


def _parse_material_ratio(text: str) -> Optional[Dict[str, object]]:
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if not m:
        return None
    have, need = int(m.group(1)), int(m.group(2))
    return {"have": have, "need": need, "enough": have >= need}


def detect_screen_anomaly(
    img: Union[str, Image.Image, np.ndarray],
    use_ocr: bool = True,
) -> Dict[str, object]:
    """Classify non-HUD / anomalous screens (revive, tutorial, confirm modal, build panel, floating tip).

    Uses fast pixel-signature gating first so normal open-world HUD frames cost <3ms with zero OCR calls.
    """
    arr = _to_rgb_1080(img)
    im = Image.fromarray(arr)
    hud = vision.check_hud_state(im)

    if hud["loading"]:
        return {"anomaly": "loading", "action": "wait", "hud": hud}

    # 1. Centered white confirm modal (修复建筑 / 离开家园 / 提示)
    if hud["confirm_dialog"]:
        info: Dict[str, object] = {
            "anomaly": "confirm_dialog",
            "confirm_xy": [1258, 676],
            "cancel_xy": [950, 676],
            "hud": hud,
        }
        if use_ocr and is_ocr_available():
            ores = ocr_roi(arr, roi=(640, 380, 1410, 720), min_score=0.45)
            joined = str(ores["joined"])
            info["text"] = joined
            mat = _parse_material_ratio(joined)
            if mat is not None:
                info["materials"] = mat
                info["action"] = "confirm" if mat["enough"] else "cancel"
            elif "离开家园" in joined:
                info["action"] = "confirm"
            else:
                info["action"] = "inspect"
        else:
            info["action"] = "confirm"
        return info

    # 2. Check death / CHALLENGE FAILED revive screen: bright bottom mist + white pill at (770..1120, 765..825)
    if not hud["in_world_hud"]:
        btn_revive = arr[770:820, 820:1070]
        btn_bright = float(((btn_revive[:, :, 0] > 215) & (btn_revive[:, :, 1] > 215) & (btn_revive[:, :, 2] > 215)).mean())
        top_dark = float(arr[40:180, 400:1520].mean())
        if btn_bright > 0.72 and top_dark < 75.0:
            ocr_text = ""
            confirmed = False
            if use_ocr and is_ocr_available():
                ores = ocr_roi(arr, roi=(600, 230, 1320, 845), min_score=0.45)
                ocr_text = str(ores["joined"])
                if any(k in ocr_text.upper() for k in ("CHALLENGE", "FAILE", "HALLENGE")) or any(
                    k in ocr_text for k in ("复苏", "提升队伍实力")
                ):
                    confirmed = True
            else:
                confirmed = True
            if confirmed:
                return {
                    "anomaly": "revive",
                    "action": "click",
                    "click_xy": [945, 795],
                    "text": ocr_text,
                    "hud": hud,
                }

        # 3. Full-screen tutorial modal (变身菜鸡 / 强大敌人): dark top + dark bottom with centered media frame
        mid_frame = arr[255:725, 450:1470]
        top_band = arr[20:150, 200:1720]
        bot_band = arr[920:1050, 200:1720]
        if float(top_band.mean()) < 90.0 and float(bot_band.mean()) < 90.0 and float(mid_frame.std()) > 32.0:
            ocr_text = ""
            has_close = False
            has_next = False
            if use_ocr and is_ocr_available():
                ores_top = ocr_roi(arr, roi=(720, 170, 1200, 255), min_score=0.45)
                ores_bot = ocr_roi(arr, roi=(640, 725, 1280, 945), min_score=0.45)
                ocr_text = " | ".join(x for x in (str(ores_top["joined"]), str(ores_bot["joined"])) if x)
                has_close = "关闭" in ocr_text
                has_next = ("翻页" in ocr_text) or ("左右箭头" in ocr_text)
            if has_close or has_next:
                return {
                    "anomaly": "tutorial_modal",
                    "action": "close_tutorial" if has_close else "page_and_close_tutorial",
                    "close_xy": [960, 865],
                    "text": ocr_text,
                    "hud": hud,
                }

        # 4. Right-hand Booster Facility build parchment card (建造增效设施 / 已建造)
        card = arr[165:930, 1345:1795]
        card_cream = float(((card[:, :, 0] > 215) & (card[:, :, 1] > 210) & (card[:, :, 2] > 195)).mean())
        if card_cream > 0.45:
            ocr_text = ""
            action = "close_panel"
            if use_ocr and is_ocr_available():
                ores = ocr_roi(arr, roi=(1350, 150, 1790, 940), min_score=0.45)
                ocr_text = str(ores["joined"])
                if "已建造" in ocr_text:
                    action = "close_panel"
                elif "建造" in ocr_text:
                    action = "build"
            return {
                "anomaly": "build_panel",
                "action": action,
                "build_xy": [1570, 888],
                "close_xy": [1865, 50],
                "text": ocr_text,
                "hud": hud,
            }

    # 5. In-world frozen floating tip bubble (e.g. 头目、特异体、幻域兽警惕性较高)
    if hud["in_world_hud"]:
        tip_box = arr[165:260, 665:1005]
        tip_white = float(((tip_box[:, :, 0] > 205) & (tip_box[:, :, 1] > 210) & (tip_box[:, :, 2] > 215)).mean())
        if tip_white > 0.58:
            ocr_text = ""
            if use_ocr and is_ocr_available():
                ores = ocr_roi(arr, roi=(655, 160, 1020, 275), min_score=0.45)
                ocr_text = str(ores["joined"])
            if ocr_text or tip_white > 0.68:
                return {
                    "anomaly": "floating_tip",
                    "action": "dismiss",
                    "click_xy": [840, 210],
                    "text": ocr_text,
                    "hud": hud,
                }

    return {"anomaly": "normal", "action": "none", "hud": hud}


def recover_anomaly(
    auto_confirm_repair: bool = False,
    dry_run: bool = False,
    frame_path: Optional[str] = None,
) -> Dict[str, object]:
    """Detect anomalous screens and optionally execute the safe recovery action live."""
    if frame_path is not None:
        det = detect_screen_anomaly(frame_path, use_ocr=True)
        return {"ok": True, "dry_run": True, "detected": det, "executed": None}

    gw = focus_game()
    img = capture_client(gw=gw)
    det = detect_screen_anomaly(img, use_ocr=True)
    kind = str(det["anomaly"])
    action = str(det["action"])
    if dry_run or kind in ("normal", "loading"):
        return {"ok": True, "dry_run": dry_run, "detected": det, "executed": None}

    executed = None
    if kind == "revive":
        ctl.mouse_click_client(945, 795, gw=gw)
        time.sleep(6.0)
        executed = "clicked_revive"
    elif kind == "tutorial_modal":
        if action == "page_and_close_tutorial":
            for _ in range(3):
                ctl.tap("d", hold=0.06, after=0.45)
        ctl.mouse_click_client(960, 865, gw=gw)
        time.sleep(0.7)
        executed = "closed_tutorial"
    elif kind == "confirm_dialog":
        mat = det.get("materials")
        if action == "confirm" and (auto_confirm_repair or mat is None):
            ctl.mouse_click_client(1258, 676, gw=gw)
            time.sleep(2.5)
            executed = "confirmed_dialog"
        else:
            ctl.mouse_click_client(950, 676, gw=gw)
            time.sleep(0.5)
            executed = "cancelled_dialog"
    elif kind == "build_panel":
        if action == "build" and auto_confirm_repair:
            ctl.mouse_click_client(1570, 888, gw=gw)
            time.sleep(3.0)
            ctl.mouse_click_client(1865, 50, gw=gw)
            executed = "built_and_closed"
        else:
            ctl.mouse_click_client(1865, 50, gw=gw)
            executed = "closed_build_panel"
        time.sleep(0.5)
    elif kind == "floating_tip":
        ctl.mouse_click_client(840, 210, gw=gw)
        time.sleep(0.35)
        ctl.mouse_click_client(960, 920, gw=gw)
        executed = "dismissed_tip"

    return {"ok": True, "dry_run": False, "detected": det, "executed": executed}
