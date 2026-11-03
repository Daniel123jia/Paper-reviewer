import sqlite3

from fastapi.testclient import TestClient

from backend.app import create_app


def test_usage_summary_separates_known_cost_from_unpriced_calls(tmp_path):
    client = TestClient(create_app(tmp_path))
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO model_calls VALUES ('c1', 'conversation', 'model-a', 100, 20, 0.002, 900, 'completed', datetime('now'))")
        db.execute("INSERT INTO review_model_calls VALUES ('r1', 'run', 'propose', 'model-a', 50, 10, NULL, 700, 'failed')")
        db.execute("INSERT INTO draft_model_calls VALUES ('d1', 'case', 'draft', 'model-b', 80, 30, 0.003, 1200, 'completed')")
    response = client.get("/api/usage/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == {"calls": 3, "input_tokens": 230, "output_tokens": 60,
                             "duration_ms": 2800, "known_cost_usd": 0.005,
                             "unpriced_calls": 1, "failed_calls": 1}
    assert {item["category"] for item in data["by_category"]} == {"chat", "review", "draft"}
    assert {item["model"] for item in data["by_model"]} == {"model-a", "model-b"}
