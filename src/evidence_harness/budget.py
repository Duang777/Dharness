from __future__ import annotations

FINALIZATION_TURN_RESERVE = 3
FINALIZATION_WALL_TIME_RATIO = 0.1


def finalization_turn_reserve(max_turns: int) -> int:
    return min(FINALIZATION_TURN_RESERVE, max(1, max_turns - 1))


def finalization_wall_time_reserve_sec(max_wall_time_sec: float) -> float:
    return max_wall_time_sec * FINALIZATION_WALL_TIME_RATIO
