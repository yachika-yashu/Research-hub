import random
import string
from locust import HttpUser, task, between

class ResearcherUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        """Register and login a new user for each Locust instance to simulate multi-tenant traffic."""
        # Generate random user info
        self.username = "user_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        self.password = "password123"
        self.team_code = "team_" + random.choice(["alpha", "beta", "gamma", "delta"])
        
        # Register
        self.client.post("/api/v1/auth/register", json={
            "username": self.username,
            "password": self.password,
            "team_code": self.team_code
        })
        
        # Login
        response = self.client.post("/api/v1/auth/token", data={
            "username": self.username,
            "password": self.password
        })
        
        if response.status_code == 200:
            token = response.json().get("access_token")
            self.headers = {"Authorization": f"Bearer {token}"}
        else:
            self.headers = {}

    @task(5)
    def check_health(self):
        """Simulate frequent background health checks/pings"""
        self.client.get("/api/v1/health")

    @task(3)
    def check_stats(self):
        """Simulate users checking their dashboard stats"""
        if self.headers:
            self.client.get("/api/v1/stats/usage", headers=self.headers)

    @task(2)
    def check_threads(self):
        """Simulate users looking at their chat history"""
        if self.headers:
            self.client.get("/api/v1/chat/threads", headers=self.headers)
            
    @task(1)
    def check_vault(self):
        """Simulate users checking the vault diagnostic endpoint"""
        if self.headers:
            self.client.get("/api/v1/debug/stats", headers=self.headers)


# =============================================================================
# Ramp profile for progressive load testing:
#   Stage 1 — Warm-up   :  10 users,  spawn rate  2/s,  duration  2 min
#   Stage 2 — Baseline  :  50 users,  spawn rate  5/s,  duration  5 min
#   Stage 3 — Stress    : 200 users,  spawn rate 20/s,  duration  5 min
#   Stage 4 — Peak      : 500 users,  spawn rate 50/s,  duration  3 min
# Run with:
#   locust -f perf/locustfile.py --headless -u 500 -r 50 --run-time 15m \
#          --host http://localhost:8000
# =============================================================================


class LiteratureReviewUser(HttpUser):
    """Simulate researchers triggering literature review synthesis."""

    wait_time = between(5, 15)

    def on_start(self):
        """Register and authenticate before sending literature review requests."""
        self.username  = "litrev_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        self.password  = "password123"
        self.team_code = "team_" + random.choice(["alpha", "beta", "gamma", "delta"])

        self.client.post("/api/v1/auth/register", json={
            "username":  self.username,
            "password":  self.password,
            "team_code": self.team_code,
        })

        resp = self.client.post("/api/v1/auth/token", data={
            "username": self.username,
            "password": self.password,
        })
        self.headers = {"Authorization": f"Bearer {resp.json().get('access_token', '')}"} \
            if resp.status_code == 200 else {}

    @task(3)
    def request_literature_review(self):
        """Hit the literature review endpoint with a sample research question."""
        if not self.headers:
            return
        questions = [
            "What are the main approaches to attention mechanisms in NLP?",
            "Summarize the state of few-shot learning in computer vision.",
            "What research gaps exist in transformer efficiency?",
        ]
        self.client.post(
            "/api/v1/literature-review",
            json={"question": random.choice(questions), "max_papers": 5},
            headers=self.headers,
        )

    @task(1)
    def check_health(self):
        """Keep-alive health check."""
        self.client.get("/api/v1/health")


class PaperComparisonUser(HttpUser):
    """Simulate researchers comparing multiple papers side-by-side."""

    wait_time = between(8, 20)

    def on_start(self):
        """Register and authenticate before sending comparison requests."""
        self.username  = "compare_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        self.password  = "password123"
        self.team_code = "team_" + random.choice(["alpha", "beta", "gamma", "delta"])

        self.client.post("/api/v1/auth/register", json={
            "username":  self.username,
            "password":  self.password,
            "team_code": self.team_code,
        })

        resp = self.client.post("/api/v1/auth/token", data={
            "username": self.username,
            "password": self.password,
        })
        self.headers = {"Authorization": f"Bearer {resp.json().get('access_token', '')}"} \
            if resp.status_code == 200 else {}

    @task(2)
    def compare_papers(self):
        """Hit the compare endpoint with a cross-paper comparison query."""
        if not self.headers:
            return
        queries = [
            "Compare the model architectures and datasets used across uploaded papers.",
            "How do the reported F1 scores differ between papers?",
            "Which papers use transformer-based models and how do they differ?",
        ]
        self.client.post(
            "/api/v1/compare",
            json={"query": random.choice(queries)},
            headers=self.headers,
        )

    @task(1)
    def check_health(self):
        """Keep-alive health check."""
        self.client.get("/api/v1/health")


class IngestionStressUser(HttpUser):
    """Simulate concurrent PDF uploads to stress-test the ingestion pipeline."""

    wait_time = between(30, 60)

    # Minimal single-page PDF bytes (valid PDF 1.4, one blank page)
    _MINIMAL_PDF: bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
    )

    def on_start(self):
        """Register and authenticate before uploading test PDFs."""
        self.username  = "ingest_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        self.password  = "password123"
        self.team_code = "team_" + random.choice(["alpha", "beta", "gamma", "delta"])

        self.client.post("/api/v1/auth/register", json={
            "username":  self.username,
            "password":  self.password,
            "team_code": self.team_code,
        })

        resp = self.client.post("/api/v1/auth/token", data={
            "username": self.username,
            "password": self.password,
        })
        self.headers = {"Authorization": f"Bearer {resp.json().get('access_token', '')}"} \
            if resp.status_code == 200 else {}

    @task(1)
    def upload_test_pdf(self):
        """Upload a minimal valid PDF to stress the ingest queue."""
        if not self.headers:
            return
        filename = f"stress_test_{random.randint(1000, 9999)}.pdf"
        self.client.post(
            "/api/v1/ingest",
            files={"file": (filename, self._MINIMAL_PDF, "application/pdf")},
            headers=self.headers,
        )
