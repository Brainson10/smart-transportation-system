"""
Temporal voting. A fake clock keeps these tests instant and deterministic.
"""

from gate.plate_utils import PlateVoter


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_voter(clock, **kwargs):
    kwargs.setdefault("window", 6.0)
    kwargs.setdefault("threshold", 2.0)
    kwargs.setdefault("min_sightings", 2)
    kwargs.setdefault("merge_distance", 0)
    return PlateVoter(time_fn=clock, **kwargs)


def test_locks_once_weight_and_sightings_are_met():
    clock = FakeClock()
    voter = make_voter(clock)
    assert voter.add("MN05AB1234", 95) is None      # weight 0.95, 1 sighting
    assert voter.add("MN05AB1234", 95) is None      # weight 1.90, still < 2.0
    assert voter.add("MN05AB1234", 95) == "MN05AB1234"


def test_single_high_confidence_frame_cannot_lock():
    """min_sightings is what stops one lucky frame opening the gate."""
    clock = FakeClock()
    voter = make_voter(clock, threshold=0.5, min_sightings=2)
    assert voter.add("MN05AB1234", 100) is None


def test_low_confidence_reads_do_not_reach_threshold():
    clock = FakeClock()
    voter = make_voter(clock)
    for _ in range(3):
        assert voter.add("MN05AB1234", 50) is None   # 3 * 0.5 = 1.5 < 2.0


def test_votes_expire_outside_the_window():
    """
    The old defaultdict(int) never expired: a plate seen once now and once ten
    minutes later would lock the gate.
    """
    clock = FakeClock()
    voter = make_voter(clock)
    voter.add("MN05AB1234", 95)
    voter.add("MN05AB1234", 95)
    clock.advance(10.0)                              # window is 6s
    assert voter.add("MN05AB1234", 95) is None
    assert voter.snapshot() == {"MN05AB1234": 0.95}


def test_expired_keys_are_dropped_so_memory_is_bounded():
    clock = FakeClock()
    voter = make_voter(clock)
    for i in range(20):
        voter.add(f"MN05AB{i:04d}", 40)
        clock.advance(1.0)
    assert len(voter.snapshot()) <= 7                 # only the 6s window survives


def test_reset_clears_everything():
    clock = FakeClock()
    voter = make_voter(clock)
    voter.add("MN05AB1234", 95)
    voter.reset()
    assert voter.snapshot() == {}
    assert voter.best() == (None, 0.0)


def test_single_glyph_misread_merges_into_the_heavier_key():
    clock = FakeClock()
    voter = make_voter(clock, merge_distance=1)
    voter.add("MN05AB1234", 90)
    voter.add("MN05AB1234", 90)
    # differs in exactly one position -- same vehicle, one bad glyph
    assert voter.add("MN05AB1239", 90) == "MN05AB1234"


def test_best_reports_the_heaviest_plate():
    clock = FakeClock()
    voter = make_voter(clock, threshold=99.0)
    voter.add("MN05AB1234", 90)
    voter.add("KA01A1234", 40)
    plate, weight = voter.best()
    assert plate == "MN05AB1234"
    assert weight > 0.8


# ---------------- consensus ----------------
def test_consensus_fixes_a_misread_that_arrived_first():
    """The first read becomes the key; the per-glyph majority must still win."""
    clock = FakeClock()
    voter = make_voter(clock, merge_distance=1)
    assert voter.add("MN05AB1239", 90) is None      # misread arrives first
    assert voter.add("MN05AB1234", 90) is None
    assert voter.add("MN05AB1234", 90) == "MN05AB1234"


def test_consensus_falls_back_to_key_when_result_is_not_a_plate():
    clock = FakeClock()
    voter = make_voter(clock, merge_distance=0)
    voter.add("MN05AB1234", 90)
    assert voter.consensus("MN05AB1234") == "MN05AB1234"
    assert voter.consensus("unknown-key") == "unknown-key"


def test_leader_reports_progress_towards_lock():
    clock = FakeClock()
    voter = make_voter(clock)
    assert voter.leader() is None
    voter.add("MN05AB1234", 50)
    leader = voter.leader()
    assert leader["plate"] == "MN05AB1234"
    assert leader["progress"] == 0.25 and leader["sightings"] == 1


def test_thresholds_follow_config_at_call_time(monkeypatch):
    """The admin System page retunes config; a running voter must pick it up."""
    import config
    monkeypatch.setattr(config, "VOTE_THRESHOLD", 0.5)
    monkeypatch.setattr(config, "VOTE_MIN_SIGHTINGS", 1)
    voter = PlateVoter(time_fn=FakeClock(), merge_distance=0)
    assert voter.add("MN05AB1234", 60) == "MN05AB1234"


# ---------------- repeat-plate cooldown ----------------
from gate.plate_utils import RecentPlates  # noqa: E402


def test_recent_plate_is_ignored_until_cooldown_ends():
    clock = FakeClock()
    recent = RecentPlates(cooldown=30, time_fn=clock)
    recent.add("MN05AB1234")
    assert recent.remaining("MN05AB1234") == 30
    clock.advance(20)
    assert recent.remaining("MN05AB1234") == 10
    clock.advance(11)
    assert recent.remaining("MN05AB1234") == 0
    assert recent.snapshot() == {}


def test_one_glyph_misread_of_a_cooling_plate_is_also_ignored():
    """Otherwise one bad frame of the same car would decide it again."""
    clock = FakeClock()
    recent = RecentPlates(cooldown=30, time_fn=clock)
    recent.add("MN05AB1234")
    assert recent.remaining("MN05AB1284") > 0
    assert recent.remaining("KA01CD9999") == 0


def test_clear_ends_every_cooldown():
    recent = RecentPlates(cooldown=30, time_fn=FakeClock())
    recent.add("MN05AB1234")
    recent.clear()
    assert recent.remaining("MN05AB1234") == 0
