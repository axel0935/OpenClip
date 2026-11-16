"""API health and scaffold endpoint tests."""

from fastapi.testclient import TestClient

from openclip.api.app import create_app

client = TestClient(create_app())


def test_health() -> None:
    """The local liveness endpoint is implemented."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_stub_routes_return_standard_error() -> None:
    """Every declared future endpoint consistently reports the scaffold state."""
    requests = [
        ("get", "/api/caption-styles", {}),
        ("post", "/api/uploads", {}),
        ("post", "/api/jobs", {"json": {"source": {"type": "upload", "value": "u1"}}}),
        ("get", "/api/jobs", {}),
        ("get", "/api/jobs/job-1", {}),
        ("delete", "/api/jobs/job-1", {}),
        ("get", "/api/jobs/job-1/events", {}),
        ("patch", "/api/jobs/job-1/clips/clip-1", {"json": {"start": 1, "end": 2}}),
        ("get", "/api/jobs/job-1/clips/clip-1/video", {}),
        ("get", "/api/jobs/job-1/clips/clip-1/thumbnail", {}),
        ("get", "/api/jobs/job-1/clips/clip-1/preview", {}),
        ("get", "/api/jobs/job-1/clips/clip-1/captions.ass", {}),
        ("get", "/api/jobs/job-1/download.zip", {}),
    ]
    for method, path, kwargs in requests:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 501
        assert response.json() == {"code": "not_implemented", "message": "not implemented yet"}


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
