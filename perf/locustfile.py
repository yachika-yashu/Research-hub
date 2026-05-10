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
