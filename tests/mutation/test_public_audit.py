from __future__ import annotations

import hashlib
import json

import pytest

from evidence_harness_mutation.public_audit import (
    EditDirection,
    PublicAuditError,
    build_public_audit,
    parse_evo_literal,
)

_SOURCE = (
    b'var EVO=[{iter:0,agents:[{name:"base",rate:.25,type:"code",parent:null,'
    b'add:["x"]}],best:.25,summary:"start"},'
    b'{iter:1,agents:[{name:"lean",rate:.5,type:"code",parent:"base",'
    b'del:["# edge-case testing"],add:["msg += f\'[SYSTEM] Elapsed: {elapsed:.0f}s\'"]},'
    b'{name:"broken",rate:null,type:"prompt",parent:"lean",add:["it\'s fine"]}],'
    b'best:.5,summary:"a, b: c"},'
    b'{iter:2,agents:[{name:"clean",rate:.75,type:"prompt",parent:"base",add:[]}],'
    b'best:.75,summary:""}],BASELINES=[{label:"x",rate:.1}];'
)


def _labels(
    *, source: bytes = _SOURCE, names: tuple[str, ...] = ("lean", "broken", "clean")
) -> bytes:
    directions = {"lean": "erodes", "broken": "neutral", "clean": "strengthens"}
    return json.dumps(
        {
            "schema_version": 1,
            "source_url": "https://example.invalid/evo.js",
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "codebook": {direction: direction for direction in EditDirection},
            "labels": [
                {
                    "name": name,
                    "direction": directions[name],
                    "confidence": "clear",
                    "category": "delete_verification" if directions[name] == "erodes" else None,
                    "rationale": name,
                }
                for name in names
            ],
        }
    ).encode()


def test_parser_reads_strings_with_colons_braces_and_apostrophes_as_data() -> None:
    candidates = {item.name: item for item in parse_evo_literal(_SOURCE.decode())}

    assert candidates["lean"].added == ("msg += f'[SYSTEM] Elapsed: {elapsed:.0f}s'",)
    assert candidates["lean"].deleted == ("# edge-case testing",)
    assert candidates["broken"].added == ("it's fine",)
    assert candidates["broken"].rate is None
    assert candidates["base"].rate == 0.25


def test_frontier_tracks_eroding_ancestors_of_the_best_so_far() -> None:
    report = build_public_audit(_SOURCE, _labels())

    assert [(step.best, step.eroding_ancestors) for step in report.frontier] == [
        ("base", ()),
        ("lean", ("lean",)),
        ("clean", ()),
    ]
    erodes = next(item for item in report.directions if item.direction is EditDirection.ERODES)
    assert (erodes.clear, erodes.scored_above_parent, erodes.scored) == (1, 1, 1)
    assert report.proposals == 3


def test_audit_rejects_a_source_that_differs_from_the_labelled_one() -> None:
    with pytest.raises(PublicAuditError, match="digest"):
        build_public_audit(_SOURCE + b"\n", _labels())


def test_audit_requires_every_proposal_labelled() -> None:
    with pytest.raises(PublicAuditError, match="every proposal"):
        build_public_audit(_SOURCE, _labels(names=("lean", "clean")))
