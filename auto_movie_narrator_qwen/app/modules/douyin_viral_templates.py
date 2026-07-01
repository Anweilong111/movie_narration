from __future__ import annotations

from typing import Any


DOUYIN_REFERENCE_PATTERNS = [
    {
        'name': '一口气看完',
        'use': '适合完整剧情推进版，标题强调完整体验和强后劲。',
        'examples': ['一口气看完这部高能恐怖片', '一口气看完经典惊悚片'],
    },
    {
        'name': '胆小慎入',
        'use': '适合恐怖、惊悚、灵异、诅咒题材，先筛选胆量再制造点击。',
        'examples': ['胆小慎入，真正可怕的不是鬼', '胆小别半夜看'],
    },
    {
        'name': '白天刷不到晚上逃不掉',
        'use': '适合午夜、诅咒、录像带、房间、怪谈类题材，制造平台语境记忆点。',
        'examples': ['白天刷不到，晚上逃不掉', '晚上看完后劲太大'],
    },
    {
        'name': '高能反转',
        'use': '适合结尾真相、身份反差、人性代价、犯罪审判类题材。',
        'examples': ['最后一秒才明白真相', '真正可怕的是最后的选择'],
    },
    {
        'name': '真实规则',
        'use': '适合用一句规则开场，比如不能接电话、不能看录像、不能回头。',
        'examples': ['看完录像七天后必死', '只要听见这通电话就来不及了'],
    },
]


HOOK_TRIGGERS = [
    '没人想到',
    '真正可怕的是',
    '直到最后才明白',
    '问题不是',
    '更狠的是',
    '从这一刻开始',
    '只要',
    '千万别',
]


HORROR_TAGS = ['#恐怖电影', '#惊悚电影', '#胆小慎入', '#白天刷不到晚上逃不掉', '#一口气看完']
GENERAL_TAGS = ['#电影解说', '#影视解说']
SUSPENSE_TAGS = ['#悬疑电影', '#高能反转']
CRIME_TAGS = ['#犯罪电影', '#悬疑犯罪']


def normalize_angle(text: str, director_plan: dict[str, Any] | None = None) -> str:
    haystack = f'{text} {director_plan or {}}'.lower()
    if any(word in haystack for word in ('鬼', '诅咒', '恐怖', '惊悚', '怪物', '灵异', '录像带', 'horror', 'curse', 'ghost')):
        return '恐怖惊悚'
    if any(word in haystack for word in ('凶手', '连环', '犯罪', '警探', '尸体', 'serial', 'killer', 'crime')):
        return '悬疑犯罪'
    if any(word in haystack for word in ('反转', '真相', '身份', '骗局', 'reversal', 'truth')):
        return '高能反转'
    if any(word in haystack for word in ('母爱', '家庭', '亲情', '救赎', '孤独', 'family')):
        return '情绪后劲'
    return '剧情悬念'


def title_templates(angle: str) -> list[str]:
    if angle == '恐怖惊悚':
        return [
            '胆小慎入！《{title}》真正可怕的不是鬼，而是{point}',
            '一口气看完《{title}》，这部恐怖片后劲太大',
            '白天刷不到，晚上逃不掉：《{title}》把{point}拍绝了',
            '看懂《{title}》才明白，最吓人的其实是最后的选择',
            '这部惊悚片最狠的一点：{point}',
        ]
    if angle == '悬疑犯罪':
        return [
            '一口气看完《{title}》，最后才知道凶手真正想审判谁',
            '《{title}》最狠的不是案子，而是{point}',
            '这部悬疑片后劲太大，真相揭开后没人能轻松离开',
            '看懂《{title}》才明白，正义也可能变成陷阱',
            '高能反转！《{title}》把人性一步步逼到绝境',
        ]
    return [
        '一口气看完《{title}》，最后一刻才明白{point}',
        '《{title}》真正厉害的不是反转，而是{point}',
        '这部电影后劲太大，越看越觉得不对劲',
        '没人想到，《{title}》最后会把故事推到这一步',
        '看懂《{title}》才明白，最难的不是活下去',
    ]


def hook_line_templates(angle: str) -> list[str]:
    if angle == '恐怖惊悚':
        return [
            '你只有七天时间。',
            '千万别在半夜看这盘录像。',
            '真正可怕的不是鬼出现，而是你已经被选中了。',
            '所有人都以为这只是传说，直到电话真的响了。',
            '这部片最吓人的地方，是它把诅咒藏进日常生活里。',
        ]
    if angle == '悬疑犯罪':
        return [
            '没人想到，最后被审判的不是凶手。',
            '这个案子从一开始就不是为了杀人。',
            '真正的线索，藏在每一次看似无关的死亡里。',
            '当警察开始追凶，他也被拖进了审判里。',
            '最狠的反转，是凶手早就算好了所有人的选择。',
        ]
    return [
        '没人想到，真正的代价在最后一刻才出现。',
        '故事一开始像意外，越往后越像一场安排。',
        '真正的问题不是发生了什么，而是谁必须付出代价。',
        '所有铺垫，都在最后变成一记回旋刀。',
    ]


def opening_formula(angle: str) -> list[dict[str, Any]]:
    return [
        {
            'time_range': [0, 3],
            'goal': '先抛后果或禁忌规则',
            'script_pattern': hook_line_templates(angle)[0],
            'visual_requirement': '强表情、危险结果、关键物件或异常画面',
        },
        {
            'time_range': [3, 8],
            'goal': '补一句反常识解释',
            'script_pattern': '真正可怕的不是表面危险，而是它已经改变了人物的选择。',
            'visual_requirement': '人物反应或线索特写',
        },
        {
            'time_range': [8, 15],
            'goal': '立刻回到故事起点',
            'script_pattern': '但这一切，要从最普通的一天说起。',
            'visual_requirement': '回到正片起点，禁止片头、黑屏、演职员表',
        },
    ]


