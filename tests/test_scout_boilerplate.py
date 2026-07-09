"""Regression tests for scan noise: license boilerplate, styled caps words,
and repository artifact names must not become terminology candidates.

Motivated by a real scan of the Apache Airflow docs corpus, where the top
candidates were ANY, ASF, KIND, BASIS, CONDITIONS, NOTICE, WARRANTIES, and
WITHOUT - all sourced from the ASF license header repeated in every file.
"""

from __future__ import annotations

from agent_lexicon.ingest import IngestDocument
from agent_lexicon.scout.candidates import discover_scout_candidates

_ASF_HEADER = (
    "Licensed under the Apache License, Version 2.0 (the \"License\");\n"
    "distributed under the License is distributed on an \"AS IS\" BASIS,\n"
    "WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n"
    "See the License for the specific language governing permissions and\n"
    "limitations under the License.\n"
)


def _doc(path: str, body: str = "", *, text: str | None = None) -> IngestDocument:
    content = text if text is not None else _ASF_HEADER + body
    return IngestDocument(
        source_path=path,
        relative_path=path,
        text=content,
        kind="markdown",
        size_bytes=len(content.encode("utf-8")),
        line_count=max(1, content.count("\n")),
        sha256="a" * 64,
    )


def _surfaces(report) -> set[str]:
    return {candidate.normalized_surface for candidate in report.candidates}


def test_repeated_license_header_is_not_terminology() -> None:
    documents = [
        _doc("docs/a.rst", "The DagProcessor parses every DagRun before the SchedulerJob starts.\n"),
        _doc("docs/b.rst", "A DagRun is created by the SchedulerJob from the DagProcessor output.\n"),
        _doc("docs/c.rst", "Use XCom to pass data; the DagProcessor and SchedulerJob read XCom too.\n"),
        _doc("docs/d.rst", "TaskInstance state is owned by the SchedulerJob, not the DagProcessor.\n"),
        _doc("docs/e.rst", "XCom values reach every TaskInstance through the DagRun context.\n"),
    ]
    report = discover_scout_candidates(documents)
    surfaces = _surfaces(report)

    for boilerplate_word in ("warranties", "conditions", "basis", "kind", "any", "without", "implied"):
        assert boilerplate_word not in surfaces, boilerplate_word

    for real_term in ("dagprocessor", "dagrun", "schedulerjob", "xcom", "taskinstance"):
        assert real_term in surfaces, real_term

    assert report.metadata["boilerplate_line_patterns"] >= 3
    assert report.metadata["boilerplate_lines_skipped"] >= 15


def test_caps_english_word_outside_boilerplate_is_rejected() -> None:
    documents = [
        _doc("docs/one.md", text="WITHOUT the RateLimiter, NOTICE how the FluxCapacitor stalls under ANY load.\n"),
    ]
    report = discover_scout_candidates(documents)
    surfaces = _surfaces(report)
    assert "without" not in surfaces
    assert "notice" not in surfaces
    assert "any" not in surfaces
    assert "ratelimiter" in surfaces
    assert "fluxcapacitor" in surfaces


def test_repository_artifact_names_are_rejected() -> None:
    documents = [
        _doc("README.md", text="See README and CHANGELOG. The ContextSpace snapshot rules live in CONTRIBUTING.\n"),
    ]
    report = discover_scout_candidates(documents)
    surfaces = _surfaces(report)
    assert "readme" not in surfaces
    assert "changelog" not in surfaces
    assert "contributing" not in surfaces
    assert "contextspace" in surfaces


def test_real_acronyms_survive_upper_stopword_filter() -> None:
    documents = [
        _doc("docs/api.md", text="The GraphQL API uses JWT auth; RBAC rules gate every DAG mutation.\n"),
    ]
    report = discover_scout_candidates(documents)
    surfaces = _surfaces(report)
    assert "jwt" in surfaces
    assert "rbac" in surfaces
    assert "dag" in surfaces


