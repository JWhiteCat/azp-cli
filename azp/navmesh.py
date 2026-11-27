"""2.5D world-map costmap, A* pathfinding, online obstacle memory, and minimap localization.

Pure external-vision navigation architecture (zero memory reads, zero asset unpacking):
- Bakes a 1920x1080 world-map screenshot into a cached 2.5D semantic cost grid:
  roads (low cost), flat grass, topographic contour density (2.5D slope penalty),
  ruins/stone structures, water bodies, red World Boss danger bubbles, and fogged regions.
- Persists online learned obstacles (stagnation/snag points), proven passable trails,
  and directed 3D action edges (jump/mushroom/teleport/fly) in JSON alongside the baked grid.
- Supports dual-layer pathfinding (``mode="ground"`` vs ``mode="fly"``), 8-connected A*
  with cost-aware line-of-sight string-pulling, minimap-to-world-map localization, and
  closed-loop replanning navigation.
"""
from __future__ import annotations

import heapq
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage, signal

from . import nav as nav_mod
from . import vision
from .win import capture_client, find_game_window

# Semantic terrain classes stored per grid cell
TERRAIN_ROAD = 1
TERRAIN_GRASS = 2
TERRAIN_SLOPE = 3
TERRAIN_RUINS = 4
TERRAIN_WATER = 5
TERRAIN_BOSS = 6
TERRAIN_FOG = 7
TERRAIN_UI = 8

# Default ground traversal costs per terrain class
DEFAULT_COSTS: Dict[str, float] = {
    "road": 1.0,
    "grass": 2.1,
    "slope_max_extra": 9.0,
    "ruins": 10.5,
    "fog": 5.5,
    "boss": 26.0,
    "water": 32.0,
    "ui": 50.0,
}

# World-map UI overlay bounding boxes on 1920x1080 (x1, y1, x2, y2) to exclude from terrain classification
MAP_UI_BOXES: Tuple[Tuple[int, int, int, int], ...] = (
    (0, 0, 260, 95),          # Top-left 冒险地图 title
    (1810, 0, 1920, 95),      # Top-right X close button
    (0, 815, 220, 1080),      # Bottom-left 奇波对决 + compass + UID
    (220, 1025, 420, 1080),   # Bottom-left UID strip
    (715, 975, 1215, 1065),   # Bottom-center zoom bar
    (1785, 560, 1920, 875),   # Right-side 筛选 / 采集物 / 探索度 buttons
    (1660, 880, 1920, 1080),  # Bottom-right 世界地图 medallion
)

DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nav_cache")


@dataclass
class ObstacleRecord:
    x: float
    y: float
    radius: float = 24.0
    penalty: float = 18.0
    hits: int = 1
    reason: str = "stuck"
    updated_at: float = field(default_factory=time.time)


@dataclass
class TrailRecord:
    x: float
    y: float
    radius: float = 16.0
    discount: float = 0.72  # multiplier < 1.0 applied to grass/slope cells
    hits: int = 1
    updated_at: float = field(default_factory=time.time)


@dataclass
class PortalEdge:
    src_x: float
    src_y: float
    dst_x: float
    dst_y: float
    action: str = "jump"  # jump | mushroom | teleport | fly
    cost: float = 4.0
    label: str = ""


