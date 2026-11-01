"""Deterministic evidence for explicitly designated, steady monophonic tones.

Intended Hz must come from the original Score/EDO or a reference sidecar, never
from the rendered signal. NumPy-only estimation is independent of that target.
No automatic window selection, percussion inference, or polyphonic certification.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray


@dataclass(frozen=True)
class ToneWindow:
    """Half-open sample bounds in one mono buffer; exclude attacks/releases."""

    name: str
    start_sample: int
    stop_sample: int
    expected_hz: float | None
    percussion: bool = False

    def __post_init__(self) -> None:
        if type(self.percussion) is not bool:
            raise ValueError('percussion must be a boolean')
        if (type(self.start_sample) is not int or type(self.stop_sample) is not int
                or not 0 <= self.start_sample < self.stop_sample):
            raise ValueError('window sample bounds must be integers with 0 <= start < stop')
        if not self.percussion and (self.expected_hz is None or not math.isfinite(self.expected_hz)
                                    or self.expected_hz <= 0):
            raise ValueError('pitched windows require explicit finite positive expected_hz')
        if self.percussion and self.expected_hz is not None:
            raise ValueError('percussion is drum-key based: expected_hz must be None')


@dataclass(frozen=True)
class TuningCheckConfig:
    """Analysis settings; confidence is periodic similarity, not a probability."""

    min_hz: float = 40.0
    max_hz: float = 2000.0
    min_confidence: float = 0.9
    min_rms: float = 1e-5
    max_tone_cents: float = 5.0
    max_mean_cents: float = 15.0
    max_p95_cents: float = 15.0

    def __post_init__(self) -> None:
        if not (math.isfinite(self.min_hz) and math.isfinite(self.max_hz)
                and 0 < self.min_hz < self.max_hz):
            raise ValueError('frequency bounds must be finite with 0 < min_hz < max_hz')
        if not math.isfinite(self.min_confidence) or not 0 < self.min_confidence <= 1:
            raise ValueError('min_confidence must be in (0, 1]')
        if not math.isfinite(self.min_rms) or self.min_rms <= 0:
            raise ValueError('min_rms must be finite and positive')
        for name, ceiling in (('max_tone_cents', 5), ('max_mean_cents', 15), ('max_p95_cents', 15)):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= ceiling:
                raise ValueError(f'{name} must be finite and in [0, {ceiling}]; limits may only tighten')


@dataclass(frozen=True)
class TuningWindowResult:
    window: ToneWindow
    measured_hz: float | None
    confidence: float
    exclusion: str | None = None

    @property
    def coverage(self) -> float:
        """Binary coverage: the whole designated window is one measurement unit."""
        return float(self.measured_hz is not None and self.exclusion is None)

    @property
    def absolute_cents(self) -> float | None:
        expected = self.window.expected_hz
        if not self.coverage or expected is None:
            return None
        assert self.measured_hz is not None
        return abs(1200 * (math.log2(self.measured_hz) - math.log2(expected)))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), 'coverage': self.coverage, 'absolute_cents': self.absolute_cents}


@dataclass(frozen=True)
class TuningReport:
    windows: tuple[TuningWindowResult, ...]
    sample_rate: int
    config: TuningCheckConfig
    controlled: bool = True

    @property
    def coverage(self) -> float:
        pitched = [row for row in self.windows if not row.window.percussion]
        return sum(row.coverage for row in pitched) / len(pitched) if pitched else 0.0

    @property
    def mean_abs_cents(self) -> float | None:
        errors = [c for row in self.windows if (c := row.absolute_cents) is not None]
        return float(np.mean(errors)) if errors else None

    @property
    def p95_abs_cents(self) -> float | None:
        errors = [c for row in self.windows if (c := row.absolute_cents) is not None]
        return float(np.percentile(errors, 95, method='linear')) if errors else None

    @property
    def failures(self) -> tuple[str, ...]:
        failures = []
        if not any(not row.window.percussion for row in self.windows):
            failures.append('insufficient evidence: no pitched reference windows')
        for row in self.windows:
            if row.window.percussion:
                continue
            if not row.coverage:
                failures.append(f'insufficient evidence: {row.window.name}: {row.exclusion}')
            elif self.controlled:
                error = row.absolute_cents
                if error is not None and _exceeds(error, self.config.max_tone_cents):
                    failures.append(f'tone {row.window.name}: {error:.9g} cents > {self.config.max_tone_cents}')
        for name, observed, limit in (
            ('mean', self.mean_abs_cents, self.config.max_mean_cents),
            ('p95', self.p95_abs_cents, self.config.max_p95_cents),
        ):
            if observed is not None and _exceeds(observed, limit):
                failures.append(f'{name}: {observed:.9g} cents > {limit}')
        return tuple(failures)

    @property
    def passed(self) -> bool:
        return not self.failures

    def require_pass(self) -> None:
        """Hard gate for callers; rejected evidence remains attached to the error."""
        if not self.passed:
            raise TuningCheckError(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            'windows': [row.to_dict() for row in self.windows],
            'sample_rate': self.sample_rate, 'config': asdict(self.config),
            'controlled': self.controlled, 'passed': self.passed, 'failures': list(self.failures),
            'coverage': self.coverage, 'mean_abs_cents': self.mean_abs_cents,
            'p95_abs_cents': self.p95_abs_cents,
            'evidence': 'controlled_monophonic_audio', 'polyphonic_accuracy': 'unmeasured',
            'method': 'normalized-autocorrelation+hann-fft-v1',
        }


class TuningCheckError(ValueError):
    def __init__(self, report: TuningReport):
        self.report = report
        super().__init__('Tuning check failed: ' + '; '.join(report.failures))


def _exceeds(observed: float, limit: float) -> bool:
    # Numerical roundoff only (Hz/log2 conversion), not additional DSP tolerance.
    return observed > limit + 1e-9


def _estimate_f0(
    samples: NDArray[np.float64], sample_rate: int, config: TuningCheckConfig,
) -> tuple[float | None, float, str | None]:
    """Target-free period selection, then interpolated spectral f0 refinement.

    Need at least four periods and 64 samples. The first local autocorrelation
    peak within 95% of the strongest peak avoids choosing a later period multiple.
    Hann/8x zero padding and log-parabolic interpolation refine its fundamental,
    rather than simply choosing the loudest harmonic. Dry, stable, harmonic tones
    only: octave ambiguity, vibrato, mixtures and inharmonic sounds are unproven.
    """
    n = len(samples)
    low = max(2, math.floor(sample_rate / config.max_hz))
    high = min(n // 4, math.ceil(sample_rate / config.min_hz))
    if n < 64 or high <= low:
        return None, 0.0, 'insufficient_samples'
    if not np.isfinite(samples).all():
        return None, 0.0, 'nonfinite_samples'
    # Scale before centering/squaring to avoid overflow, keeping RMS in input units.
    scale = float(np.max(np.abs(samples)))
    if scale == 0:
        return None, 0.0, 'silence'
    x = samples / scale
    x = x - np.mean(x)
    if float(np.sqrt(np.mean(x * x))) <= config.min_rms / scale:
        return None, 0.0, 'silence'
    size = 1 << (2 * n - 1).bit_length()
    spectrum = np.fft.rfft(x, size)
    correlation = np.fft.irfft(spectrum * np.conj(spectrum), size)[:high + 2]
    energy = np.concatenate(([0.0], np.cumsum(x * x)))
    lags = np.arange(high + 2)
    denominator = energy[n - lags] + energy[n] - energy[lags]
    similarity = np.divide(2 * correlation, denominator, out=np.zeros_like(correlation), where=denominator > 0)
    peaks = [lag for lag in range(low, high + 1)
             if similarity[lag] > similarity[lag - 1] and similarity[lag] >= similarity[lag + 1]]
    if not peaks:
        return None, 0.0, 'insufficient_confidence'
    best = max(float(similarity[lag]) for lag in peaks)
    if best <= 0:
        return None, 0.0, 'insufficient_confidence'
    lag = next(lag for lag in peaks if similarity[lag] >= 0.95 * best)
    confidence = float(np.clip(similarity[lag], 0, 1))
    if confidence < config.min_confidence:
        return None, confidence, 'insufficient_confidence'
    left, center, right = similarity[lag - 1:lag + 2]
    period = lag + float(0.5 * (left - right) / (left - 2 * center + right))
    coarse_hz = sample_rate / period
    size = 1 << (8 * n - 1).bit_length()
    magnitudes = np.abs(np.fft.rfft(x * np.hanning(n), size))
    first = max(1, math.ceil(0.9 * coarse_hz * size / sample_rate))
    last = min(len(magnitudes) - 2, math.floor(1.1 * coarse_hz * size / sample_rate))
    peak = first + int(np.argmax(magnitudes[first:last + 1]))
    # No spectral fundamental near the period estimate: do not certify an octave.
    if (magnitudes[peak] < 0.05 * np.max(magnitudes)
            or not magnitudes[peak - 1] < magnitudes[peak] > magnitudes[peak + 1]):
        return None, confidence, 'insufficient_confidence'
    left, center, right = np.log(np.maximum(magnitudes[peak - 1:peak + 2], np.finfo(float).tiny))
    refined = peak + float(0.5 * (left - right) / (left - 2 * center + right))
    measured_hz = refined * sample_rate / size
    if not config.min_hz <= measured_hz <= config.max_hz:
        return None, confidence, 'out_of_range'
    return measured_hz, confidence, None


def analyze_tuning(
    samples: ArrayLike, sample_rate: int, windows: Sequence[ToneWindow], *,
    config: TuningCheckConfig = TuningCheckConfig(), controlled: bool = True,
) -> TuningReport:
    """Measure caller-supplied reference windows without inferring pitch intent.

    Samples are real, normalized mono PCM (no implicit channel averaging). Missing
    or nonfinite audio produces explicit evidence exclusions, never zero errors.
    Percussion is explicitly designated by the caller, not inferred from sound.
    """
    if type(controlled) is not bool:
        raise ValueError('controlled must be a boolean')
    if type(sample_rate) is not int or sample_rate <= 0:
        raise ValueError('sample_rate must be a positive integer')
    if config.max_hz >= sample_rate / 2:
        raise ValueError('max_hz must be below the sample_rate Nyquist frequency')
    audio = np.asarray(samples)
    if audio.ndim != 1 or not np.isrealobj(audio):
        raise ValueError('samples must be a real mono buffer')
    audio = np.asarray(audio, dtype=np.float64)
    results = []
    for window in windows:
        result: tuple[float | None, float, str | None]
        if window.percussion:
            result = (None, 0.0, 'percussion')
        elif window.stop_sample > len(audio):
            result = (None, 0.0, 'insufficient_samples')
        else:
            result = _estimate_f0(audio[window.start_sample:window.stop_sample], sample_rate, config)
        results.append(TuningWindowResult(window, *result))
    return TuningReport(tuple(results), sample_rate, config, controlled)
