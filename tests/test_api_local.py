"""Full job flow through the API in local mode."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from aggtool.api.app import create_app
from aggtool.service import build_service
from aggtool.settings import Settings


@pytest.fixture
def client(tmp_path):
    settings = Settings(mode="local", local_data_dir=tmp_path, max_upload_bytes=10_000)
    service = build_service(settings)
    service.queue._sync = True  # type: ignore[attr-defined]
    return TestClient(create_app(settings, service))


def _create(client, **overrides):
    body = {"primary_filename": "tmo.csv", "lookup_filename": "csca.csv"} | overrides
    resp = client.post("/jobs", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload(client, target, data: bytes, name: str):
    resp = client.post(target["url"], data=target["fields"], files={"file": (name, data)})
    assert resp.status_code == 204, resp.text


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_profiles(client):
    assert client.get("/profiles").json()[0]["output"][-1] == "Lease Holder"


def test_full_flow(client, primary_bytes, lookup_bytes):
    created = _create(client)
    job_id = created["job"]["job_id"]
    assert created["job"]["status"] == "PENDING_UPLOAD"
    _upload(client, created["uploads"]["primary"], primary_bytes, "tmo.csv")
    _upload(client, created["uploads"]["lookup"], lookup_bytes, "csca.csv")

    assert client.post(f"/jobs/{job_id}/submit").status_code == 202
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "SUCCEEDED", job
    assert job["summary"]["output_rows"] == 6
    assert "key" not in str(job["filenames"])

    for artifact in ("xlsx", "csv", "report"):
        link = client.get(f"/jobs/{job_id}/download", params={"artifact": artifact}).json()
        file = client.get(link["url"])
        assert file.status_code == 200 and file.content
    csv_text = client.get(client.get(f"/jobs/{job_id}/download?artifact=csv").json()["url"]).text
    assert "22222,Acme Messaging,Northwind Alerts,Northwind Traders" in csv_text

    assert [j["job_id"] for j in client.get("/jobs").json()] == [job_id]
    # Submitting twice is rejected.
    assert client.post(f"/jobs/{job_id}/submit").status_code == 409


def test_bad_input_marks_job_failed_with_reason(client, lookup_bytes):
    created = _create(client)
    job_id = created["job"]["job_id"]
    _upload(client, created["uploads"]["primary"], b"Wrong,Header\n1,2\n", "tmo.csv")
    _upload(client, created["uploads"]["lookup"], lookup_bytes, "csca.csv")
    client.post(f"/jobs/{job_id}/submit")
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "FAILED"
    assert "no header row" in job["error"]
    assert client.get(f"/jobs/{job_id}/download").status_code == 409


def test_submit_requires_both_uploads(client, primary_bytes):
    created = _create(client)
    _upload(client, created["uploads"]["primary"], primary_bytes, "tmo.csv")
    resp = client.post(f"/jobs/{created['job']['job_id']}/submit")
    assert resp.status_code == 409 and "lookup" in resp.json()["detail"]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"primary_filename": "a.pdf", "lookup_filename": "b.csv"}, 422),
        ({"primary_filename": "a.csv", "lookup_filename": "b.csv", "profile": "nope"}, 422),
    ],
)
def test_create_validation(client, body, status):
    assert client.post("/jobs", json=body).status_code == status


def test_unknown_job_and_artifact(client):
    assert client.get("/jobs/missing").status_code == 404
    assert client.get("/jobs/missing/download?artifact=exe").status_code == 422


def test_local_upload_guards(client):
    assert (
        client.post(
            "/_local/upload", data={"key": "results/x"}, files={"file": ("a", b"1")}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/_local/upload", data={"key": "uploads/x"}, files={"file": ("a", b"1" * 20_000)}
        ).status_code
        == 400
    )
    assert client.get("/_local/files/uploads/x").status_code == 404
    assert client.get("/_local/files/results/none").status_code == 404


def test_background_queue_runs_job(tmp_path, primary_bytes, lookup_bytes):
    settings = Settings(mode="local", local_data_dir=tmp_path)
    client = TestClient(create_app(settings, build_service(settings)))
    created = _create(client)
    _upload(client, created["uploads"]["primary"], primary_bytes, "tmo.csv")
    _upload(client, created["uploads"]["lookup"], lookup_bytes, "csca.csv")
    job_id = created["job"]["job_id"]
    client.post(f"/jobs/{job_id}/submit")
    for _ in range(100):
        if client.get(f"/jobs/{job_id}").json()["status"] == "SUCCEEDED":
            break
        time.sleep(0.05)
    assert client.get(f"/jobs/{job_id}").json()["status"] == "SUCCEEDED"
