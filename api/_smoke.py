"""Quick smoke test of the DEDAN Remote API (run manually)."""
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app, raise_server_exceptions=False)

# Job detail (real id from DB)
r = client.get("/api/jobs/b31abea85319618e")
print("detail", r.status_code)
d = None
if r.status_code == 200:
    d = r.json()
    print("  title:", d["title"], "| score:", d["score"], "| slug:", d["slug"])
    print("  freshness:", d["freshness"]["label"],
          "| stale:", d["freshness"]["is_stale"])
    print("  salary_disclosed:", d["salary_disclosed"],
          "| apply_url:", d["apply_url"])
    expl = d.get("score_explanation") or {}
    print("  reasons:", [x["label"] for x in expl.get("reasons", [])])
    print("  intelligence present:", d.get("intelligence") is not None)

if d:
    r2 = client.get(f"/api/jobs/{d['slug']}")
    print("slug lookup", r2.status_code)

# Register + login + save + applications + profile
email = "tester@example.com"
client.post("/api/auth/register",
            json={"email": email, "password": "supersecret1",
                  "display_name": "Tester"})
r = client.post("/api/auth/login",
                json={"email": email, "password": "supersecret1"})
print("login", r.status_code)
tok = r.json()["token"]
h = {"Authorization": f"Bearer {tok}"}

r = client.post("/api/jobs/b31abea85319618e/save",
                json={"note": "interesting"}, headers=h)
print("save", r.status_code, r.json())
r = client.get("/api/saved", headers=h)
print("saved list", r.status_code, len(r.json()))

r = client.post("/api/applications",
                json={"job_id": "b31abea85319618e", "status": "applied"},
                headers=h)
print("create app", r.status_code)
app_id = r.json()["id"] if r.status_code == 201 else None
if app_id:
    r = client.patch(f"/api/applications/{app_id}",
                     json={"status": "interview"}, headers=h)
    print("patch app -> interview", r.status_code, r.json().get("status"))
r = client.get("/api/applications", headers=h)
print("apps list", r.status_code, len(r.json()))

r = client.patch("/api/profile",
                 json={"categories": ["data"], "experience": "beginner",
                       "regions": ["worldwide"]}, headers=h)
print("patch profile", r.status_code, r.json()["preferences"])
r = client.get("/api/recommendations", headers=h)
print("recs", r.status_code, len(r.json()["items"]),
      "| basis:", r.json()["basis"])

r = client.delete("/api/jobs/b31abea85319618e/save", headers=h)
print("unsave", r.status_code)

r = client.post("/api/applications",
                json={"job_id": "b31abea85319618e", "status": "nonsense"},
                headers=h)
print("invalid status", r.status_code)

from api.security import validate_external_url  # noqa: E402

cases = ["javascript:alert(1)", "file:///etc/passwd",
         "http://localhost/admin", "http://169.254.169.254/meta",
         "https://example.com/jobs/1", "ftp://evil.com", None,
         "http://192.168.1.1/x"]
for c in cases:
    print("url", repr(c), "->", validate_external_url(c))