def retention_structure(duration: int, angle: str) -> list[dict[str, Any]]:
    duration = max(60, int(duration or 0))
    phases = [
        ('hook', 0.0, 0.04, '强规则/强后果，先制造点击后的停留'),
        ('setup', 0.04, 0.16, '交代人物处境，但每 20 秒给一个异常点'),
        ('clue_chain', 0.16, 0.45, '线索递进，每 30-45 秒出现新问题'),
        ('pressure', 0.45, 0.68, '人物关系和外部危险同时升级'),
        ('climax', 0.68, 0.88, '集中释放真相、代价、反转或强冲突'),
        ('aftertaste', 0.88, 1.0, '放慢收束，用观点和情绪留下评论空间'),
    ]
    return [
        {
            'phase': phase,
            'time_range': [round(duration * start, 1), round(duration * end, 1)],
            'goal': goal,
            'required_beat': required_beat(phase, angle),
        }
        for phase, start, end, goal in phases
    ]


def required_beat(phase: str, angle: str) -> str:
    if phase == 'hook':
        return '一句话制造危险规则或结局倒钩'
    if phase == 'setup':
        return '不要平铺背景，必须带异常点'
    if phase == 'clue_chain':
        return '新线索/新问题/新恐惧至少出现一次'
    if phase == 'pressure':
        return '人物选择被逼窄，镜头要有反应或冲突'
    if phase == 'climax':
        return '释放真相、代价或核心反转'
    return f'围绕{angle}做情绪余味和评论引导'


def subtitle_policy() -> dict[str, Any]:
    return {
        'chars_per_screen_min': 8,
        'chars_per_screen_max': 16,
        'hook_chars_per_screen_max': 12,
        'turning_words_standalone': ['但', '可没想到', '直到', '真正可怕的是', '问题是', '更狠的是'],
        'highlight_keywords': ['诅咒', '录像带', '电话', '真相', '凶手', '死亡', '七天', '选择', '代价'],
        'avoid': ['遮挡人脸', '整段长字幕', '把画面描述误塞进字幕'],
    }


def voice_policy(angle: str) -> dict[str, Any]:
    return {
        'hook': {'speed': 'fast', 'emotion': '压迫', 'pause_after': 0.25},
        'suspense': {'speed': 'medium', 'emotion': '悬疑', 'pause_after': 0.35},
        'shock': {'speed': 'fast', 'emotion': '紧张', 'pause_after': 0.2},
        'reversal': {'speed': 'slow', 'emotion': '反转', 'pause_after': 0.55},
        'ending': {'speed': 'slow', 'emotion': '后劲', 'pause_after': 0.7},
        'angle': angle,
    }


def visual_policy() -> dict[str, Any]:
    return {
        'prefer': ['人物反应', '关键物件', '异常画面', '冲突动作', '象征结尾'],
        'avoid': ['片头', '黑屏', '演职员表', '水印', '无关对白', '长时间静帧'],
        'max_static_hold_seconds': 4.5,
        'max_seconds_without_new_information': 35,
        'story_sync': '开头钩子可短暂倒钩，正片必须按剧情顺序推进',
    }


def hashtag_templates(angle: str, title: str) -> list[str]:
    tags = [*GENERAL_TAGS, f'#{tag_safe(title)}']
    if angle == '恐怖惊悚':
        tags.extend(HORROR_TAGS)
    elif angle == '悬疑犯罪':
        tags.extend(CRIME_TAGS + SUSPENSE_TAGS)
    elif angle == '高能反转':
        tags.extend(SUSPENSE_TAGS + ['#反转电影'])
    else:
        tags.extend(['#高分电影', '#一口气看完'])
    return unique(tags)[:10]


def comment_prompts(angle: str, point: str) -> list[str]:
    if angle == '恐怖惊悚':
        return [
            '如果是你，看完录像后会告诉别人吗？',
            f'你觉得真正可怕的是鬼，还是{point}？',
            '这部片你敢一个人晚上看完吗？',
        ]
    if angle == '悬疑犯罪':
        return [
            '如果你是警探，最后会怎么选？',
            f'你觉得这是正义，还是{point}？',
            '这个结局你第一次看猜到了吗？',
        ]
    return [
        '如果你是主角，最后会怎么选？',
        f'你觉得故事真正想讲的是不是{point}？',
        '这个结尾你觉得是救赎还是惩罚？',
    ]


def cover_text_templates(title: str, angle: str, point: str) -> list[str]:
    if angle == '恐怖惊悚':
        return unique([
            title,
            f'{title}\n胆小慎入',
            f'真正可怕的是\n{short_text(point, 10)}',
        ])
    return unique([
        title,
        f'{title}\n高能反转',
        f'最后才明白\n{short_text(point, 10)}',
    ])


def tag_safe(text: str) -> str:
    return ''.join(ch for ch in str(text or '') if ch.isalnum() or '\u4e00' <= ch <= '\u9fff') or '电影'


def short_text(text: Any, limit: int) -> str:
    value = ' '.join(str(text or '').split()).strip(' ，。；;,.')
    if len(value) <= limit:
        return value
    return value[:limit].rstrip('，。；;,.')


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = str(item or '').strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
