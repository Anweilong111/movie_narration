from app.utils.timecode import seconds_to_srt_time, hhmmss_to_seconds


def test_seconds_to_srt_time():
    assert seconds_to_srt_time(0) == '00:00:00,000'
    assert seconds_to_srt_time(65.123) == '00:01:05,123'


def test_hhmmss_to_seconds():
    assert hhmmss_to_seconds('00:01:05') == 65
