"""LLM routing: Gemini first, Groq as a backup when Gemini is unavailable.

The only LLM call left in the pipeline -- discovery.py's company list --
goes through generate_json() here, not to a provider directly. Drafting
no longer uses an LLM at all (see src/templates.py), so this now runs
once per pipeline run instead of once per company.

Why a second provider: Gemini's free tier returned sustained 503s across
several real runs -- server-side overload that no retry tuning fixes.
Groq is a separate service with its own free quota, so it keeps a run
moving while Gemini is struggling.

Routing rules:
  - Gemini is tried first (larger free daily quota for this workload).
  - With Groq configured, Gemini gets fewer retries (FAST_FAIL_RETRIES)
    so a failing call hands off in ~40s instead of ~3 minutes.
  - After Gemini fails outright, it's skipped for GEMINI_COOLDOWN_SECONDS
    and calls go straight to Groq -- during an outage, paying Gemini's
    full retry cycle on every single call would waste most of the run.
  - Without a Groq key, behaviour is exactly as before: Gemini only, with
    its full retry budget.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from src import gemini_client, groq_client
from src.config import GEMINI_MODELS

FAST_FAIL_RETRIES = 3
GEMINI_COOLDOWN_SECONDS = 300.0

_gemini_cooldown_until = 0.0


@dataclass(frozen=True)
class LLMKeys:
    gemini: str
    groq: str | None = None


@dataclass
class ProviderStats:
    gemini_success: int = 0
    groq_success: int = 0
    gemini_failures: int = 0

    def summary(self) -> str:
        return (
            f"LLM calls: {self.gemini_success} via Gemini, {self.groq_success} via Groq "
            f"(Gemini gave up {self.gemini_failures} time(s))"
        )


stats = ProviderStats()


def _reset_state_for_tests() -> None:
    global _gemini_cooldown_until, stats
    _gemini_cooldown_until = 0.0
    stats = ProviderStats()


def generate_json(
    keys: LLMKeys,
    prompt: str,
    schema: dict,
    system_instruction: str | None = None,
    max_output_tokens: int = 4096,
) -> dict:
    global _gemini_cooldown_until

    gemini_error: Exception | None = None
    in_cooldown = keys.groq is not None and time.monotonic() < _gemini_cooldown_until

    if not in_cooldown:
        try:
            result = gemini_client.generate_json(
                GEMINI_MODELS,
                prompt,
                schema,
                keys.gemini,
                system_instruction=system_instruction,
                max_output_tokens=max_output_tokens,
                retries=FAST_FAIL_RETRIES if keys.groq else gemini_client.RETRIES_PER_MODEL,
            )
            stats.gemini_success += 1
            return result
        except RuntimeError as exc:
            gemini_error = exc
            stats.gemini_failures += 1
            if keys.groq is None:
                raise
            _gemini_cooldown_until = time.monotonic() + GEMINI_COOLDOWN_SECONDS
            print(
                f"  Gemini unavailable ({exc}); switching to Groq, "
                f"skipping Gemini for the next {GEMINI_COOLDOWN_SECONDS / 60:.0f} min"
            )

    try:
        result = groq_client.generate_json(
            prompt,
            schema,
            keys.groq,  # type: ignore[arg-type] -- guaranteed non-None on this path
            system_instruction=system_instruction,
            max_output_tokens=max_output_tokens,
        )
        stats.groq_success += 1
        return result
    except RuntimeError as groq_error:
        if in_cooldown:
            # Gemini was skipped, not tried -- it may have recovered, so
            # give it one fast attempt before declaring both down.
            print(f"  Groq failed during Gemini cooldown ({groq_error}); retrying Gemini")
            try:
                result = gemini_client.generate_json(
                    GEMINI_MODELS,
                    prompt,
                    schema,
                    keys.gemini,
                    system_instruction=system_instruction,
                    max_output_tokens=max_output_tokens,
                    retries=FAST_FAIL_RETRIES,
                )
                stats.gemini_success += 1
                _gemini_cooldown_until = 0.0
                return result
            except RuntimeError as exc:
                gemini_error = exc
                stats.gemini_failures += 1
        raise RuntimeError(
            f"Both providers failed. Gemini: {gemini_error}; Groq: {groq_error}"
        ) from groq_error
