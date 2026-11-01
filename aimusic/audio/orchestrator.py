"""Stage DAG, content-hash cache, and budget guard (M1 deterministic spine).

Stages communicate only via files + JSON sidecars — never in-memory objects
across the DAG boundary. Restyle / scoring / mixmaster remain gated off.
"""

from __future__ import annotations

import json
import shutil
import platform
from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from aimusic.audio.from_score import AudioPackageContext, from_score, validate_package
from aimusic.audio.manifest import content_hash_files
from aimusic.audio.cache import cached_stage
from aimusic.audio.render.registry import file_digest
from aimusic.audio.render.stem_publication import pin_output
from aimusic.core.render_package import ContractViolation


STAGE_ORDER = (
    "analysis",
    "expressivization",
    "render",
    "prompts",
    "restyle",
    "scoring",
    "mixmaster",
)


@dataclass
class BudgetGuard:
    max_endpoint_calls: int = 40
    max_usd_estimate: float = 10.0
    calls_used: int = 0
    usd_used: float = 0.0

    def can_spend(self, *, calls: int = 1, usd: float = 0.0) -> bool:
        return (
            self.calls_used + calls <= self.max_endpoint_calls
            and self.usd_used + usd <= self.max_usd_estimate
        )

    def record(self, *, calls: int = 1, usd: float = 0.0) -> None:
        if not self.can_spend(calls=calls, usd=usd):
            raise RuntimeError(
                f"budget exceeded: calls={self.calls_used}/{self.max_endpoint_calls}, "
                f"usd={self.usd_used:.2f}/{self.max_usd_estimate:.2f}"
            )
        self.calls_used += calls
        self.usd_used += usd


