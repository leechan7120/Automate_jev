from fastapi.testclient import TestClient

from automate_jev.api import create_app


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
