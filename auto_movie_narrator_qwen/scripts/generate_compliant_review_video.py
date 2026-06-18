#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault('APP_WORKDIR', str(PROJECT_DIR / 'workdir'))

from app.config import get_settings
from app.modules.ffmpeg_tools import concat_audios, ffprobe_duration, run_cmd, speedfit_video
from app.modules.renderer import build_tts_instruction
from app.providers.qwen_tts import QwenTTSClient
from app.utils.json_utils import load_json, save_json


WIDTH = 1080
HEIGHT = 1920
FPS = 30
TARGET_DURATION = 300.0
FONT_REGULAR = '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc'
FONT_MEDIUM = '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Medium.ttc'
FONT_BOLD = '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc'


@dataclass
class ReviewSegment:
    segment_id: int
    voiceover: str
    title: str
    focus: str
    visual_kind: str
    bullets: list[str]
    emotion: str = '沉稳'
    speed: str = 'medium'
    pause_after: float = 0.35
    audio_path: str | None = None
    audio_start: float = 0.0
    audio_end: float = 0.0
    actual_duration: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            'segment_id': self.segment_id,
            'voiceover': self.voiceover,
            'subtitle': self.voiceover,
            'title': self.title,
            'focus': self.focus,
            'visual_kind': self.visual_kind,
            'bullets': self.bullets,
            'emotion': self.emotion,
            'speed': self.speed,
            'pause_after': self.pause_after,
            'audio_path': self.audio_path,
            'audio_start': self.audio_start,
            'audio_end': self.audio_end,
            'actual_duration': self.actual_duration,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description='Generate a compliant original review visualization video.')
    parser.add_argument('--source-task', required=True, help='Existing analyzed task directory or task id under workdir.')
    parser.add_argument('--target-duration', type=float, default=TARGET_DURATION)
    parser.add_argument('--task-id', default='')
    args = parser.parse_args()

    settings = get_settings()
    source_task = _resolve_source_task(args.source_task, settings.workdir)
    if not source_task.exists():
        raise FileNotFoundError(source_task)

    task_id = args.task_id or f'xinmigong_compliant_review_5min_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    task_dir = settings.workdir / task_id
    _make_dirs(task_dir)

    source_payload = _load_source_payload(source_task)
    segments = _build_review_script(source_payload)
    save_json(task_dir / 'script' / 'review_script.json', [seg.to_json() for seg in segments])

    _synthesize_review_tts(task_dir, segments)
    save_json(task_dir / 'script' / 'review_script_with_audio.json', [seg.to_json() for seg in segments])

    _render_slides(task_dir, segments, source_payload)
    visual_base = _render_visual_video(task_dir, segments)
    subtitle_ass = _write_vertical_ass(task_dir / 'render' / 'subtitle.ass', segments)
    final_pre = _compose_final(task_dir, visual_base, task_dir / 'tts' / 'voice_full.aac', subtitle_ass)
    final_video = speedfit_video(
        str(final_pre),
        args.target_duration,
        str(task_dir / 'render' / 'final.mp4'),
        video_encoder=settings.ffmpeg_video_encoder,
        tolerance_seconds=0.2,
    )

    manifest = {
        'task_id': task_id,
        'source_task': str(source_task),
        'final_video': final_video,
        'target_duration': args.target_duration,
        'format': 'compliant_original_review_visualization',
        'copyright_safety_notes': [
            '主视觉为自制图解、时间线、人物关系图和主题卡。',
            '未使用原片视频帧、原片音频、原片字幕截图或演员肖像复刻。',
            '内容定位为影评解析，不作为剧情速看或完整替代观影。',
        ],
        'durations': {
            'voice_full': ffprobe_duration(str(task_dir / 'tts' / 'voice_full.aac')),
            'visual_base': ffprobe_duration(str(visual_base)),
            'final': ffprobe_duration(str(final_video)),
        },
        'segments': len(segments),
    }
    save_json(task_dir / 'manifest.json', manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _resolve_source_task(value: str, workdir: Path) -> Path:
    raw = Path(value)
    if raw.exists():
        return raw.resolve()
    return (workdir / value).resolve()


def _make_dirs(task_dir: Path) -> None:
    for name in ('script', 'tts', 'visuals', 'edit', 'render', 'review'):
        (task_dir / name).mkdir(parents=True, exist_ok=True)


def _load_source_payload(source_task: Path) -> dict[str, Any]:
    return {
        'storyline': load_json(source_task / 'analysis' / 'storyline.json', {}),
        'story_events': load_json(source_task / 'analysis' / 'story_events.json', []),
        'style_profile': load_json(source_task / 'analysis' / 'style_profile.json', {}),
        'director_plan': load_json(source_task / 'analysis' / 'director_plan.json', {}),
    }


def _build_review_script(payload: dict[str, Any]) -> list[ReviewSegment]:
    storyline = payload.get('storyline') or {}
    theme = storyline.get('theme') or '熟人社会里的伦理困境、荒诞命运和人性幽暗'
    return [
        ReviewSegment(
            1,
            '这一版不做五分钟看完电影，而是拆开《心迷宫》的叙事机关。它最狠的地方，不是告诉你谁杀了谁，而是让一具无名焦尸，把整个村子的谎言都照出来。接下来我们只谈结构、主题和人物选择，不替代原片的完整观看，画面也只使用自制图解。',
            '不是速看，是结构解析',
            '一具焦尸照出全村谎言',
            'hook',
            ['不复述全片', '分析叙事机关', '主视觉为自制图解'],
            '好奇',
            'medium',
            0.35,
        ),
        ReviewSegment(
            2,
            '这部片的核心设计，可以看成一个空白变量：尸体是谁。不同的人把自己的秘密投到这具尸体上，于是同一个物件，在每条人物线里都变成了完全不同的答案。观众看的不是尸体移动，而是谎言怎样一层一层改写事实，这就是全片的叙事发动机。',
            '核心机关：尸体是空白变量',
            '同一个物件，多套答案',
            'chain',
            ['尸体不是终点', '身份不断错位', '人物秘密被迫显形'],
            '悬疑',
            'medium',
            0.35,
        ),
        ReviewSegment(
            3,
            '先看人物关系。宗耀背后是父子冲突，黄欢背后是逃离困境，白虎背后是敲诈和债务，丽琴背后是婚姻里的暴力。每条线单独看都小，合在一起就成了犯罪网络。影片没有急着解释案情，而是先让这些压力互相碰撞，人物越多，网越紧。',
            '人物关系不是背景，是压力网',
            '每个人都有一根绷紧的线',
            'relation',
            ['宗耀：父子冲突', '黄欢：逃离困境', '白虎：敲诈与债务', '丽琴：婚姻暴力'],
            '铺垫',
            'medium',
            0.35,
        ),
        ReviewSegment(
            4,
            '它的高级之处在于，悬疑不是靠突然吓你，而是靠信息错位。观众知道一点，人物知道一点，村里人又误会一点。所有人都在补全真相，结果越补越偏。这种叙事会让你一直怀疑：眼前的答案，可能只是另一个人的遮羞布。悬疑因此变得更日常，也更扎人。',
            '悬疑来自信息错位',
            '知道一点，误会一点',
            'timeline',
            ['观众信息', '人物信息', '村庄传言', '真相延迟出现'],
            '疑惑',
            'medium',
            0.35,
        ),
        ReviewSegment(
            5,
            '如果按普通犯罪片拍，重点会是追凶。但《心迷宫》真正追问的是：为什么每个人第一反应都不是报警，而是把事情往自己有利的方向解释。这里的犯罪不只是动作，更是一种心理反应：先保住自己，再谈所谓真相。这比单纯反转更刺痛。',
            '它追问的不是凶手',
            '为什么所有人都想先遮住自己',
            'question',
            ['不是谁干的', '而是谁在遮掩', '法理让位给人情'],
            '沉稳',
            'medium',
            0.35,
        ),
        ReviewSegment(
            6,
            '乡村熟人社会，是这部片的第二个主角。这里没有真正的旁观者，谁都认识谁，谁都欠过谁，谁也不想把话说死。于是法律还没进场，人情已经先把真相包住了。很多选择看起来荒唐，其实都来自熟人关系里的面子、债务和亏欠，这让空间本身也成了迷宫。',
            '熟人社会是第二主角',
            '人情先于法律抵达现场',
            'map',
            ['村口传言', '家族面子', '债务压力', '派出所之外的裁判'],
            '压抑',
            'slow',
            0.45,
        ),
        ReviewSegment(
            7,
            '再看黑色幽默。棺材本该让人想到死亡，但在片中，它又像一件可以被交易、转移、利用的道具。荒诞感就来自这里：死人沉默，活人却忙着给它安排身份。越严肃的东西被越现实地使用，讽刺就越冷，也越能说明活人的狼狈。',
            '黑色幽默：棺材成了道具',
            '死人沉默，活人安排身份',
            'coffin',
            ['认尸', '退尸', '借尸', '遮羞'],
            '荒诞',
            'medium',
            0.4,
        ),
        ReviewSegment(
            8,
            '父亲线最沉。村长不是简单的恶人，他代表的是一种扭曲的保护欲：为了儿子，可以把道德、法律和他自己的体面一起烧掉。影片没有替这种父爱辩护，它只是冷冷地展示：当保护变成遮掩，亲情也会长出阴影。越沉默，越残酷。',
            '父爱为什么变得可怕',
            '保护欲越重，真相越黑',
            'cards',
            ['父亲身份', '村长权威', '沉默共谋', '体面崩塌'],
            '沉重',
            'slow',
            0.45,
        ),
        ReviewSegment(
            9,
            '所以这部片最可怕的不是犯罪本身，而是犯罪之后的集体反应。每个人都知道有些地方不对，但只要沉默对自己有利，沉默就会变成新的秩序。等所有人都选择少说一句，真相就不再是事实，而成了没人愿意碰的麻烦。迷宫也就在这时合上了门。',
            '真正可怕的是集体沉默',
            '沉默变成新的秩序',
            'theme',
            ['知道不对', '选择闭嘴', '彼此利用', '共同掩埋'],
            '压迫',
            'slow',
            0.45,
        ),
        ReviewSegment(
            10,
            f'这也是它的主题重量：{theme}。影片没有把人简单分成好坏，它更像是在问，当一个封闭环境把所有人困在一起，人会怎样替自己的私心找理由。它拍的不是远处的恶，而是每个人心里那点能被说服的侥幸，所以看完才会觉得不舒服。',
            '主题：没有人完全清白',
            '私心会给自己找理由',
            'theme',
            ['人性幽暗', '命运荒诞', '伦理困境', '法治缺席'],
            '沉稳',
            'medium',
            0.35,
        ),
        ReviewSegment(
            11,
            '从创作角度看，它的多线叙事不是炫技。每一条线都在改写尸体的意义，最后再汇成一个结论：真相并不是没人看见，而是每个人都只想看见对自己有用的那一部分。所以影片越到后面越像迷宫，出口不是没有，而是被人一次次改了方向。这也是片名真正厉害的地方。',
            '多线叙事不是炫技',
            '每条线都改写同一具尸体',
            'network',
            ['宗耀线', '丽琴线', '王宝山线', '白虎家线', '村长线'],
            '反转',
            'medium',
            0.4,
        ),
        ReviewSegment(
            12,
            '这就是为什么《心迷宫》的后劲很长。它没有给观众一个痛快的正义收束，而是留下一个更难受的问题：如果所有人都靠谎言活下来，真相还会有人需要吗。真正的寒意，不在案子多复杂，而在沉默看起来竟然那么实用。',
            '结尾的后劲',
            '真相还会有人需要吗',
            'ending',
            ['没有爽快审判', '只有沉默共谋', '余味来自不安'],
            '后劲',
            'slow',
            0.6,
        ),
        ReviewSegment(
            13,
            '所以，真正值得看的不是这条视频替你讲完故事，而是回到原片里，看它怎样用人物、空间和错位信息，一点点把这个村子变成一座迷宫。这条视频只做影评和结构解析，完整的表演、节奏和细节，仍然应该在正版原片里观看。',
            '回到原片，才是完整体验',
            '这条视频只做评论和解析',
            'final',
            ['非剧情速看', '不替代观影', '推荐观看正版完整影片'],
            '收束',
            'slow',
            0.8,
        ),
    ]


def _synthesize_review_tts(task_dir: Path, segments: list[ReviewSegment]) -> None:
    client = QwenTTSClient()
    voice = _default_voice()
    audio_paths: list[str] = []
    current = 0.0
    for seg in segments:
        out = task_dir / 'tts' / f'voice_{seg.segment_id:03d}.wav'
        if not _audio_file_ok(out, seg.voiceover):
            _synthesize_with_retry(client, seg, out, voice)
            out.with_suffix('.text.txt').write_text(seg.voiceover, encoding='utf-8')
        duration = ffprobe_duration(str(out))
        seg.audio_path = str(out)
        seg.audio_start = current
        seg.audio_end = current + duration
        seg.actual_duration = duration
        audio_paths.append(str(out))
        current += duration
        if seg.pause_after > 0:
            pause_path = task_dir / 'tts' / f'pause_{seg.segment_id:03d}.wav'
            _write_silence(pause_path, seg.pause_after)
            audio_paths.append(str(pause_path))
            current += seg.pause_after
    concat_audios(audio_paths, str(task_dir / 'tts' / 'voice_full.aac'))


def _synthesize_with_retry(
    client: QwenTTSClient,
    seg: ReviewSegment,
    out: Path,
    voice: dict[str, str],
    attempts: int = 4,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            client.synthesize(
                text=seg.voiceover,
                voice=voice['voice_id'],
                output_path=str(out),
                model=voice['model'],
                language_type='Chinese',
                instructions=(
                    build_tts_instruction('冷峻影评解析', seg.emotion, seg.speed)
                    + '这是原创影评解析口播，不要像剧情速看，语气克制、有观点感。'
                ),
                optimize_instructions=True,
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(6.0 * attempt, 18.0))
    assert last_error is not None
    raise last_error


def _default_voice() -> dict[str, str]:
    voices = load_json(PROJECT_DIR / 'workdir' / 'voices.json', [])
    for voice in voices:
        if voice.get('id') == 'voice_default_male':
            return voice
    return {'voice_id': get_settings().default_male_voice, 'model': get_settings().qwen_tts_model}


def _audio_file_ok(path: Path, expected_text: str) -> bool:
    sidecar = path.with_suffix('.text.txt')
    if not path.exists() or not sidecar.exists():
        return False
    if sidecar.read_text(encoding='utf-8') != expected_text:
        return False
    try:
        return ffprobe_duration(str(path)) > 0.1
    except Exception:
        return False


def _write_silence(path: Path, duration: float) -> None:
    run_cmd([
        'ffmpeg', '-y',
        '-f', 'lavfi',
        '-i', 'anullsrc=channel_layout=mono:sample_rate=24000',
        '-t', f'{duration:.3f}',
        '-c:a', 'pcm_s16le',
        str(path),
    ])


def _render_slides(task_dir: Path, segments: list[ReviewSegment], payload: dict[str, Any]) -> None:
    for seg in segments:
        img = _base_canvas(seg.segment_id)
        draw = ImageDraw.Draw(img)
        _draw_header(draw, seg)
        _draw_visual(draw, seg, payload)
        _draw_footer(draw)
        img.save(task_dir / 'visuals' / f'slide_{seg.segment_id:03d}.png', quality=95)


def _base_canvas(index: int) -> Image.Image:
    palette = [
        ((24, 28, 27), (92, 34, 35), (222, 185, 96)),
        ((18, 29, 34), (49, 88, 93), (232, 204, 137)),
        ((31, 26, 26), (101, 75, 56), (190, 215, 177)),
        ((26, 27, 35), (76, 57, 99), (227, 177, 111)),
    ][index % 4]
    img = Image.new('RGB', (WIDTH, HEIGHT), palette[0])
    px = img.load()
    for y in range(HEIGHT):
        t = y / max(1, HEIGHT - 1)
        for x in range(WIDTH):
            r = int(palette[0][0] * (1 - t) + palette[1][0] * t)
            g = int(palette[0][1] * (1 - t) + palette[1][1] * t)
            b = int(palette[0][2] * (1 - t) + palette[1][2] * t)
            vignette = 1 - 0.26 * _distance_from_center(x, y)
            px[x, y] = (int(r * vignette), int(g * vignette), int(b * vignette))
    overlay = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    accent = (*palette[2], 32)
    for i in range(10):
        y = 220 + i * 150 + (index % 3) * 13
        od.line([(70, y), (WIDTH - 70, y + 60)], fill=accent, width=2)
    for i in range(6):
        cx = 120 + i * 180
        cy = 330 + ((i * 271 + index * 77) % 1180)
        od.ellipse((cx - 110, cy - 110, cx + 110, cy + 110), outline=(*palette[2], 25), width=2)
    return Image.alpha_composite(img.convert('RGBA'), overlay).filter(ImageFilter.SMOOTH).convert('RGB')


def _distance_from_center(x: int, y: int) -> float:
    dx = (x - WIDTH / 2) / (WIDTH / 2)
    dy = (y - HEIGHT / 2) / (HEIGHT / 2)
    return min(1.0, math.sqrt(dx * dx + dy * dy) / 1.2)


def _draw_header(draw: ImageDraw.ImageDraw, seg: ReviewSegment) -> None:
    draw.rounded_rectangle((70, 72, 1010, 176), radius=18, fill=(246, 238, 217), outline=(232, 204, 137), width=2)
    draw.text((100, 95), '原创影评解析 / 非原片画面', font=_font(FONT_MEDIUM, 34), fill=(36, 35, 31))
    draw.text((870, 96), f'{seg.segment_id:02d}/13', font=_font(FONT_BOLD, 34), fill=(113, 48, 45))
    _draw_wrapped(draw, seg.title, 80, 230, 920, _font(FONT_BOLD, 66), (250, 244, 225), line_gap=10)
    _draw_wrapped(draw, seg.focus, 84, 380, 900, _font(FONT_MEDIUM, 38), (229, 199, 130), line_gap=8)


def _draw_visual(draw: ImageDraw.ImageDraw, seg: ReviewSegment, payload: dict[str, Any]) -> None:
    box = (74, 530, WIDTH - 74, 1465)
    draw.rounded_rectangle(box, radius=26, fill=(245, 238, 220), outline=(232, 204, 137), width=3)
    if seg.visual_kind in {'relation', 'network'}:
        _draw_relation_graph(draw, box, seg.visual_kind == 'network')
    elif seg.visual_kind == 'timeline':
        _draw_timeline(draw, box)
    elif seg.visual_kind == 'chain':
        _draw_identity_chain(draw, box)
    elif seg.visual_kind == 'map':
        _draw_village_map(draw, box)
    elif seg.visual_kind == 'coffin':
        _draw_coffin_exchange(draw, box)
    elif seg.visual_kind in {'theme', 'cards'}:
        _draw_theme_cards(draw, box, seg.bullets)
    elif seg.visual_kind == 'question':
        _draw_question_stack(draw, box, seg.bullets)
    elif seg.visual_kind in {'ending', 'final'}:
        _draw_ending_symbol(draw, box, seg.visual_kind)
    else:
        _draw_hook_symbol(draw, box)
    _draw_bullets(draw, seg.bullets, 105, 1510)


def _draw_hook_symbol(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    cx = (x1 + x2) // 2
    draw.ellipse((cx - 230, y1 + 105, cx + 230, y1 + 565), fill=(42, 45, 42), outline=(125, 50, 48), width=8)
    draw.rounded_rectangle((cx - 250, y1 + 585, cx + 250, y1 + 720), radius=35, fill=(116, 59, 50))
    draw.text((cx - 178, y1 + 625), '无名焦尸', font=_font(FONT_BOLD, 58), fill=(250, 244, 225))
    for dx in (-170, -60, 75, 170):
        draw.line((cx + dx, y1 + 80, cx + dx - 30, y1 + 20), fill=(185, 154, 90), width=5)


def _draw_identity_chain(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    labels = ['无名焦尸', '被误认', '被利用', '被转移', '真相沉底']
    x1, y1, x2, _ = box
    start_x = x1 + 70
    y = y1 + 380
    gap = 182
    for idx, label in enumerate(labels):
        x = start_x + idx * gap
        draw.rounded_rectangle((x, y - 70, x + 140, y + 70), radius=24, fill=(42, 45, 42), outline=(125, 50, 48), width=4)
        _draw_wrapped(draw, label, x + 14, y - 34, 112, _font(FONT_BOLD, 34), (250, 244, 225), line_gap=4)
        if idx < len(labels) - 1:
            draw.line((x + 150, y, x + gap - 12, y), fill=(125, 50, 48), width=6)
            draw.polygon([(x + gap - 12, y), (x + gap - 35, y - 14), (x + gap - 35, y + 14)], fill=(125, 50, 48))
    draw.text((x1 + 96, y1 + 130), '身份错位链', font=_font(FONT_BOLD, 64), fill=(52, 48, 40))
    draw.text((x1 + 96, y1 + 610), '同一具尸体，被不同秘密反复改名。', font=_font(FONT_MEDIUM, 38), fill=(99, 67, 52))


def _draw_relation_graph(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], dense: bool) -> None:
    nodes = [
        ('宗耀', 500, 220),
        ('村长', 310, 420),
        ('黄欢', 690, 420),
        ('白虎', 300, 650),
        ('丽琴', 710, 650),
    ]
    if dense:
        nodes.append(('王宝山', 505, 785))
    x1, y1, _, _ = box
    edges = [(0, 1), (0, 2), (0, 3), (3, 4), (1, 4), (2, 5 if dense else 3)]
    for a, b in edges:
        ax, ay = nodes[a][1] + x1, nodes[a][2] + y1
        bx, by = nodes[b][1] + x1, nodes[b][2] + y1
        draw.line((ax, ay, bx, by), fill=(151, 81, 69), width=5)
    for label, nx, ny in nodes:
        cx, cy = x1 + nx, y1 + ny
        draw.ellipse((cx - 78, cy - 78, cx + 78, cy + 78), fill=(42, 45, 42), outline=(220, 176, 99), width=5)
        draw.text((cx - _text_width(draw, label, _font(FONT_BOLD, 36)) / 2, cy - 24), label, font=_font(FONT_BOLD, 36), fill=(250, 244, 225))
    draw.text((x1 + 95, y1 + 90), '人物压力网', font=_font(FONT_BOLD, 62), fill=(52, 48, 40))


def _draw_timeline(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, _ = box
    y = y1 + 455
    draw.line((x1 + 110, y, x2 - 110, y), fill=(50, 48, 42), width=8)
    points = [('观众', '知道一点'), ('人物', '隐瞒一点'), ('村里', '误会一点'), ('真相', '延迟出现')]
    for idx, (top, bottom) in enumerate(points):
        x = x1 + 140 + idx * 235
        draw.ellipse((x - 28, y - 28, x + 28, y + 28), fill=(125, 50, 48))
        draw.text((x - 46, y - 155), top, font=_font(FONT_BOLD, 42), fill=(52, 48, 40))
        _draw_wrapped(draw, bottom, x - 62, y + 55, 130, _font(FONT_MEDIUM, 32), (99, 67, 52), line_gap=4)
    draw.text((x1 + 95, y1 + 100), '信息错位时间线', font=_font(FONT_BOLD, 62), fill=(52, 48, 40))


def _draw_village_map(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    x1, y1, _, _ = box
    places = [('村口', 180, 250), ('灵堂', 460, 210), ('树林', 720, 360), ('派出所', 300, 650), ('棺材', 650, 690)]
    for idx in range(len(places) - 1):
        _, ax, ay = places[idx]
        _, bx, by = places[idx + 1]
        draw.line((x1 + ax, y1 + ay, x1 + bx, y1 + by), fill=(130, 111, 82), width=6)
    for label, px, py in places:
        draw.rounded_rectangle((x1 + px - 82, y1 + py - 46, x1 + px + 82, y1 + py + 46), radius=18, fill=(42, 45, 42), outline=(185, 154, 90), width=4)
        draw.text((x1 + px - _text_width(draw, label, _font(FONT_BOLD, 34)) / 2, y1 + py - 24), label, font=_font(FONT_BOLD, 34), fill=(250, 244, 225))
    draw.text((x1 + 95, y1 + 95), '熟人社会地图', font=_font(FONT_BOLD, 62), fill=(52, 48, 40))
    draw.text((x1 + 95, y1 + 810), '人情、面子、债务，比法律更早抵达现场。', font=_font(FONT_MEDIUM, 34), fill=(99, 67, 52))


def _draw_coffin_exchange(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    x1, y1, _, _ = box
    cx = x1 + 466
    cy = y1 + 420
    draw.rounded_rectangle((cx - 260, cy - 88, cx + 260, cy + 88), radius=38, fill=(116, 59, 50), outline=(52, 48, 40), width=8)
    draw.text((cx - 110, cy - 34), '棺材', font=_font(FONT_BOLD, 62), fill=(250, 244, 225))
    labels = ['认尸', '退尸', '借尸', '遮羞']
    for idx, label in enumerate(labels):
        angle = math.pi * 2 * idx / len(labels) - math.pi / 2
        x = cx + int(math.cos(angle) * 320)
        y = cy + int(math.sin(angle) * 260)
        draw.ellipse((x - 60, y - 60, x + 60, y + 60), fill=(42, 45, 42), outline=(220, 176, 99), width=4)
        draw.text((x - _text_width(draw, label, _font(FONT_BOLD, 32)) / 2, y - 22), label, font=_font(FONT_BOLD, 32), fill=(250, 244, 225))
        draw.line((cx, cy, x, y), fill=(151, 81, 69), width=4)
    draw.text((x1 + 95, y1 + 95), '死亡道具化', font=_font(FONT_BOLD, 62), fill=(52, 48, 40))


def _draw_theme_cards(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], bullets: list[str]) -> None:
    x1, y1, _, _ = box
    for idx, item in enumerate(bullets[:4]):
        x = x1 + 95 + (idx % 2) * 390
        y = y1 + 180 + (idx // 2) * 260
        draw.rounded_rectangle((x, y, x + 330, y + 180), radius=26, fill=(42, 45, 42), outline=(125, 50, 48), width=4)
        _draw_wrapped(draw, item, x + 30, y + 55, 270, _font(FONT_BOLD, 42), (250, 244, 225), line_gap=6)
    draw.text((x1 + 95, y1 + 760), '主题不是口号，而是人物选择后的余温。', font=_font(FONT_MEDIUM, 34), fill=(99, 67, 52))


def _draw_question_stack(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], bullets: list[str]) -> None:
    x1, y1, _, _ = box
    for idx, item in enumerate(bullets[:3]):
        y = y1 + 190 + idx * 220
        draw.rounded_rectangle((x1 + 115, y, x1 + 815, y + 145), radius=28, fill=(42, 45, 42), outline=(220, 176, 99), width=4)
        draw.text((x1 + 145, y + 40), f'Q{idx + 1}', font=_font(FONT_BOLD, 44), fill=(220, 176, 99))
        _draw_wrapped(draw, item, x1 + 250, y + 40, 570, _font(FONT_BOLD, 40), (250, 244, 225), line_gap=6)


def _draw_ending_symbol(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], kind: str) -> None:
    x1, y1, _, _ = box
    cx = x1 + 466
    draw.arc((cx - 250, y1 + 190, cx + 250, y1 + 690), 25, 335, fill=(125, 50, 48), width=10)
    draw.line((cx - 170, y1 + 600, cx + 170, y1 + 600), fill=(52, 48, 40), width=7)
    text = '沉默共谋' if kind == 'ending' else '回到原片'
    draw.text((cx - _text_width(draw, text, _font(FONT_BOLD, 64)) / 2, y1 + 380), text, font=_font(FONT_BOLD, 64), fill=(52, 48, 40))
    draw.text((x1 + 105, y1 + 760), '解析不能替代作品，真正的完整体验仍在正版原片里。', font=_font(FONT_MEDIUM, 32), fill=(99, 67, 52))


def _draw_bullets(draw: ImageDraw.ImageDraw, bullets: list[str], x: int, y: int) -> None:
    for idx, bullet in enumerate(bullets[:4]):
        yy = y + idx * 72
        draw.ellipse((x, yy + 12, x + 22, yy + 34), fill=(232, 204, 137))
        _draw_wrapped(draw, bullet, x + 42, yy, 850, _font(FONT_MEDIUM, 38), (250, 244, 225), line_gap=4)


def _draw_footer(draw: ImageDraw.ImageDraw) -> None:
    footer = '本视频为原创影评图解；未使用原片画面或原片音频；建议观看正版完整影片'
    draw.rounded_rectangle((70, HEIGHT - 122, WIDTH - 70, HEIGHT - 58), radius=16, fill=(246, 238, 217))
    draw.text((92, HEIGHT - 108), footer, font=_font(FONT_MEDIUM, 25), fill=(36, 35, 31))


def _render_visual_video(task_dir: Path, segments: list[ReviewSegment]) -> Path:
    clip_paths: list[Path] = []
    for seg in segments:
        duration = max(1.0, seg.actual_duration + seg.pause_after)
        slide = task_dir / 'visuals' / f'slide_{seg.segment_id:03d}.png'
        out = task_dir / 'edit' / f'visual_{seg.segment_id:03d}.mp4'
        run_cmd([
            'ffmpeg', '-y',
            '-loop', '1',
            '-i', str(slide),
            '-t', f'{duration:.3f}',
            '-vf', f'scale={WIDTH}:{HEIGHT},format=yuv420p',
            '-r', str(FPS),
            '-c:v', get_settings().ffmpeg_video_encoder,
            '-pix_fmt', 'yuv420p',
            str(out),
        ])
        clip_paths.append(out)
    concat_file = task_dir / 'edit' / 'visual_base.concat.txt'
    concat_file.write_text('\n'.join(f"file '{path.resolve()}'" for path in clip_paths), encoding='utf-8')
    out = task_dir / 'edit' / 'visual_base.mp4'
    run_cmd([
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', str(concat_file),
        '-fflags', '+genpts',
        '-c:v', get_settings().ffmpeg_video_encoder,
        '-pix_fmt', 'yuv420p',
        str(out),
    ])
    return out


def _compose_final(task_dir: Path, visual_base: Path, voice_audio: Path, subtitle_ass: Path) -> Path:
    duration = ffprobe_duration(str(visual_base))
    ambience = (
        f'aevalsrc=0.018*sin(2*PI*55*t)+0.010*sin(2*PI*110*t):'
        f's=48000:d={duration:.3f}'
    )
    out = task_dir / 'render' / 'final.pre_speedfit.mp4'
    run_cmd([
        'ffmpeg', '-y',
        '-i', str(visual_base),
        '-i', str(voice_audio),
        '-f', 'lavfi',
        '-i', ambience,
        '-filter_complex',
        (
            f'[1:a]volume=1.0,apad,atrim=0:{duration:.3f}[voice];'
            f'[2:a]volume=0.16,apad,atrim=0:{duration:.3f}[bed];'
            '[voice][bed]amix=inputs=2:duration=first:normalize=0,'
            'loudnorm=I=-23.0:TP=-2.0:LRA=11.0[a]'
        ),
        '-map', '0:v:0',
        '-map', '[a]',
        '-vf', f'ass={subtitle_ass}',
        '-t', f'{duration:.3f}',
        '-c:v', get_settings().ffmpeg_video_encoder,
        '-c:a', 'aac',
        '-movflags', '+faststart',
        str(out),
    ])
    return out


def _write_vertical_ass(path: Path, segments: list[ReviewSegment]) -> Path:
    lines = [
        '[Script Info]',
        'ScriptType: v4.00+',
        'PlayResX: 1080',
        'PlayResY: 1920',
        'ScaledBorderAndShadow: yes',
        '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
        'Style: Default,Noto Sans CJK SC,42,&H00FFFFFF,&H000000FF,&H001A1A1A,&HA0000000,1,0,0,0,100,100,0,0,1,3,1,2,70,70,170,1',
        '',
        '[Events]',
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    for seg in segments:
        chunks = _subtitle_chunks(seg.voiceover, 16, 2)
        seg_duration = max(0.1, seg.audio_end - seg.audio_start)
        chunk_duration = seg_duration / max(1, len(chunks))
        for idx, chunk in enumerate(chunks):
            start = seg.audio_start + idx * chunk_duration
            end = seg.audio_end if idx == len(chunks) - 1 else start + chunk_duration
            lines.append(
                f'Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{_ass_escape(chunk)}'
            )
    path.write_text('\n'.join(lines), encoding='utf-8')
    return path


def _subtitle_chunks(text: str, max_line_chars: int, max_lines: int) -> list[str]:
    clauses = _split_clauses(text)
    chunks: list[str] = []
    current = ''
    limit = max_line_chars * max_lines
    for clause in clauses:
        candidate = clause if not current else current + clause
        if len(candidate) > limit and current:
            chunks.append(_wrap_lines(current, max_line_chars, max_lines))
            current = clause
        elif len(clause) > limit:
            if current:
                chunks.append(_wrap_lines(current, max_line_chars, max_lines))
                current = ''
            for idx in range(0, len(clause), limit):
                chunks.append(_wrap_lines(clause[idx:idx + limit], max_line_chars, max_lines))
        else:
            current = candidate
    if current:
        chunks.append(_wrap_lines(current, max_line_chars, max_lines))
    return chunks


def _split_clauses(text: str) -> list[str]:
    result: list[str] = []
    current = ''
    for ch in text:
        current += ch
        if ch in '。！？!?；;，,':
            result.append(current)
            current = ''
    if current:
        result.append(current)
    return result


def _wrap_lines(text: str, max_line_chars: int, max_lines: int) -> str:
    lines = []
    remaining = text.strip()
    while remaining and len(lines) < max_lines:
        if len(remaining) <= max_line_chars:
            lines.append(remaining)
            break
        split = _best_split(remaining, max_line_chars)
        lines.append(remaining[:split].strip())
        remaining = remaining[split:].strip()
    if remaining and lines and len(lines) == max_lines:
        lines[-1] = (lines[-1] + remaining).strip()
    return r'\N'.join(lines)


def _best_split(text: str, max_line_chars: int) -> int:
    upper = min(len(text) - 1, max_line_chars)
    lower = max(1, upper - 6)
    candidates = range(lower, upper + 1)
    punct = '，,。！？!?；;：:'
    return min(candidates, key=lambda idx: (0 if text[idx - 1] in punct else 8) + abs(idx - max_line_chars))


def _ass_escape(text: str) -> str:
    return text.replace('{', '｛').replace('}', '｝')


def _ass_time(seconds: float) -> str:
    total = int(round(max(0.0, seconds) * 100))
    hours = total // 360000
    total %= 360000
    minutes = total // 6000
    total %= 6000
    secs = total // 100
    centis = total % 100
    return f'{hours}:{minutes:02d}:{secs:02d}.{centis:02d}'


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    width: int,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    line_gap: int = 8,
) -> int:
    lines = _wrap_visual_text(draw, text, font, width)
    yy = y
    for line in lines:
        draw.text((x, yy), line, font=font, fill=fill)
        yy += _text_height(draw, line, font) + line_gap
    return yy


def _wrap_visual_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    current = ''
    for ch in text:
        candidate = current + ch
        if current and _text_width(draw, candidate, font) > width:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _text_height(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


if __name__ == '__main__':
    raise SystemExit(main())
