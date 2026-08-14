r"""Load test profile for the inference API (Phase 14).

Run against a real environment, e.g.:

    locust -f deploy/loadtest/locustfile.py --host http://localhost:8000 \
        --users 20 --spawn-rate 2 --run-time 2m --headless \
        --csv deploy/loadtest/results

Requires ``LOAD_TEST_EMAIL``/``LOAD_TEST_PASSWORD`` for an already-registered operator
account, and ``LOAD_TEST_CATEGORY`` (default ``bottle``) to already have a promoted
production model — this profile drives real traffic through the real API, the same
"no backdoor queries" principle Phase 13 held the dashboard to; it does not fabricate a
model or a token, it logs in and predicts exactly the way a real client would.
"""

from __future__ import annotations

import io
import os

from locust import HttpUser, between, task
from PIL import Image

_CATEGORY = os.environ.get("LOAD_TEST_CATEGORY", "bottle")
_EMAIL = os.environ.get("LOAD_TEST_EMAIL", "loadtest@factoryai.local")
_PASSWORD = os.environ.get("LOAD_TEST_PASSWORD", "change-me-locally")


def _synthetic_image() -> bytes:
    """Build a small, valid PNG in memory — no fixture file to ship or go stale."""
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), color=(120, 120, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


class InspectionUser(HttpUser):
    """Mirrors an operator's real usage mix: mostly reads, occasional predictions."""

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        """Log in once per simulated user, exactly like a real client would."""
        self._image_bytes = _synthetic_image()
        response = self.client.post(
            "/auth/login", json={"email": _EMAIL, "password": _PASSWORD}, name="/auth/login"
        )
        self.access_token = response.json().get("access_token") if response.ok else None

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}

    @task(5)
    def health(self) -> None:
        """The cheapest, most frequent call — matches a load balancer's own health checks."""
        self.client.get("/health/live", name="/health/live")

    @task(3)
    def list_models(self) -> None:
        """A dashboard-style read, viewer-level."""
        self.client.get("/models", headers=self._auth_headers(), name="/models")

    @task(2)
    def predictions_history(self) -> None:
        """A paginated dashboard read (Phase 13)."""
        self.client.get(
            "/predictions", params={"limit": 25}, headers=self._auth_headers(), name="/predictions"
        )

    @task(1)
    def predict(self) -> None:
        """The expensive path: real inference against the category's production model."""
        if not self.access_token:
            return
        self.client.post(
            "/predict",
            data={"category": _CATEGORY},
            files={"image": ("sample.png", self._image_bytes, "image/png")},
            headers=self._auth_headers(),
            name="/predict",
        )