@dataclass
class Orchestrator:
    """Pipeline runner with content-addressed cache under cache_dir."""

    config: dict[str, Any]
    cache_dir: Path
    budget: BudgetGuard = field(default_factory=BudgetGuard)
    cache_hits: dict[str, int] = field(default_factory=dict)
    cache_misses: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        budget_cfg = self.config.get("budget", {})
        self.budget = BudgetGuard(
            max_endpoint_calls=int(budget_cfg.get("max_endpoint_calls", 40)),
            max_usd_estimate=float(budget_cfg.get("max_usd_estimate", 10.0)),
        )

    def cache_key(self, stage: str, input_paths: list[Path | str], subset: dict[str, Any]) -> str:
        return content_hash_files(
            [Path(p) for p in input_paths],
            stage,
            json.dumps(subset, sort_keys=True),
            str(self.config.get("code_version", "0.1.0")),
        )

    def cached_path(self, stage: str, key: str, suffix: str) -> Path:
        return self.cache_dir / stage / f"{key[:16]}{suffix}"

    @staticmethod
    def _copy_stage_artifacts(
        cached: Path, dest: Path, *sidecar_suffixes: str
    ) -> None:
        """Copy a stage's primary artifact plus every existing sidecar.

        Stages emit a primary file (e.g. ``<stem>.analysis.json``,
        ``<stem>.expressive.mid``) and one or more sidecars (e.g.
        ``<stem>.analysis.json.json`` for the provenance manifest,
        ``<stem>.expressive.mid.diff.json`` for the note-diff report).
        Both warm and cold cache paths must reproduce the same set of
        files in the user output directory so downstream consumers (and
        manifest reconciliation) see a complete artifact set regardless
        of whether the stage ran or hit the cache.
        """
        shutil.copy2(cached, dest)
        for suffix in sidecar_suffixes:
            cached_sidecar = Path(str(cached) + suffix)
            dest_sidecar = Path(str(dest) + suffix)
            if cached_sidecar.is_file():
                shutil.copy2(cached_sidecar, dest_sidecar)

    def _cached_stage(self, stage: str, identity: dict[str, Any], names: list[str],
                      produce: Callable[[Path], Any]) -> Path:
        directory, hit = cached_stage(self.cache_dir / stage, identity, names, produce)
        counts = self.cache_hits if hit else self.cache_misses
        counts[stage] = counts.get(stage, 0) + 1
        return directory

    def _load_groove_spec(self) -> dict[str, Any]:
        groove_cfg = self.config.get("groove", {})
        spec_path = groove_cfg.get("spec_path") or groove_cfg.get("spec_file")
        if spec_path:
            p = Path(spec_path)
            if not p.is_file():
                repo_root = Path(__file__).resolve().parents[2]
                candidate = repo_root / spec_path
                if candidate.is_file():
                    p = candidate
            with p.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise ValueError(f"groove spec must be a mapping: {p}")
            return data
        if "spec" in groove_cfg and isinstance(groove_cfg["spec"], dict):
            return groove_cfg["spec"]
        return {
            "swing": {
                "ratio": 0.58,
                "subdivision": 8,
                "applies_to": ["drums", "bass", "comp"],
            },
            "velocity": {
                "accent_map_16": [1.0, 0.7, 0.85, 0.7] * 4,
            },
        }

    def run(
        self, midi_path: Path | str | AudioPackageContext, *,
        output_dir: Path | str | None = None,
    ) -> Path:
        """Execute analysis → expressivization → render (M1 spine)."""
        context = None
        validation = None
        if isinstance(midi_path, AudioPackageContext):
            # Re-open before cache access: files may have changed after admission.
            context = from_score(midi_path.package.root)
            if context.package.content_hash != midi_path.package.content_hash:
                raise ContractViolation(
                    f"package identity changed since admission: expected={midi_path.package.content_hash}, "
                    f"actual={context.package.content_hash}; reload the context"
                )
            validation = validate_package(context)
            midi_path = context.package.midi_path
        midi_path = Path(midi_path)
        if not midi_path.is_file():
            raise FileNotFoundError(f"MIDI file not found: {midi_path}")

        out = (
            Path(output_dir)
            if output_dir
            else Path(self.config.get("paths", {}).get("output_dir", "outputs/audio"))
        )
        out.mkdir(parents=True, exist_ok=True)
        stages = self.config.get("stages", {})
        code_version = str(self.config.get("code_version", "0.1.0"))

        from aimusic.audio.analysis.from_midi import analyze_midi
        from aimusic.audio.groove.apply import apply_groove
        from aimusic.audio.render.stems import (
            STEM_LAYOUT_VERSION, copy_stem_artifacts, prepare_stem_render, render_role_stems,
        )

        code_identity = {str(p.relative_to(Path(__file__).parent)): file_digest(p)
                         for p in sorted(Path(__file__).parent.rglob('*.py'))}
        runtime = {'python': platform.python_version(), 'platform': platform.platform()}
        source_identity = {'midi_sha256': file_digest(midi_path),
                           **(context.verified_identity if context else {})}
        if context:
            source_identity['structure_sha256'] = file_digest(context.package.structure_path)
            source_identity['tuning_sha256'] = file_digest(context.package.tuning_path)
        analysis_doc: dict[str, Any] | None = None
        expressive_midi = midi_path

        if context is not None:
            analysis_doc = context.analysis_document()
            if validation is not None:
                analysis_doc["tracks"] = list(validation.reconciliation.tracks)
                (out / f"{midi_path.stem}.reconciliation.json").write_text(
                    json.dumps(validation.reconciliation.to_dict(), indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            (out / f"{midi_path.stem}.analysis.json").write_text(
                json.dumps(analysis_doc, indent=2, sort_keys=True), encoding="utf-8"
            )
        elif stages.get("analysis", True):
            identity: dict[str, Any] = {'source': source_identity, 'code': code_identity, 'runtime': runtime,
                        'code_version': code_version}
            cached = self._cached_stage('analysis', identity, ['analysis.json', 'analysis.json.json'],
                                        lambda directory: analyze_midi(midi_path, config=self.config,
                                                                       output_path=directory / 'analysis.json'))
            dest = out / f"{midi_path.stem}.analysis.json"
            self._copy_stage_artifacts(cached / 'analysis.json', dest, '.json')
            analysis_doc = json.loads(dest.read_text(encoding='utf-8'))

        if stages.get("expressivization", True):
            groove_spec = self._load_groove_spec()
            light = bool(self.config.get("groove", {}).get("light_cleanup", False))
            identity = {'source': source_identity, 'groove': groove_spec, 'light_cleanup': light,
                        'analysis': _semantic_analysis(analysis_doc), 'code': code_identity,
                        'runtime': runtime, 'code_version': code_version}
            names = ['expressive.mid', 'expressive.mid.diff.json', 'expressive.mid.json']
            cached = self._cached_stage('expressivization', identity, names,
                lambda directory: apply_groove(midi_path, groove_spec, analysis=analysis_doc,
                                                output_path=directory / 'expressive.mid',
                                                config=self.config, light_cleanup=light))
            dest = out / f"{midi_path.stem}.expressive.mid"
            self._copy_stage_artifacts(cached / 'expressive.mid', dest, '.diff.json', '.json')
            expressive_midi = dest

        if stages.get("render", True):
            plan = prepare_stem_render(expressive_midi, config=self.config, analysis=analysis_doc)
            effective = deepcopy(plan.config)
            effective.pop('source_path', None)
            effective['paths'] = {k: v for k, v in effective.get('paths', {}).items()
                                  if k not in ('cache_dir', 'output_dir', 'soundfont')}
            for key in ('spec_path', 'spec_file'):
                effective.get('groove', {}).pop(key, None)
            if stages.get('expressivization', True):
                effective.setdefault('groove', {})['spec'] = groove_spec
            render_subset = {
                'stem_layout_version': STEM_LAYOUT_VERSION, 'source': source_identity,
                'expressive_midi_sha256': file_digest(expressive_midi),
                'analysis': _semantic_analysis(analysis_doc), 'effective_config': effective,
                'application_backend': {'requested_backend': 'native', 'effective_backend': 'native',
                                        'fallback_reason': None},
                'renderers': [dict(choice.identity(), source_track_index=stem.source_track_index,
                                   role=stem.role, role_source=stem.role_source)
                              for stem, choice in zip(plan.stems, plan.renderers)],
                'duration_sec': plan.duration_sec, 'release_tail_sec': plan.release_tail_sec,
                'code': code_identity, 'runtime': runtime,
            }

            def produce(directory: Path) -> None:
                render_role_stems(expressive_midi, output_dir=directory, plan=plan)
                (directory / 'render.json').write_text(json.dumps(render_subset, indent=2, sort_keys=True), encoding='utf-8')

            with pin_output(out) as root:
                root.check_new_files(['render.json'])
                cached_dir = self._cached_stage('render', render_subset, plan.names + ['render.json'], produce)
                # Recheck the copied snapshot before publishing it; manifest last.
                copy_stem_artifacts(cached_dir, out / 'stems', verify_cache=True)
                root.publish(out / 'stems', ['render.json'])

        return out


def _semantic_analysis(analysis: dict[str, Any] | None) -> dict[str, Any]:
    """Source paths are display provenance, not semantic content identity."""
    return {k: v for k, v in (analysis or {}).items() if k != 'source_midi'}


def load_config(path: Path | str) -> dict[str, Any]:
    """Load YAML config (default.yaml or a profile)."""
    with Path(path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise TypeError(f"config root must be a mapping: {path}")
    return data


def merge_configs(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Retain the orchestrator API while sharing profile merge semantics."""
    from aimusic.audio.config import merge_configs as merge

    return merge(base, override)


def audio_config_to_dict(config: Any) -> dict[str, Any]:
    """Build orchestrator dict config from frozen AudioConfig (+ defaults)."""
    from aimusic.audio.config import AudioConfig

    if not isinstance(config, AudioConfig):
        raise TypeError("expected AudioConfig")
    return config.to_dict()