class ObstacleMemory:
    """Persistent online memory of terrain snags, proven passable trails, and 3D action edges."""

    def __init__(
        self,
        obstacles: Optional[List[ObstacleRecord]] = None,
        trails: Optional[List[TrailRecord]] = None,
        portals: Optional[List[PortalEdge]] = None,
    ) -> None:
        self.obstacles: List[ObstacleRecord] = list(obstacles or [])
        self.trails: List[TrailRecord] = list(trails or [])
        self.portals: List[PortalEdge] = list(portals or [])

    def record_obstacle(
        self,
        x: float,
        y: float,
        radius: float = 24.0,
        penalty: float = 18.0,
        reason: str = "stuck",
        merge_dist: float = 18.0,
        max_penalty: float = 65.0,
    ) -> ObstacleRecord:
        """Record (or reinforce) an impassable/snag point learned online."""
        now = time.time()
        for obs in self.obstacles:
            if math.hypot(obs.x - x, obs.y - y) <= merge_dist:
                obs.hits += 1
                obs.x = (obs.x * (obs.hits - 1) + x) / obs.hits
                obs.y = (obs.y * (obs.hits - 1) + y) / obs.hits
                obs.radius = max(obs.radius, radius)
                obs.penalty = min(max_penalty, obs.penalty + penalty * 0.6)
                obs.reason = reason
                obs.updated_at = now
                return obs
        rec = ObstacleRecord(x=float(x), y=float(y), radius=float(radius), penalty=float(penalty), hits=1, reason=reason, updated_at=now)
        self.obstacles.append(rec)
        return rec

    def record_passable(
        self,
        x: float,
        y: float,
        radius: float = 16.0,
        discount: float = 0.75,
        merge_dist: float = 16.0,
    ) -> TrailRecord:
        """Record a smoothly traversed point to reinforce proven safe corridors."""
        now = time.time()
        # Slightly weaken any stale obstacle right on top of a newly proven passable point
        for obs in list(self.obstacles):
            if math.hypot(obs.x - x, obs.y - y) <= max(10.0, obs.radius * 0.5):
                obs.penalty *= 0.6
                if obs.penalty < 3.0:
                    self.obstacles.remove(obs)

        for tr in self.trails:
            if math.hypot(tr.x - x, tr.y - y) <= merge_dist:
                tr.hits += 1
                tr.discount = max(0.55, min(tr.discount, discount))
                tr.updated_at = now
                return tr
        rec = TrailRecord(x=float(x), y=float(y), radius=float(radius), discount=float(discount), hits=1, updated_at=now)
        self.trails.append(rec)
        return rec

    def add_portal(
        self,
        src_x: float,
        src_y: float,
        dst_x: float,
        dst_y: float,
        action: str = "jump",
        cost: float = 4.0,
        label: str = "",
    ) -> PortalEdge:
        edge = PortalEdge(
            src_x=float(src_x),
            src_y=float(src_y),
            dst_x=float(dst_x),
            dst_y=float(dst_y),
            action=action,
            cost=float(cost),
            label=label,
        )
        self.portals.append(edge)
        return edge

    def clear(self) -> None:
        self.obstacles.clear()
        self.trails.clear()
        self.portals.clear()

    def to_dict(self) -> Dict[str, object]:
        return {
            "obstacles": [asdict(o) for o in self.obstacles],
            "trails": [asdict(t) for t in self.trails],
            "portals": [asdict(p) for p in self.portals],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "ObstacleMemory":
        obs = [ObstacleRecord(**d) for d in data.get("obstacles", [])]  # type: ignore[arg-type]
        trs = [TrailRecord(**d) for d in data.get("trails", [])]       # type: ignore[arg-type]
        pts = [PortalEdge(**d) for d in data.get("portals", [])]       # type: ignore[arg-type]
        return cls(obstacles=obs, trails=trs, portals=pts)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "ObstacleMemory":
        if not os.path.isfile(path):
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


def _strip_debug_gridlines(arr: np.ndarray) -> np.ndarray:
    """Remove 1px bright-yellow `--grid` overlay lines (R>210, G>210, B<90) via neighbor interpolation."""
    out = arr.copy()
    yellow = (arr[:, :, 0] > 210) & (arr[:, :, 1] > 210) & (arr[:, :, 2] < 90)
    # Detect rows/cols that have >35% pure yellow pixels (characteristic of azp snap --grid)
    row_ratio = yellow.mean(axis=1)
    col_ratio = yellow.mean(axis=0)
    bad_rows = np.where(row_ratio > 0.35)[0]
    bad_cols = np.where(col_ratio > 0.35)[0]
    h, w = arr.shape[:2]
    for r in bad_rows:
        src_r = r - 2 if r >= 2 else min(h - 1, r + 2)
        out[r, :, :] = out[src_r, :, :]
    for c in bad_cols:
        src_c = c - 2 if c >= 2 else min(w - 1, c + 2)
        out[:, c, :] = out[:, src_c, :]
    return out


def _downsample_mean(mat: np.ndarray, cell_size: int) -> np.ndarray:
    h, w = mat.shape[:2]
    gh = h // cell_size
    gw = w // cell_size
    trimmed = mat[: gh * cell_size, : gw * cell_size]
    if mat.ndim == 2:
        return trimmed.reshape(gh, cell_size, gw, cell_size).mean(axis=(1, 3))
    return trimmed.reshape(gh, cell_size, gw, cell_size, mat.shape[2]).mean(axis=(1, 3))


class CostMap25D:
    """Baked 2.5D navigation cost grid + A* pathfinder + online obstacle memory."""

    def __init__(
        self,
        name: str,
        width: int,
        height: int,
        cell_size: int,
        base_cost: np.ndarray,
        terrain_class: np.ndarray,
        slope_map: np.ndarray,
        map_rgb_small: Optional[np.ndarray] = None,
        source_image: str = "",
        px_per_sec: float = 34.0,
        memory: Optional[ObstacleMemory] = None,
    ) -> None:
        self.name = name
        self.width = int(width)
        self.height = int(height)
        self.cell_size = int(cell_size)
        self.base_cost = base_cost.astype(np.float32)
        self.terrain_class = terrain_class.astype(np.uint8)
        self.slope_map = slope_map.astype(np.float32)
        self.map_rgb_small = map_rgb_small.astype(np.uint8) if map_rgb_small is not None else None
        self.source_image = source_image
        self.px_per_sec = float(px_per_sec)
        self.memory = memory or ObstacleMemory()

    @property
    def gh(self) -> int:
        return int(self.base_cost.shape[0])

    @property
    def gw(self) -> int:
        return int(self.base_cost.shape[1])

    def px_to_cell(self, x: float, y: float) -> Tuple[int, int]:
        cx = max(0, min(self.gw - 1, int(x // self.cell_size)))
        cy = max(0, min(self.gh - 1, int(y // self.cell_size)))
        return cx, cy

    def cell_to_px(self, cx: int, cy: int) -> Tuple[float, float]:
        return (cx + 0.5) * self.cell_size, (cy + 0.5) * self.cell_size

    @classmethod
    def bake_from_image(
        cls,
        img: Image.Image,
        name: str = "sprout_valley",
        cell_size: int = 6,
        costs: Optional[Dict[str, float]] = None,
        source_image: str = "",
        px_per_sec: float = 34.0,
    ) -> "CostMap25D":
        """Bake a 1920x1080 world-map screenshot into a 2.5D semantic costmap."""
        cfg = dict(DEFAULT_COSTS)
        if costs:
            cfg.update(costs)

        arr = _strip_debug_gridlines(vision._norm_arr(img))
        h, w = arr.shape[:2]
        r = arr[:, :, 0].astype(np.int16)
        g = arr[:, :, 1].astype(np.int16)
        b = arr[:, :, 2].astype(np.int16)

        # 1. Semantic pixel masks calibrated on real Azur Promilia world maps
        ui_mask = np.zeros((h, w), dtype=bool)
        for x1, y1, x2, y2 in MAP_UI_BOXES:
            ui_mask[y1:y2, x1:x2] = True

        # Unexplored fog (very dark near-black interior)
        fog_mask = (r < 44) & (g < 44) & (b < 44) & (~ui_mask)
        fog_mask = ndimage.binary_closing(fog_mask, iterations=3)

        # Water bodies: deep sea, rivers, light-blue lakes (cyan/blue dominant over red)
        water_mask = (b > r + 24) & (g > r + 12) & (b > 72) & (~fog_mask) & (~ui_mask)
        water_mask = ndimage.binary_opening(water_mask, iterations=1)
        water_mask = ndimage.binary_closing(water_mask, iterations=2)

        # Red World Boss markers (e.g. Lv.30 铁鬃霸主, Lv.35 角斗斯) -> expand safety bubble
        boss_core = (r > 180) & ((r - g) > 70) & ((r - b) > 70) & (~ui_mask)
        boss_core = ndimage.binary_opening(boss_core, iterations=1)
        # Keep only clusters large enough to be real boss icons (>= 60 px)
        lbl, n_lbl = ndimage.label(boss_core)
        boss_filtered = np.zeros_like(boss_core)
        if n_lbl > 0:
            counts = ndimage.sum(boss_core, lbl, range(1, n_lbl + 1))
            for idx, cnt in enumerate(counts, start=1):
                if cnt >= 60:
                    boss_filtered |= lbl == idx
        boss_mask = ndimage.binary_dilation(boss_filtered, iterations=26)

        # Dirt / stone roads: warm pale beige/cream (mean RGB ~ 164, 163, 107)
        road_mask = (
            (r > 132)
            & (g > 132)
            & (b > 72)
            & ((r - b) > 16)
            & ((g - b) > 16)
            & (np.abs(r - g) < 32)
            & (~water_mask)
            & (~fog_mask)
            & (~boss_mask)
            & (~ui_mask)
        )
        road_mask = ndimage.binary_closing(road_mask, iterations=1)

        # Graystone ruins / masonry walls (low-saturation mid-graystone clusters)
        spread = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
        mean_luma = (r + g + b) / 3.0
        ruins_mask = (
            (spread < 24)
            & (mean_luma > 75)
            & (mean_luma < 175)
            & (~road_mask)
            & (~water_mask)
            & (~fog_mask)
            & (~ui_mask)
        )
        ruins_mask = ndimage.binary_opening(ruins_mask, iterations=1)

        # 2. 2.5D topographic contour-line density (proxy for terrain slope/steep cliffs)
        # Contour lines on green terrain create crisp local intensity edges in the green channel.
        g_smooth = ndimage.gaussian_filter(arr[:, :, 1].astype(np.float32), sigma=0.9)
        gx = ndimage.sobel(g_smooth, axis=1)
        gy = ndimage.sobel(g_smooth, axis=0)
        grad_mag = np.hypot(gx, gy)
        # Only count contour edges on non-water, non-road, non-fog, non-UI terrain
        terrain_only = (~water_mask) & (~road_mask) & (~fog_mask) & (~ui_mask)
        contour_edges = ((grad_mag > 18.0) & (grad_mag < 95.0) & terrain_only).astype(np.float32)
        slope_density = ndimage.uniform_filter(contour_edges, size=19)
        # Normalize contour density to [0, 1] (empirically ~0.04 on flat grass, ~0.28+ on cliff bands)
        slope_norm = np.clip((slope_density - 0.05) / 0.25, 0.0, 1.0)

        # 3. Downsample masks & slope to navigation grid
        cs = int(cell_size)
        ui_g = _downsample_mean(ui_mask.astype(np.float32), cs) > 0.35
        fog_g = _downsample_mean(fog_mask.astype(np.float32), cs) > 0.50
        water_g = _downsample_mean(water_mask.astype(np.float32), cs) > 0.28
        boss_g = _downsample_mean(boss_mask.astype(np.float32), cs) > 0.20
        road_g = _downsample_mean(road_mask.astype(np.float32), cs) > 0.16
        ruins_g = _downsample_mean(ruins_mask.astype(np.float32), cs) > 0.32
        slope_g = _downsample_mean(slope_norm, cs).astype(np.float32)

        # Inflate water shoreline by 1 cell so ground paths hug dry land instead of slipping into rivers
        water_margin = ndimage.binary_dilation(water_g, iterations=1) & (~water_g) & (~road_g)

        gh, gw = water_g.shape
        tclass = np.full((gh, gw), TERRAIN_GRASS, dtype=np.uint8)
        base = np.full((gh, gw), cfg["grass"], dtype=np.float32)

        # Add continuous 2.5D slope cost to grass cells
        base += slope_g * cfg["slope_max_extra"]
        tclass[slope_g > 0.45] = TERRAIN_SLOPE

        # Apply semantic precedence (roads beat slope/margins; water/boss/ui beat grass)
        base[water_margin] += 4.5
        base[ruins_g] = cfg["ruins"]
        tclass[ruins_g] = TERRAIN_RUINS

        base[fog_g] = cfg["fog"]
        tclass[fog_g] = TERRAIN_FOG

        base[road_g] = cfg["road"]
        tclass[road_g] = TERRAIN_ROAD

        base[water_g] = cfg["water"]
        tclass[water_g] = TERRAIN_WATER

        base[boss_g] = np.maximum(base[boss_g], cfg["boss"])
        tclass[boss_g] = TERRAIN_BOSS

        base[ui_g] = cfg["ui"]
        tclass[ui_g] = TERRAIN_UI

        # Store a compact 1/2-scale RGB reference image (960x540) for fast minimap localization
        small_rgb = np.asarray(Image.fromarray(arr).resize((w // 2, h // 2), Image.Resampling.BILINEAR), dtype=np.uint8)

        return cls(
            name=name,
            width=w,
            height=h,
            cell_size=cs,
            base_cost=base,
            terrain_class=tclass,
            slope_map=slope_g,
            map_rgb_small=small_rgb,
            source_image=source_image,
            px_per_sec=px_per_sec,
        )

    def effective_cost_grid(self, mode: str = "ground") -> np.ndarray:
        """Combine static 2.5D base costs with live ObstacleMemory (ground vs fly layer)."""
        if mode == "fly":
            # In flight mode, water/slopes/ground ruins are freely traversable; only avoid UI & boss bubbles
            grid = np.full_like(self.base_cost, 1.0, dtype=np.float32)
            grid[self.terrain_class == TERRAIN_FOG] = 1.6
            grid[self.terrain_class == TERRAIN_BOSS] = 18.0
            grid[self.terrain_class == TERRAIN_UI] = 50.0
            return grid

        grid = self.base_cost.copy()
        yy, xx = np.ogrid[0 : self.gh, 0 : self.gw]
        cs = float(self.cell_size)

        # Apply proven passable trail discounts first
        for tr in self.memory.trails:
            tcx = tr.x / cs
            tcy = tr.y / cs
            r_cells = max(1.0, tr.radius / cs)
            d2 = (xx - tcx) ** 2 + (yy - tcy) ** 2
            mask = d2 <= r_cells * r_cells
            if np.any(mask):
                # Never discount water/boss/UI into free ground
                safe = mask & (self.terrain_class != TERRAIN_WATER) & (self.terrain_class != TERRAIN_BOSS) & (self.terrain_class != TERRAIN_UI)
                grid[safe] = np.maximum(0.85, grid[safe] * float(tr.discount))

        # Stamp learned online obstacles with smooth radial falloff
        for obs in self.memory.obstacles:
            ocx = obs.x / cs
            ocy = obs.y / cs
            r_cells = max(1.5, obs.radius / cs)
            d2 = (xx - ocx) ** 2 + (yy - ocy) ** 2
            mask = d2 <= r_cells * r_cells
            if np.any(mask):
                falloff = 1.0 - np.sqrt(d2[mask]) / (r_cells + 1e-6)
                grid[mask] += float(obs.penalty) * (0.35 + 0.65 * falloff)

        return grid

    def find_path(
        self,
        start_xy: Tuple[float, float],
        goal_xy: Tuple[float, float],
        mode: str = "ground",
        smooth: bool = True,
    ) -> Dict[str, object]:
        """Compute an optimal 8-connected A* path (with portal edges and cost-aware string-pulling)."""
        t0 = time.perf_counter()
        grid = self.effective_cost_grid(mode=mode)
        sx, sy = self.px_to_cell(*start_xy)
        gx, gy = self.px_to_cell(*goal_xy)

        if (sx, sy) == (gx, gy):
            return {
                "ok": True,
                "mode": mode,
                "waypoints": [[round(start_xy[0], 1), round(start_xy[1], 1)], [round(goal_xy[0], 1), round(goal_xy[1], 1)]],
                "raw_cells": 1,
                "total_cost": 0.0,
                "segments": [],
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }

        # Index 3D portal edges by source cell (expanded to 3x3 neighborhood so paths easily enter portals)
        portal_adj: Dict[Tuple[int, int], List[Tuple[int, int, float, PortalEdge]]] = {}
        for p in self.memory.portals:
            psx, psy = self.px_to_cell(p.src_x, p.src_y)
            pdx, pdy = self.px_to_cell(p.dst_x, p.dst_y)
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    nx, ny = psx + ox, psy + oy
                    if 0 <= nx < self.gw and 0 <= ny < self.gh:
                        portal_adj.setdefault((nx, ny), []).append((pdx, pdy, float(p.cost), p))

        # 8-neighbor offsets (dx, dy, step_len)
        neighbors = (
            (1, 0, 1.0),
            (-1, 0, 1.0),
            (0, 1, 1.0),
            (0, -1, 1.0),
            (1, 1, 1.41421356),
            (1, -1, 1.41421356),
            (-1, 1, 1.41421356),
            (-1, -1, 1.41421356),
        )
        min_cell_cost = float(np.min(grid))
        gh, gw = self.gh, self.gw

        def h_octile(x: int, y: int) -> float:
            dx = abs(x - gx)
            dy = abs(y - gy)
            return min_cell_cost * (max(dx, dy) + 0.41421356 * min(dx, dy))

        start_node = (sx, sy)
        goal_node = (gx, gy)
        g_score: Dict[Tuple[int, int], float] = {start_node: 0.0}
        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
        used_portal: Dict[Tuple[int, int], PortalEdge] = {}
        open_heap: List[Tuple[float, int, Tuple[int, int]]] = [(h_octile(sx, sy), 0, start_node)]
        closed: set[Tuple[int, int]] = set()
        push_seq = 1
        found = False

        while open_heap:
            _, _, cur = heapq.heappop(open_heap)
            if cur in closed:
                continue
            if cur == goal_node:
                found = True
                break
            closed.add(cur)
            cx, cy = cur
            cur_g = g_score[cur]

            for dx, dy, step_len in neighbors:
                nx, ny = cx + dx, cy + dy
                if nx < 0 or nx >= gw or ny < 0 or ny >= gh:
                    continue
                nxt = (nx, ny)
                if nxt in closed:
                    continue
                step_cost = step_len * 0.5 * (float(grid[cy, cx]) + float(grid[ny, nx]))
                ng = cur_g + step_cost
                if ng < g_score.get(nxt, 1e18):
                    g_score[nxt] = ng
                    came_from[nxt] = cur
                    if nxt in used_portal:
                        del used_portal[nxt]
                    heapq.heappush(open_heap, (ng + h_octile(nx, ny), push_seq, nxt))
                    push_seq += 1

            # Expand directed 3D portal edges (jump / mushroom / fly / teleport)
            if cur in portal_adj:
                for pdx, pdy, pcost, pedge in portal_adj[cur]:
                    nxt = (pdx, pdy)
                    if nxt in closed:
                        continue
                    ng = cur_g + pcost
                    if ng < g_score.get(nxt, 1e18):
                        g_score[nxt] = ng
                        came_from[nxt] = cur
                        used_portal[nxt] = pedge
                        heapq.heappush(open_heap, (ng + h_octile(pdx, pdy), push_seq, nxt))
                        push_seq += 1

        if not found:
            return {
                "ok": False,
                "mode": mode,
                "error": "no_path",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }

        # Reconstruct cell path
        raw_cells: List[Tuple[int, int]] = []
        node = goal_node
        while node != start_node:
            raw_cells.append(node)
            node = came_from[node]
        raw_cells.append(start_node)
        raw_cells.reverse()

        # Identify portal indices so string-pulling never deletes a portal jump
        portal_indices: set[int] = set()
        portal_map: Dict[int, PortalEdge] = {}
        for idx in range(1, len(raw_cells)):
            if raw_cells[idx] in used_portal:
                portal_indices.add(idx - 1)
                portal_indices.add(idx)
                portal_map[idx] = used_portal[raw_cells[idx]]

        if smooth and len(raw_cells) > 2:
            kept_indices = self._smooth_cell_indices(raw_cells, grid, portal_indices)
        else:
            kept_indices = list(range(len(raw_cells)))

        waypoints: List[List[float]] = []
        actions_per_seg: List[str] = []
        for k_pos, idx in enumerate(kept_indices):
            if idx == 0:
                wx, wy = start_xy
            elif idx == len(raw_cells) - 1:
                wx, wy = goal_xy
            else:
                wx, wy = self.cell_to_px(*raw_cells[idx])
            waypoints.append([round(float(wx), 1), round(float(wy), 1)])
            if k_pos > 0:
                act = portal_map[idx].action if idx in portal_map else ("fly" if mode == "fly" else "move")
                actions_per_seg.append(act)

        segments = self.waypoints_to_segments(waypoints, actions_per_seg)
        return {
            "ok": True,
            "mode": mode,
            "waypoints": waypoints,
            "raw_cells": len(raw_cells),
            "total_cost": round(float(g_score[goal_node]), 2),
            "total_px": round(sum(s["dist_px"] for s in segments), 1),
            "est_sec": round(sum(s["est_sec"] for s in segments), 2),
            "segments": segments,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }

    def _line_cost_ok(
        self,
        a: Tuple[int, int],
        b: Tuple[int, int],
        grid: np.ndarray,
        orig_segment_cost: float,
    ) -> bool:
        """Check whether a straight line from cell a -> b has cost <= 1.06 * orig_segment_cost and hits no spikes."""
        x0, y0 = a
        x1, y1 = b
        steps = max(abs(x1 - x0), abs(y1 - y0))
        if steps <= 1:
            return True
        xs = np.linspace(x0, x1, steps + 1)
        ys = np.linspace(y0, y1, steps + 1)
        xi = np.clip(np.round(xs).astype(int), 0, self.gw - 1)
        yi = np.clip(np.round(ys).astype(int), 0, self.gh - 1)
        vals = grid[yi, xi]
        # Reject shortcuts crossing any water/boss/UI/obstacle cell spike
        if float(vals.max()) > max(float(grid[y0, x0]), float(grid[y1, x1]), 5.5) * 1.35 and float(vals.max()) >= 8.5:
            return False
        step_dist = math.hypot(x1 - x0, y1 - y0) / steps
        line_cost = float(np.sum(0.5 * (vals[:-1] + vals[1:]) * step_dist))
        # Allow a gentle smoothing budget so gentle curving roads don't shatter into dozens of tiny micro-legs
        return line_cost <= orig_segment_cost * 1.12 + 0.65

    def _smooth_cell_indices(
        self,
        raw_cells: List[Tuple[int, int]],
        grid: np.ndarray,
        protected: set[int],
    ) -> List[int]:
        # Precompute cumulative cost along raw_cells
        cum = [0.0]
        for i in range(1, len(raw_cells)):
            x0, y0 = raw_cells[i - 1]
            x1, y1 = raw_cells[i]
            d = math.hypot(x1 - x0, y1 - y0)
            cum.append(cum[-1] + d * 0.5 * (float(grid[y0, x0]) + float(grid[y1, x1])))

        kept = [0]
        i = 0
        n = len(raw_cells)
        while i < n - 1:
            if i in protected and (i + 1) in protected:
                kept.append(i + 1)
                i += 1
                continue
            # Limit single smoothed leg length to ~180 map px (~30 cells) for reliable compass tracking
            max_j = min(n - 1, i + 30)
            best_j = i + 1
            for j in range(max_j, i + 1, -1):
                # Do not skip over a protected portal node
                if any(p in protected for p in range(i + 1, j)):
                    continue
                if self._line_cost_ok(raw_cells[i], raw_cells[j], grid, cum[j] - cum[i]):
                    best_j = j
                    break
            kept.append(best_j)
            i = best_j
        return kept

    def waypoints_to_segments(
        self,
        waypoints: Sequence[Sequence[float]],
        actions_per_seg: Optional[Sequence[str]] = None,
        min_leg_px: float = 24.0,
    ) -> List[Dict[str, object]]:
        # Merge consecutive tiny non-portal road bends (< min_leg_px with turn <= 50 deg) so nav-goto
        # doesn't waste leg budgets on 5-15px zigzags.
        pts = [[float(p[0]), float(p[1])] for p in waypoints]
        acts = [
            (actions_per_seg[i] if (actions_per_seg and i < len(actions_per_seg)) else "move")
            for i in range(max(0, len(pts) - 1))
        ]
        if len(pts) >= 3 and min_leg_px > 0:
            merged_pts = [pts[0]]
            merged_acts: List[str] = []
            i = 0
            while i < len(acts):
                cur_a = acts[i]
                p0 = merged_pts[-1]
                p1 = pts[i + 1]
                d1 = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
                if (
                    d1 < min_leg_px
                    and i + 1 < len(acts)
                    and cur_a == acts[i + 1]
                    and cur_a in ("move", "fly")
                ):
                    p2 = pts[i + 2]
                    b1 = (math.degrees(math.atan2(p1[0] - p0[0], -(p1[1] - p0[1]))) + 360.0) % 360.0
                    b2 = (math.degrees(math.atan2(p2[0] - p1[0], -(p2[1] - p1[1]))) + 360.0) % 360.0
                    diff = abs((b2 - b1 + 180.0) % 360.0 - 180.0)
                    if diff <= 52.0:
                        # Skip intermediate micro-bend p1
                        i += 1
                        continue
                merged_pts.append(p1)
                merged_acts.append(cur_a)
                i += 1
            pts = merged_pts
            acts = merged_acts

        segs: List[Dict[str, object]] = []
        for idx in range(len(pts) - 1):
            x0, y0 = pts[idx]
            x1, y1 = pts[idx + 1]
            dx = x1 - x0
            dy = y1 - y0
            dist = math.hypot(dx, dy)
            bearing = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
            act = acts[idx] if idx < len(acts) else "move"
            est_sec = max(0.5, dist / max(1.0, self.px_per_sec))
            segs.append(
                {
                    "from": [round(x0, 1), round(y0, 1)],
                    "to": [round(x1, 1), round(y1, 1)],
                    "dist_px": round(dist, 1),
                    "bearing_deg": round(bearing, 1),
                    "est_sec": round(est_sec, 2),
                    "action": act,
                }
            )
        return segs

    def localize_minimap(
        self,
        world_frame: Image.Image,
        hint_xy: Optional[Tuple[float, float]] = None,
        search_radius_px: float = 240.0,
        scale_candidates: Sequence[float] = (0.38, 0.45, 0.55, 0.65, 0.78, 0.92),
    ) -> Dict[str, object]:
        """Estimate player (x, y) on this world map by matching the circular minimap terrain.

        Masks out the central player arrow and bright white POI icons so icons do not distort
        correlation, and matches on normalized RGB terrain structure at half resolution.
        """
        if self.map_rgb_small is None:
            return {"ok": False, "error": "map_rgb_small_missing"}

        arr = vision._norm_arr(world_frame)
        # Extract minimap terrain square centered at (MM_CX, MM_CY)
        r_mm = 64
        mm = arr[
            nav_mod.MM_CY - r_mm : nav_mod.MM_CY + r_mm,
            nav_mod.MM_CX - r_mm : nav_mod.MM_CX + r_mm,
        ].astype(np.float32)

        # Build circular validity weight excluding center player arrow (r<18) and bright white POIs
        yy, xx = np.ogrid[-r_mm:r_mm, -r_mm:r_mm]
        d2 = xx * xx + yy * yy
        ring = (d2 <= (r_mm - 4) ** 2) & (d2 >= 18 ** 2)
        white_icon = (mm[:, :, 0] > 220) & (mm[:, :, 1] > 220) & (mm[:, :, 2] > 220)
        valid_base = ring & (~white_icon)

        # Search ROI on 1/2-scale world map (960x540)
        mh, mw = self.map_rgb_small.shape[:2]
        if hint_xy is not None:
            hx, hy = hint_xy[0] * 0.5, hint_xy[1] * 0.5
            rad = search_radius_px * 0.5
            x1 = max(0, int(hx - rad))
            y1 = max(0, int(hy - rad))
            x2 = min(mw, int(hx + rad))
            y2 = min(mh, int(hy + rad))
        else:
            x1, y1, x2, y2 = 0, 0, mw, mh

        search_rgb = self.map_rgb_small[y1:y2, x1:x2].astype(np.float32)
        # Use green + blue channels (strongest land/road/water contrast, invariant to golden icons)
        s_feat = 0.55 * search_rgb[:, :, 1] + 0.45 * search_rgb[:, :, 2]
        s_feat = s_feat - ndimage.uniform_filter(s_feat, size=25)
        s_sq = s_feat * s_feat

        # Detect whether minimap itself is mostly dark unexplored fog
        mm_dark_ratio = float(((mm[:, :, 0] < 45) & (mm[:, :, 1] < 45) & (mm[:, :, 2] < 45))[ring].mean())
        exclude_fog = mm_dark_ratio < 0.45

        best_score = -1.0
        best_xy = (0.0, 0.0)
        best_scale = 0.55

        for sc in scale_candidates:
            ts = max(24, int(round(mm.shape[0] * sc)))
            if ts >= search_rgb.shape[0] - 4 or ts >= search_rgb.shape[1] - 4:
                continue
            tpl_rgb = np.asarray(
                Image.fromarray(mm.astype(np.uint8)).resize((ts, ts), Image.Resampling.BILINEAR),
                dtype=np.float32,
            )
            mask = np.asarray(
                Image.fromarray((valid_base.astype(np.uint8) * 255)).resize((ts, ts), Image.Resampling.NEAREST),
                dtype=np.float32,
            ) > 127.0
            if mask.sum() < 80:
                continue
            t_feat = 0.55 * tpl_rgb[:, :, 1] + 0.45 * tpl_rgb[:, :, 2]
            t_mean = float(t_feat[mask].mean())
            t_zero = np.where(mask, t_feat - t_mean, 0.0)
            t_norm = float(np.sqrt(np.sum(t_zero * t_zero))) + 1e-6

            num = signal.fftconvolve(s_feat, t_zero[::-1, ::-1], mode="valid")
            den = np.sqrt(np.maximum(1e-6, signal.fftconvolve(s_sq, mask.astype(np.float32)[::-1, ::-1], mode="valid"))) * t_norm
            ncc = num / den
            # Mask out candidate centers landing inside UI overlays or unexplored black fog
            nh, nw = ncc.shape
            cxs = np.clip(((x1 + np.arange(nw) + ts * 0.5) * 2.0 / self.cell_size).astype(np.int32), 0, self.gw - 1)
            cys = np.clip(((y1 + np.arange(nh) + ts * 0.5) * 2.0 / self.cell_size).astype(np.int32), 0, self.gh - 1)
            tc_grid = self.terrain_class[np.ix_(cys, cxs)]
            invalid = tc_grid == TERRAIN_UI
            if exclude_fog:
                invalid = invalid | (tc_grid == TERRAIN_FOG)
            if not np.all(invalid):
                ncc = np.where(invalid, -1.0, ncc)
            idx = int(np.argmax(ncc))
            py, px = divmod(idx, ncc.shape[1])
            score = float(ncc[py, px])
            if score > best_score:
                best_score = score
                best_scale = float(sc)
                # Center in full 1920x1080 coordinates
                cx_full = (x1 + px + ts * 0.5) * 2.0
                cy_full = (y1 + py + ts * 0.5) * 2.0
                best_xy = (cx_full, cy_full)

        return {
            "ok": best_score > 0.25,
            "x": round(best_xy[0], 1),
            "y": round(best_xy[1], 1),
            "confidence": round(best_score, 3),
            "matched_scale": best_scale,
        }

    def render_debug_overlay(
        self,
        base_image: Optional[Image.Image] = None,
        path_result: Optional[Dict[str, object]] = None,
        mode: str = "ground",
        out_path: Optional[str] = None,
    ) -> Image.Image:
        """Render color-coded 2.5D costmap + obstacle memory + optional A* path onto an image."""
        if base_image is not None:
            canvas = Image.fromarray(vision._norm_arr(base_image)).convert("RGBA")
        elif self.map_rgb_small is not None:
            canvas = Image.fromarray(self.map_rgb_small).resize((self.width, self.height), Image.Resampling.BILINEAR).convert("RGBA")
        else:
            canvas = Image.new("RGBA", (self.width, self.height), (20, 24, 28, 255))

        # Build RGBA terrain overlay at grid resolution then upscale cleanly
        ov = np.zeros((self.gh, self.gw, 4), dtype=np.uint8)
        eff = self.effective_cost_grid(mode=mode)

        ov[self.terrain_class == TERRAIN_ROAD] = (120, 255, 210, 95)     # Mint cyan = fast road
        ov[self.terrain_class == TERRAIN_SLOPE] = (255, 165, 40, 105)    # Orange = steep contour slope
        ov[self.terrain_class == TERRAIN_RUINS] = (185, 130, 255, 115)   # Purple = ruins/walls
        ov[self.terrain_class == TERRAIN_WATER] = (30, 125, 255, 125)    # Blue = water
        ov[self.terrain_class == TERRAIN_BOSS] = (255, 45, 55, 135)      # Red = boss danger zone
        ov[self.terrain_class == TERRAIN_FOG] = (60, 60, 70, 80)         # Dim gray = fog
        # Highlight learned online obstacle delta in vivid magenta
        delta = eff - self.base_cost
        ov[delta > 3.0] = (255, 40, 220, 175)

        ov_img = Image.fromarray(ov, mode="RGBA").resize((self.width, self.height), Image.Resampling.NEAREST)
        canvas = Image.alpha_composite(canvas, ov_img)
        draw = ImageDraw.Draw(canvas)

        # Draw learned trails (lime) and obstacles (magenta rings) and portals (cyan arrows)
        for tr in self.memory.trails:
            r = tr.radius
            draw.ellipse([tr.x - r, tr.y - r, tr.x + r, tr.y + r], outline=(100, 255, 100, 200), width=2)
        for obs in self.memory.obstacles:
            r = obs.radius
            draw.ellipse([obs.x - r, obs.y - r, obs.x + r, obs.y + r], outline=(255, 40, 220, 240), width=3)
        for p in self.memory.portals:
            draw.line([p.src_x, p.src_y, p.dst_x, p.dst_y], fill=(0, 240, 255, 240), width=3)

        # Draw planned A* path
        if path_result and path_result.get("ok") and path_result.get("waypoints"):
            wps = path_result["waypoints"]
            pts = [(float(p[0]), float(p[1])) for p in wps]  # type: ignore[index]
            if len(pts) >= 2:
                draw.line(pts, fill=(255, 245, 40, 255), width=5)
            for idx, (wx, wy) in enumerate(pts):
                col = (50, 255, 90, 255) if idx == 0 else ((255, 70, 70, 255) if idx == len(pts) - 1 else (255, 235, 60, 255))
                rad = 8 if idx in (0, len(pts) - 1) else 5
                draw.ellipse([wx - rad, wy - rad, wx + rad, wy + rad], fill=col, outline=(15, 15, 15, 255), width=2)

        rgb = canvas.convert("RGB")
        if out_path:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            rgb.save(out_path)
        return rgb

    def save(self, cache_dir: str = DEFAULT_CACHE_DIR) -> Dict[str, str]:
        os.makedirs(cache_dir, exist_ok=True)
        npz_path = os.path.join(cache_dir, f"{self.name}.npz")
        meta_path = os.path.join(cache_dir, f"{self.name}.json")
        mem_path = os.path.join(cache_dir, f"{self.name}_memory.json")

        arrays = {
            "base_cost": self.base_cost,
            "terrain_class": self.terrain_class,
            "slope_map": self.slope_map,
        }
        if self.map_rgb_small is not None:
            arrays["map_rgb_small"] = self.map_rgb_small
        np.savez_compressed(npz_path, **arrays)

        meta = {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "cell_size": self.cell_size,
            "grid_shape": [self.gh, self.gw],
            "source_image": self.source_image,
            "px_per_sec": self.px_per_sec,
            "stats": self.terrain_stats(),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        self.memory.save(mem_path)
        return {"npz": npz_path, "meta": meta_path, "memory": mem_path}

    @classmethod
    def load(cls, name: str, cache_dir: str = DEFAULT_CACHE_DIR) -> "CostMap25D":
        npz_path = os.path.join(cache_dir, f"{name}.npz")
        meta_path = os.path.join(cache_dir, f"{name}.json")
        mem_path = os.path.join(cache_dir, f"{name}_memory.json")
        if not os.path.isfile(npz_path) or not os.path.isfile(meta_path):
            raise FileNotFoundError(f"Baked costmap {name!r} not found in {cache_dir}. Run `azp.py nav-bake` first.")
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        data = np.load(npz_path)
        mem = ObstacleMemory.load(mem_path)
        return cls(
            name=meta["name"],
            width=meta["width"],
            height=meta["height"],
            cell_size=meta["cell_size"],
            base_cost=data["base_cost"],
            terrain_class=data["terrain_class"],
            slope_map=data["slope_map"],
            map_rgb_small=data["map_rgb_small"] if "map_rgb_small" in data else None,
            source_image=meta.get("source_image", ""),
            px_per_sec=meta.get("px_per_sec", 34.0),
            memory=mem,
        )

    def terrain_stats(self) -> Dict[str, float]:
        total = float(self.terrain_class.size)
        names = {
            "road_pct": TERRAIN_ROAD,
            "grass_pct": TERRAIN_GRASS,
            "slope_pct": TERRAIN_SLOPE,
            "ruins_pct": TERRAIN_RUINS,
            "water_pct": TERRAIN_WATER,
            "boss_pct": TERRAIN_BOSS,
            "fog_pct": TERRAIN_FOG,
            "ui_pct": TERRAIN_UI,
        }
        return {k: round(100.0 * float((self.terrain_class == code).sum()) / total, 2) for k, code in names.items()}


def navigate_with_costmap(
    cmap: CostMap25D,
    goal_xy: Tuple[float, float],
    start_xy: Optional[Tuple[float, float]] = None,
    mode: str = "ground",
    max_legs: int = 12,
    arrive_px: float = 28.0,
    sprint: bool = True,
    spam_f: bool = True,
    mount: bool = False,
    cache_dir: str = DEFAULT_CACHE_DIR,
    abort_fn=None,
) -> Dict[str, object]:
    """Closed-loop A* navigator with online obstacle learning & automatic replanning."""
    from . import actions as act_mod

    gw = find_game_window()
    cur_xy = start_xy
    if cur_xy is None:
        img0 = capture_client(gw=gw)
        loc = cmap.localize_minimap(img0)
        if not loc["ok"]:
            return {"ok": False, "error": "initial_localization_failed", "localization": loc}
        cur_xy = (float(loc["x"]), float(loc["y"]))

    # Wolf mount (~速 800) travels ~1.75x faster than on-foot sprint
    eff_px_per_sec = cmap.px_per_sec * (1.75 if mount else 1.0)
    mounted = False
    if mount:
        act_mod.toggle_mount()
        mounted = True

    history: List[Dict[str, object]] = []
    arrived = False
    obstacles_added = 0

    try:
        for leg_idx in range(max_legs):
            if abort_fn is not None and abort_fn():
                break
            dist_goal = math.hypot(goal_xy[0] - cur_xy[0], goal_xy[1] - cur_xy[1])
            if dist_goal <= arrive_px:
                arrived = True
                break

            plan = cmap.find_path(cur_xy, goal_xy, mode=mode, smooth=True)
            if not plan.get("ok") or not plan.get("segments"):
                return {"ok": False, "error": "path_failed", "cur_xy": list(cur_xy), "history": history}

            seg = plan["segments"][0]  # type: ignore[index]
            bearing = float(seg["bearing_deg"])
            seg_sec = float(seg["dist_px"]) / max(1.0, eff_px_per_sec)
            walk_sec = min(6.5, max(0.75, seg_sec))

            res = nav_mod.approach_poi(
                target_bearing=bearing,
                walk_sec=walk_sec,
                sprint=sprint,
                spam_f=spam_f and (not mounted),
                unstuck=True,
                abort_fn=abort_fn,
            )
            if res.get("aborted"):
                history.append({"leg": leg_idx, "aborted": True, "seg": seg})
                break

            # Predict new position & refine via local minimap match
            rad = math.radians(bearing)
            pred_x = cur_xy[0] + math.sin(rad) * min(float(seg["dist_px"]), walk_sec * eff_px_per_sec)
            pred_y = cur_xy[1] - math.cos(rad) * min(float(seg["dist_px"]), walk_sec * eff_px_per_sec)

            img_after = capture_client(gw=gw)
            hud_after = res.get("hud_after") or vision.check_hud_state(img_after)
            search_rad = max(72.0, min(180.0, walk_sec * eff_px_per_sec * 1.45))
            max_phys_jump = max(55.0, walk_sec * eff_px_per_sec * 1.75)
            loc_after = cmap.localize_minimap(img_after, hint_xy=(pred_x, pred_y), search_radius_px=search_rad)
            loc_jump = (
                math.hypot(float(loc_after["x"]) - cur_xy[0], float(loc_after["y"]) - cur_xy[1])
                if loc_after.get("ok")
                else 1e9
            )
            if loc_after["ok"] and float(loc_after["confidence"]) >= 0.46 and loc_jump <= max_phys_jump:
                new_xy = (float(loc_after["x"]), float(loc_after["y"]))
            else:
                # Fallback to dead-reckoning (discounted if unstuck fired)
                scale = 0.35 if int(res.get("unstuck_count", 0)) > 0 else 0.85
                new_xy = (
                    cur_xy[0] + (pred_x - cur_xy[0]) * scale,
                    cur_xy[1] + (pred_y - cur_xy[1]) * scale,
                )

            moved_px = math.hypot(new_xy[0] - cur_xy[0], new_xy[1] - cur_xy[1])
            snagged = int(res.get("unstuck_count", 0)) > 0 or (walk_sec >= 1.5 and moved_px < 12.0)

            if snagged:
                # Stamp obstacle slightly ahead of where we stalled so next A* leg routes cleanly around it
                obs_x = new_xy[0] + math.sin(rad) * 18.0
                obs_y = new_xy[1] - math.cos(rad) * 18.0
                cmap.memory.record_obstacle(obs_x, obs_y, radius=26.0, penalty=20.0, reason="nav_stuck")
                obstacles_added += 1
            elif moved_px >= 14.0:
                # Reinforce proven passable midpoint & endpoint
                cmap.memory.record_passable((cur_xy[0] + new_xy[0]) * 0.5, (cur_xy[1] + new_xy[1]) * 0.5)
                cmap.memory.record_passable(new_xy[0], new_xy[1])

            history.append(
                {
                    "leg": leg_idx,
                    "from": [round(cur_xy[0], 1), round(cur_xy[1], 1)],
                    "to": [round(new_xy[0], 1), round(new_xy[1], 1)],
                    "target_seg": seg,
                    "moved_px": round(moved_px, 1),
                    "snagged": snagged,
                    "loc_conf": loc_after.get("confidence"),
                }
            )
            cur_xy = new_xy

            if not hud_after.get("in_world_hud", True):
                # Triggered repair dialog / tutorial / revive / cutscene — stop safely and classify anomaly
                try:
                    from . import ocr_tpl

                    history[-1]["anomaly"] = ocr_tpl.detect_screen_anomaly(img_after, use_ocr=ocr_tpl.is_ocr_available())
                except Exception:
                    pass
                break
    finally:
        if mounted:
            act_mod.toggle_mount()

    # Check final arrival after completing the last leg
    final_dist = math.hypot(goal_xy[0] - cur_xy[0], goal_xy[1] - cur_xy[1])
    if final_dist <= arrive_px:
        arrived = True

    # Persist any learned obstacles & proven trails
    mem_path = os.path.join(cache_dir, f"{cmap.name}_memory.json")
    cmap.memory.save(mem_path)

    return {
        "ok": arrived,
        "arrived": arrived,
        "mount": mount,
        "final_xy": [round(cur_xy[0], 1), round(cur_xy[1], 1)],
        "dist_to_goal_px": round(final_dist, 1),
        "legs_run": len(history),
        "obstacles_added": obstacles_added,
        "memory_path": mem_path,
        "history": history,
    }
