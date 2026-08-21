from dataclasses import dataclass
from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class Window:
    train: tuple[int, ...]
    validation: tuple[int, ...]
    test: tuple[int, ...]


def walk_forward_windows(
    timestamps: Sequence[int],
    train_size: int,
    validation_size: int,
    test_size: int,
    step: int | None = None,
) -> tuple[Window, ...]:
    """Produce chronological train/validation/test windows with no overlap leakage."""
    if min(train_size, validation_size, test_size) <= 0:
        raise ValueError("window_sizes_must_be_positive")
    step = test_size if step is None else step
    if step <= 0:
        raise ValueError("step_must_be_positive")
    out: list[Window] = []
    start = 0
    total = len(timestamps)
    while start + train_size + validation_size + test_size <= total:
        a = start + train_size
        b = a + validation_size
        c = b + test_size
        out.append(Window(tuple(timestamps[start:a]), tuple(timestamps[a:b]), tuple(timestamps[b:c])))
        start += step
    return tuple(out)
