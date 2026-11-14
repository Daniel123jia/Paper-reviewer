"""Local browser-test server. Fake SDK only; never load provider credentials.

Run with a new AUTO_REVIEW_TEST_DATA directory and optional AUTO_REVIEW_TEST_PDF.
"""
import os
from pathlib import Path
from unittest.mock import patch
from pytest import MonkeyPatch
from fastapi.testclient import TestClient


def build_app():
    data = Path(os.environ['AUTO_REVIEW_TEST_DATA']).resolve()
    data.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.environ['PAPER_REVIEW_DATA_DIR'] = str(data)
    os.environ.pop('PAPER_REVIEW_ACCESS_TOKEN',None)
    from backend.tests.model_test_config import configure_test_model
    configure_test_model(MonkeyPatch(),key='fake-browser-test-key',model='actual-model')
    from backend.tests.auto_review_fake_sdk import AutoReviewFakeSDK
    from backend.tests.test_p2a_api import _pdf
    from backend.app import create_app
    AutoReviewFakeSDK.calls = []
    with patch('backend.app.OpenAI',AutoReviewFakeSDK):
        app = create_app(data)
    # Keep the replacement active for every request in this dedicated process.
    import importlib
    importlib.import_module('backend.app').OpenAI = AutoReviewFakeSDK
    pdf_path=os.environ.get('AUTO_REVIEW_TEST_PDF')
    pdf=Path(pdf_path).read_bytes() if pdf_path else _pdf()
    with TestClient(app) as client:
        response=client.post('/api/documents',files={'file':('offline-sdk-test.pdf',pdf,'application/pdf')})
        assert response.status_code==201, response.text
    return app
