"""Single-account session protection for local and predeployment use."""

import hashlib
import hmac
import os
import time


SESSION_SECONDS = 12 * 60 * 60
COOKIE_NAME = "paper_review_session"


class AccessPolicy:
    def __init__(self):
        self.secret = os.environ.get("PAPER_REVIEW_ACCESS_TOKEN", "")
        self.secure_cookie = os.environ.get("PAPER_REVIEW_SECURE_COOKIE") == "1"
        if os.environ.get("PAPER_REVIEW_PREDEPLOY") == "1":
            if len(self.secret) < 32:
                raise ValueError("PAPER_REVIEW_ACCESS_TOKEN must contain at least 32 characters")
            if not self.secure_cookie:
                raise ValueError("PAPER_REVIEW_SECURE_COOKIE must be 1 in predeployment mode")

    @property
    def required(self):
        return bool(self.secret)

    def matches(self, attempt: str):
        return self.required and hmac.compare_digest(attempt.encode(), self.secret.encode())

    def issue(self):
        issued = str(int(time.time()))
        signature = hmac.new(self.secret.encode(), issued.encode(), hashlib.sha256).hexdigest()
        return f"{issued}.{signature}"

    def valid(self, cookie: str | None):
        if not self.required:
            return True
        if not cookie:
            return False
        try:
            issued, signature = cookie.split(".", 1)
            age = time.time() - int(issued)
        except (ValueError, TypeError):
            return False
        if age < 0 or age > SESSION_SECONDS:
            return False
        expected = hmac.new(self.secret.encode(), issued.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
