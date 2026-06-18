#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser(description='Run TransNetV2 PyTorch backend and write *.scenes.txt.')
    parser.add_argument('video')
    parser.add_argument('--threshold', type=float, default=float(os.environ.get('TRANSNETV2_THRESHOLD', '0.5')))
    parser.add_argument('--device', default=os.environ.get('TRANSNETV2_DEVICE', 'auto'))
    parser.add_argument('--weights', default=os.environ.get('TRANSNETV2_PYTORCH_WEIGHTS'))
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[1]
    source_pkg = project / 'vendor' / 'TransNetV2' / 'inference-pytorch'
    vendor_pkg = project / 'vendor' / 'transnetv2_pytorch_pkg'
    for path in (source_pkg, vendor_pkg):
        if path.exists():
            sys.path.insert(0, str(path))

    try:
        from transnetv2_pytorch import TransNetV2
    except Exception as exc:
        print(f'TransNetV2 PyTorch source/package not available: {exc}', file=sys.stderr)
        return 127

    try:
        import torch
    except Exception as exc:
        print(f'PyTorch is not available: {exc}', file=sys.stderr)
        return 127

    video = Path(args.video).resolve()
    weights = Path(args.weights).expanduser().resolve() if args.weights else source_pkg / 'transnetv2-pytorch-weights.pth'
    if not weights.exists():
        print(f'TransNetV2 PyTorch weights not found: {weights}', file=sys.stderr)
        return 127

    device = _resolve_device(torch, args.device)
    try:
        model = TransNetV2(device=args.device)
        state_dict = _load_state_dict(torch, weights, device)
        model.load_state_dict(state_dict)
    except TypeError:
        # Official source class has no device argument.
        model = TransNetV2()
        state_dict = _load_state_dict(torch, weights, device)
        model.load_state_dict(state_dict)
    model.to(device)
    model.eval()
    with torch.no_grad():
        frames = _extract_frames(video)
        single, many = _predict_frames(torch, model, frames, device)
        ranges = _predictions_to_scenes(single, args.threshold)

    if not ranges:
        print('TransNetV2 PyTorch produced no scenes', file=sys.stderr)
        return 1

    scenes_txt = Path(f'{video}.scenes.txt')
    scenes_txt.write_text('\n'.join(f'{start} {end}' for start, end in ranges) + '\n', encoding='utf-8')
    predictions = np.stack([single, many], axis=1)
    np.savetxt(f'{video}.predictions.txt', predictions, fmt='%.6f')
    print(f'Wrote {len(ranges)} scenes to {scenes_txt}')
    return 0


def _resolve_device(torch, requested: str):
    if requested == 'auto':
        return torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    return torch.device(requested)


def _load_state_dict(torch, weights: Path, device):
    try:
        return torch.load(str(weights), map_location=device, weights_only=True)
    except TypeError:
        return torch.load(str(weights), map_location=device)


def _extract_frames(video: Path) -> np.ndarray:
    proc = subprocess.run(
        [
            'ffmpeg', '-v', 'error',
            '-i', str(video),
            '-s', '48x27',
            '-pix_fmt', 'rgb24',
            '-f', 'rawvideo',
            'pipe:1',
        ],
        capture_output=True,
        check=True,
    )
    data = np.frombuffer(proc.stdout, np.uint8)
    if data.size == 0:
        raise RuntimeError(f'No frames extracted from video: {video}')
    return data.reshape([-1, 27, 48, 3])


def _predict_frames(torch, model, frames: np.ndarray, device):
    no_padded_frames_start = 25
    no_padded_frames_end = 25 + 50 - (len(frames) % 50 if len(frames) % 50 != 0 else 50)
    start_frame = np.expand_dims(frames[0], 0)
    end_frame = np.expand_dims(frames[-1], 0)
    padded = np.concatenate([start_frame] * no_padded_frames_start + [frames] + [end_frame] * no_padded_frames_end, 0)

    predictions = []
    ptr = 0
    while ptr + 100 <= len(padded):
        window = padded[ptr:ptr + 100][np.newaxis]
        ptr += 50
        one_hot, extra = model(torch.from_numpy(window).to(device))
        single = torch.sigmoid(one_hot).detach().cpu().numpy()[0, 25:75, 0]
        many = torch.sigmoid(extra['many_hot']).detach().cpu().numpy()[0, 25:75, 0]
        predictions.append((single, many))
        print(f'\r[TransNetV2 PyTorch] Processing video frames {min(len(predictions) * 50, len(frames))}/{len(frames)}', end='')
    print('')
    single_frame_pred = np.concatenate([single for single, _ in predictions])[:len(frames)]
    all_frame_pred = np.concatenate([many for _, many in predictions])[:len(frames)]
    return single_frame_pred, all_frame_pred


def _predictions_to_scenes(predictions: np.ndarray, threshold: float = 0.5) -> list[tuple[int, int]]:
    binary = (predictions > threshold).astype(np.uint8)
    scenes = []
    t, t_prev, start = -1, 0, 0
    for i, t in enumerate(binary):
        if t_prev == 1 and t == 0:
            start = i
        if t_prev == 0 and t == 1 and i != 0:
            scenes.append((start, i))
        t_prev = t
    if t == 0:
        scenes.append((start, i))
    if not scenes:
        return [(0, len(predictions) - 1)]
    return scenes


if __name__ == '__main__':
    raise SystemExit(main())
