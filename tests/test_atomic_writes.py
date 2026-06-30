from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from agent_lexicon import atomic_write_text, lexicon_from_dict
from agent_lexicon.core import files as atomic_files
from agent_lexicon.dictionary.merge import write_merged_lexicon_json
from agent_lexicon.dictionary import merge_lexicons


def _payload(term_id: str = "billing.credit_limit", canonical: str = "credit limit") -> dict:
    return {
        "version": 1,
        "terms": [
            {
                "id": term_id,
                "canonical": canonical,
                "aliases": [
                    {"surface": canonical},
                ],
            }
        ],
    }


def test_atomic_write_text_replaces_complete_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "artifact.json"

    returned = atomic_write_text(target, '{"version": 1}\n')

    assert returned == target
    assert target.read_text(encoding="utf-8") == '{"version": 1}\n'
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_atomic_write_text_preserves_previous_file_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "artifact.json"
    target.write_text('{"version": "old"}\n', encoding="utf-8")

    def fail_replace(_src: object, _dst: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_files.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_text(target, '{"version": "new"}\n')

    assert target.read_text(encoding="utf-8") == '{"version": "old"}\n'
    assert not list(tmp_path.glob(f".{target.name}.*.tmp"))


def test_atomic_write_text_does_not_expose_partial_json_to_readers(tmp_path: Path) -> None:
    target = tmp_path / "snapshot.json"
    atomic_write_text(target, json.dumps({"generation": -1, "values": []}) + "\n")
    stop = threading.Event()
    ready = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        ready.set()
        for generation in range(120):
            payload = {
                "generation": generation,
                "values": list(range(500)),
                "text": "x" * 4096,
            }
            atomic_write_text(target, json.dumps(payload, sort_keys=True) + "\n")
        stop.set()

    def reader() -> None:
        ready.wait(timeout=5)
        while not stop.is_set():
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
                assert isinstance(payload.get("generation"), int)
            except BaseException as exc:  # pragma: no cover - only used on concurrency failure
                errors.append(exc)
                stop.set()
                return

    writer_thread = threading.Thread(target=writer)
    reader_threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in reader_threads:
        thread.start()
    writer_thread.start()
    writer_thread.join(timeout=10)
    stop.set()
    for thread in reader_threads:
        thread.join(timeout=10)

    assert not writer_thread.is_alive()
    assert not errors
    json.loads(target.read_text(encoding="utf-8"))


def test_write_merged_lexicon_json_preserves_existing_file_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = lexicon_from_dict(_payload())
    ours_payload = _payload()
    ours_payload["terms"][0]["aliases"].append({"surface": "customer cap"})
    ours = lexicon_from_dict(ours_payload)
    theirs = lexicon_from_dict(_payload())
    report = merge_lexicons(base, ours, theirs)
    output = tmp_path / "merged.json"
    output.write_text('{"version": "old"}\n', encoding="utf-8")

    def fail_replace(_src: object, _dst: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_files.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        write_merged_lexicon_json(report, output)

    assert output.read_text(encoding="utf-8") == '{"version": "old"}\n'
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))
