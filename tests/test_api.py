from fastapi.testclient import TestClient

from automate_jev.api import create_app


async def successful_runner(workflow, provider, session_id):
    assert workflow.workflow_id == "fixture-upload"
    return {
        "provider": provider.upper(),
        "status": "SUCCESS",
        "action_id": workflow.steps[0].action_id,
        "effect_observed": True,
        "journal_state": "COMMITTED",
        "session_id": session_id,
    }


def test_health_and_fixture_video_extraction(tmp_path):
    client = TestClient(create_app(env_path=tmp_path / "missing.env"))
    assert client.get("/health").json() == {"status": "ok"}
    assert "Workflow" in client.get("/").text

    response = client.post(
        "/v1/workflows/extract",
        data={
            "provider": "fixture",
            "workflow_id": "fixture-upload",
            "allowed_root": "D:/compe/skku_AI_hack/demo-data",
        },
        files={"video": ("sample.mp4", b"synthetic-video", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "FIXTURE"
    assert body["execution_authorized"] is False
    assert body["workflow"]["steps"][0]["action_id"] == "smoke-target.fill-required-text"
    assert body["video"]["sha256"].startswith("sha256:")

    review = client.post("/v1/workflows/review", json=body["workflow"])
    assert review.status_code == 200
    reviewed = review.json()
    assert reviewed["reviewed"] is True
    assert reviewed["execution_authorized"] is False
    assert reviewed["review"]["workflow_hash"].startswith("sha256:")


def test_upload_rejects_empty_unsupported_and_invalid_provider(tmp_path):
    client = TestClient(create_app(env_path=tmp_path / "missing.env"))
    base = {
        "workflow_id": "fixture-upload",
        "allowed_root": "D:/compe/skku_AI_hack/demo-data",
    }
    unsupported = client.post(
        "/v1/workflows/extract",
        data={**base, "provider": "fixture"},
        files={"video": ("sample.txt", b"data", "text/plain")},
    )
    assert unsupported.status_code == 415

    empty = client.post(
        "/v1/workflows/extract",
        data={**base, "provider": "fixture"},
        files={"video": ("sample.mp4", b"", "video/mp4")},
    )
    assert empty.status_code == 400

    invalid_provider = client.post(
        "/v1/workflows/extract",
        data={**base, "provider": "unknown"},
        files={"video": ("sample.mp4", b"data", "video/mp4")},
    )
    assert invalid_provider.status_code == 400


def test_review_token_exchanges_once_for_successful_execution(tmp_path):
    client = TestClient(
        create_app(
            env_path=tmp_path / "missing.env",
            execution_runner=successful_runner,
            journal_root=tmp_path / "journal",
        )
    )
    extracted = client.post(
        "/v1/workflows/extract",
        data={
            "provider": "fixture",
            "workflow_id": "fixture-upload",
            "allowed_root": "D:/compe/skku_AI_hack/demo-data",
        },
        files={"video": ("sample.mp4", b"synthetic-video", "video/mp4")},
    ).json()
    workflow = extracted["workflow"]
    review = client.post("/v1/workflows/review", json=workflow).json()["review"]

    response = client.post(
        "/v1/executions",
        json={"workflow": workflow, "review": review, "provider": "fixture"},
    )

    assert response.status_code == 202
    session_id = response.json()["session_id"]
    status = client.get(f"/v1/executions/{session_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "SUCCESS"
    assert status.json()["result"]["journal_state"] == "COMMITTED"

    replay = client.post(
        "/v1/executions",
        json={"workflow": workflow, "review": review, "provider": "fixture"},
    )
    assert replay.status_code == 400
    assert "already been exchanged" in replay.json()["detail"]


def test_execution_rejects_workflow_changed_after_review(tmp_path):
    client = TestClient(create_app(execution_runner=successful_runner))
    extracted = client.post(
        "/v1/workflows/extract",
        data={
            "provider": "fixture",
            "workflow_id": "fixture-upload",
            "allowed_root": "D:/demo",
        },
        files={"video": ("sample.mp4", b"synthetic-video", "video/mp4")},
    ).json()
    workflow = extracted["workflow"]
    review = client.post("/v1/workflows/review", json=workflow).json()["review"]
    workflow["allowed_root"] = "D:/changed"

    response = client.post(
        "/v1/executions",
        json={"workflow": workflow, "review": review, "provider": "fixture"},
    )

    assert response.status_code == 400
    assert "hash mismatch" in response.json()["detail"]


def test_extract_validates_optional_series4_sidecar(tmp_path):
    client = TestClient(create_app())
    response = client.post(
        "/v1/workflows/extract",
        data={
            "provider": "fixture",
            "workflow_id": "fixture-upload",
            "allowed_root": "D:/demo",
        },
        files={
            "video": ("sample.mp4", b"synthetic-video", "video/mp4"),
            "sidecar": (
                "sample.mp4.series4.json",
                b'{"version":2,"events":[{"actionKind":"TextEntry","actionText":"DEMO_APPROVED"}]}',
                "application/json",
            ),
        },
    )

    assert response.status_code == 200
    assert response.json()["sidecar"]["action_ids"] == ["smoke-target.fill-required-text"]
