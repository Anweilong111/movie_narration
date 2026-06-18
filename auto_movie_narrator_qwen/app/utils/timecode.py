def seconds_to_srt_time(seconds: float) -> str:
    seconds = max(seconds, 0)
    millis = int(round((seconds - int(seconds)) * 1000))
    whole = int(seconds)
    if millis == 1000:
        whole += 1
        millis = 0
    h = whole // 3600
    m = (whole % 3600) // 60
    s = whole % 60
    return f'{h:02d}:{m:02d}:{s:02d},{millis:03d}'


def hhmmss_to_seconds(value: str) -> float:
    h, m, s = value.replace(',', '.').split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)
