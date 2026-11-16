"""API health and scaffold endpoint tests."""

from fastapi.testclient import TestClient

from openclip.api.app import create_app

client = TestClient(create_app())


def test_health() -> None:
    """The local liveness endpoint is implemented."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_real_endpoints_behave() -> None:
    """Implemented endpoints return real responses or contract errors."""
    # caption styles are real data
    response = client.get("/api/caption-styles")
    assert response.status_code == 200 and isinstance(response.json(), list)
    # unknown job id -> contract error shape
    response = client.get("/api/jobs/" + "0" * 32)
    assert response.status_code == 404
    assert set(response.json()) == {"code", "message"}
    # invalid clip/job paths -> 404, not 501
    response = client.get("/api/jobs/" + "0" * 32 + "/clips/c1/video")
    assert response.status_code == 404
    # jobs list works
    response = client.get("/api/jobs")
    assert response.status_code == 200 and isinstance(response.json(), list)


def test_openapi_contains_every_contract_model() -> None:
    """The generated schema exposes each public model to API clients."""
    schemas = create_app().openapi()["components"]["schemas"]
    expected = {
        "Word",
        "Segment",
        "Transcript",
        "MediaInfo",
        "CandidateFeatures",
        "Candidate",
        "Clip",
        "CropKeyframe",
        "CropPath",
        "CaptionBackground",
        "CaptionStyle",
        "Stage",
        "JobStatus",
        "JobSource",
        "JobOptions",
        "Job",
        "JobError",
        "HealthResponse",
        "UploadResponse",
        "JobCreate",
        "ClipUpdate",
        "ErrorResponse",
    }
    assert expected <= set(schemas)
