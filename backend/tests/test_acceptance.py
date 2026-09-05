import pytest
from app.acceptance import score


def segment(text, speaker='A', start=0, end=1):
    return dict(text=text, speaker=speaker, start=start, end=end)


def test_exact_and_renamed_speakers():
    result = score([segment('你好 世界')], [segment('你好 世界', 'cluster_4')])
    assert result['text']['cer']['rate'] == 0
    assert result['speaker']['der'] == 0
    assert result['quality_gate'] == 'not_evaluated'


def test_edits_missed_and_false_alarms():
    result = score([segment('a b')], [segment('a c', start=0, end=2)])
    assert result['text']['wer']['rate'] == 0.5
    assert result['speaker']['false_alarm_seconds'] == 1
    result = score([segment('a')], [])
    assert result['speaker']['der'] == 1


def test_overlap_and_confusion():
    result = score([segment('a'), segment('b', 'B')], [segment('a')])
    assert result['speaker']['der'] == 0.5
    result = score([segment('a'), segment('b','B',1,2)], [segment('a','X',0,2)])
    assert result['speaker']['confusion_seconds'] == 1


def test_requires_reference_and_valid_timing():
    with pytest.raises(ValueError): score([], [])
    with pytest.raises(ValueError): score([segment('a', end=float('nan'))], [])