def test_small_corpora_are_not_boilerplate_filtered() -> None:
    shared = "Install the FluxCapacitor before running the TemporalRouter.\n"
    documents = [
        _doc("a.md", text=shared),
        _doc("b.md", text=shared),
    ]
    report = discover_scout_candidates(documents)
    surfaces = _surfaces(report)
    assert "fluxcapacitor" in surfaces
    assert report.metadata["boilerplate_line_patterns"] == 0


def test_markup_and_abbreviation_noise_is_rejected() -> None:
    documents = [
        _doc(
            "docs/guide.rst",
            text=(
                "Use :class:`~airflow.sdk.definitions.param.Param` (e.g. via i.e. the API).\n"
                ":header-rows: 1\n"
                "See logging_mixin.py:188 and raw.githubusercontent.com/apache/airflow/constraints for details.\n"
                "Style uses stroke-width:2px on the DagProcessor node.\n"
            ),
        ),
    ]
    report = discover_scout_candidates(documents)
    surfaces = _surfaces(report)
    assert "e.g" not in surfaces
    assert "i.e" not in surfaces
    assert "header-rows" not in surfaces
    assert "logging_mixin.py:188" not in surfaces
    assert "raw.githubusercontent.com/apache/airflow/constraints" not in surfaces
    assert "stroke-width:2px" not in surfaces
    # The tilde cross-reference resolves to the plain identifier, once.
    assert "airflow.sdk.definitions.param.param" in surfaces
    assert "~airflow.sdk.definitions.param.param" not in surfaces
    assert "dagprocessor" in surfaces


def test_import_lines_are_skipped_and_third_party_roots_rejected() -> None:
    documents = [
        _doc(
            "myproject/api/routes.py",
            text=(
                "from fastapi import status\n"
                "import sqlalchemy as sa\n"
                "from alembic import op\n"
                "from myproject.models.workflow import WorkflowRun\n"
                "raise HTTPException(status.HTTP_403_FORBIDDEN)\n"
                "col = sa.Column(sa.String)\n"
                "op.create_table('runs')\n"
                "run = WorkflowRun(run_id=run_id, workflow.run_state)\n"
            ),
        ),
        _doc(
            "myproject/models/workflow.py",
            text="class WorkflowRun:\n    run_state = 'queued'  # WorkflowRun owns run_state and run_id\n",
        ),
    ]
    report = discover_scout_candidates(documents)
    surfaces = _surfaces(report)
    # Third-party receivers are library API, not project vocabulary.
    assert "status.http_403_forbidden" not in surfaces
    assert "sa.column" not in surfaces
    assert "sa.string" not in surfaces
    assert "op.create_table" not in surfaces
    # Import module paths do not become candidates by themselves.
    assert "myproject.models.workflow" not in surfaces
    assert "fastapi" not in surfaces
    # Project vocabulary survives, including dotted self-reference.
    assert "workflowrun" in surfaces
    assert "run_state" in surfaces
    assert "run_id" in surfaces
    assert report.metadata["import_lines_skipped"] >= 4
    assert report.metadata["external_import_roots"] >= 3


def test_self_and_cls_receiver_prefixes_are_stripped() -> None:
    documents = [
        _doc(
            "app/session.py",
            text="value = self.context_space\nother = cls.snapshot_ref\nplain = self.\n",
        ),
    ]
    report = discover_scout_candidates(documents)
    surfaces = _surfaces(report)
    assert "context_space" in surfaces
    assert "self.context_space" not in surfaces
    assert "snapshot_ref" in surfaces
    assert "cls.snapshot_ref" not in surfaces


def test_prose_starting_with_import_is_not_swallowed() -> None:
    documents = [
        _doc(
            "docs/guide.md",
            text="import the FluxCapacitor lexicon before running the TemporalRouter.\n",
        ),
    ]
    report = discover_scout_candidates(documents)
    surfaces = _surfaces(report)
    assert "fluxcapacitor" in surfaces
