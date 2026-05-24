"""Sermon-pack deliverables generator.

Pure data transformation: SQLite in, three markdown files out. No Flask,
no LLM. See docs/superpowers/specs/2026-05-23-sermon-pack-deliverables-design.md.

Source precedence per field:
  1. session_decisions  (the 9 keyed homiletical commitments)
  2. outline_nodes      (the structured outline tree)
  3. card_notepads      (Bryan's per-phase notepad work)
  4. card_responses     (Bryan's per-phase card answers)
  5. conversation_messages (mined when nothing above has the field)

Each resolved field carries a `source` tag; per-artifact mining-percentage
is recorded to sermon_pack_metrics. Driving mining-% to zero is the
acceptance metric for the follow-on upstream-wiring spec.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SOURCES = ('decisions', 'outline', 'notepad', 'card', 'mined', 'missing')


@dataclass
class FieldResult:
    """One resolved artifact field.

    `value` is always a string ready for template substitution. When the
    field couldn't be resolved from any source, value is the literal
    `[missing: <field>]` marker and source is "missing"."""
    value: str
    source: str


def missing_field(name: str) -> FieldResult:
    return FieldResult(value=f'[missing: {name}]', source='missing')
