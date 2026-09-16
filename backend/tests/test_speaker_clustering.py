from app.local_models.worker import cluster_speaker_embeddings


def segment(start, end):
    return {"start_time": start, "end_time": end}


def test_centroid_updates_allow_one_speaker_to_drift():
    speakers = cluster_speaker_embeddings(
        [[1, 0], [0.8, 0.6], [0.55, 0.835]],
        [segment(0, 2), segment(2, 4), segment(4, 6)],
        threshold=0.7,
    )
    assert speakers == ["speaker_1"] * 3


def test_short_segments_and_short_singleton_join_stable_neighbors():
    speakers = cluster_speaker_embeddings(
        [[1, 0], None, [0.98, 0.1], [0, 1], [-0.9, -0.1], [0.1, 0.99]],
        [segment(0, 2), segment(2, 2.4), segment(2.4, 5), segment(5, 8), segment(8, 9.5), segment(9.5, 12)],
    )
    assert speakers[0] == speakers[1] == speakers[2] == "speaker_1"
    assert speakers[3] == speakers[4] == speakers[5] == "speaker_2"
    assert None not in speakers


def test_multiple_supported_speakers_are_not_collapsed():
    speakers = cluster_speaker_embeddings(
        [[1, 0, 0], [0.99, 0.05, 0], [0, 1, 0], [0.05, 0.99, 0], [0, 0, 1], [0, 0.05, 0.99]],
        [segment(i * 2, i * 2 + 2) for i in range(6)],
    )
    assert speakers == ["speaker_1", "speaker_1", "speaker_2", "speaker_2", "speaker_3", "speaker_3"]


def test_all_too_short_remains_unassigned_without_model_evidence():
    assert cluster_speaker_embeddings(
        [None, None], [segment(0, 0.4), segment(0.5, 0.9)]
    ) == [None, None]
