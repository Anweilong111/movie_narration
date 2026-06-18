from __future__ import annotations

from pathlib import Path

from app.models import Scene
from app.utils.json_utils import save_json


def build_scene_grids(
    scenes: list[Scene],
    output_dir: str,
    enabled: bool = True,
    rows: int = 3,
    cols: int = 3,
) -> list[Scene]:
    if not enabled:
        return scenes
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for scene in scenes:
        result = build_scene_grid(scene, str(out / f'scene_{scene.scene_id:03d}_grid.jpg'), rows=rows, cols=cols)
        if result:
            grid_path, grid_times = result
            scene.grid_image_path = grid_path
            scene.grid_frame_times = grid_times
            index.append({'scene_id': scene.scene_id, 'grid_image_path': grid_path, 'grid_frame_times': grid_times})
    save_json(out / 'index.json', index)
    return scenes


def build_scene_grid(
    scene: Scene,
    output_path: str,
    rows: int = 3,
    cols: int = 3,
    tile_w: int = 426,
    tile_h: int = 240,
    label_h: int = 30,
) -> tuple[str, list[float]] | None:
    if not scene.keyframes:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ModuleNotFoundError as exc:
        raise RuntimeError('Pillow is required to generate scene overview grids') from exc

    limit = max(1, rows * cols)
    indexes = _even_indexes(len(scene.keyframes), limit)
    selected_paths = [scene.keyframes[idx] for idx in indexes]
    selected_times = [scene.keyframe_times[idx] for idx in indexes if idx < len(scene.keyframe_times)]
    if len(selected_times) < len(selected_paths):
        selected_times = [scene.start] * len(selected_paths)

    canvas = Image.new('RGB', (cols * tile_w, rows * (tile_h + label_h)), (16, 16, 16))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for slot, path in enumerate(selected_paths):
        row, col = divmod(slot, cols)
        x = col * tile_w
        y = row * (tile_h + label_h)
        image = _fit_image(Image, Image.open(path).convert('RGB'), tile_w, tile_h)
        canvas.paste(image, (x, y))
        label = f'{slot + 1}  {selected_times[slot]:.1f}s'
        draw.rectangle((x, y + tile_h, x + tile_w, y + tile_h + label_h), fill=(0, 0, 0))
        draw.text((x + 8, y + tile_h + 8), label, fill=(255, 255, 255), font=font)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=92)
    return str(out), [float(round(item, 3)) for item in selected_times]


def _fit_image(image_module, image, tile_w: int, tile_h: int):
    image.thumbnail((tile_w, tile_h))
    background = image_module.new('RGB', (tile_w, tile_h), (0, 0, 0))
    x = (tile_w - image.width) // 2
    y = (tile_h - image.height) // 2
    background.paste(image, (x, y))
    return background


def _even_indexes(length: int, limit: int) -> list[int]:
    if length <= 0:
        return []
    if length <= limit:
        return list(range(length))
    if limit <= 1:
        return [length // 2]
    step = (length - 1) / (limit - 1)
    return [round(i * step) for i in range(limit)]
