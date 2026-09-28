import { useEffect, useState } from "react";
import { useAuth } from "../stores/AuthContext";
import { api } from "../services/api";
import type { ProfileOut } from "../types";

export function ProfilePage() {
  const { user, signOut } = useAuth();
  const [profile, setProfile] = useState<ProfileOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.profile()
      .then(setProfile)
      .catch(() => setProfile(null))
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.patchProfile({
        categories: profile?.preferences.categories || [],
        experience: profile?.preferences.experience || undefined,
        regions: profile?.preferences.regions || [],
      });
    } catch (err) {
      console.error("Failed to save preferences", err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="container" style={{ padding: "var(--sp-20) 0" }}>
      <header className="anim-fade-up">
        <h1 style={{ fontSize: "var(--text-2xl)", marginBottom: "var(--sp-4)" }}>
          Profile & Preferences
        </h1>
        <p className="muted">
          Customize your discovery experience.
        </p>
      </header>

      {loading ? (
        <div className="skeleton" style={{ height: 300, borderRadius: "var(--radius-lg)" }} />
      ) : profile ? (
        <div className="glass anim-fade-up" style={{ padding: "var(--sp-8)", marginTop: "var(--sp-8)" }}>
          <section style={{ marginBottom: "var(--sp-8)" }}>
            <h2 style={{ marginBottom: "var(--sp-4)" }}>Account</h2>
            <div style={{ display: "grid", gap: "var(--sp-3)" }}>
              <div>
                <label className="tertiary" style={{ fontSize: "var(--text-xs)" }}>Email</label>
                <div>{user?.email}</div>
              </div>
              <div>
                <label className="tertiary" style={{ fontSize: "var(--text-xs)" }}>Display name</label>
                <div>{user?.display_name || "Not set"}</div>
              </div>
            </div>
          </section>

          <section style={{ marginBottom: "var(--sp-8)" }}>
            <h2 style={{ marginBottom: "var(--sp-4)" }}>Preferences</h2>
            <div style={{ display: "grid", gap: "var(--sp-4)" }}>
              <div>
                <label className="tertiary" style={{ fontSize: "var(--text-xs)" }}>Experience level</label>
                <select
                  className="input"
                  value={profile.preferences.experience || ""}
                  onChange={(e) =>
                    setProfile({
                      ...profile,
                      preferences: {
                        ...profile.preferences,
                        experience: e.target.value as any,
                      },
                    })
                  }
                >
                  <option value="">Not specified</option>
                  <option value="beginner">Beginner</option>
                  <option value="intermediate">Intermediate</option>
                  <option value="advanced">Advanced</option>
                </select>
              </div>
              <div>
                <label className="tertiary" style={{ fontSize: "var(--text-xs)" }}>
                  Preferred categories
                </label>
                <input
                  className="input"
                  placeholder="AI/ML, Software, Data, AI Training"
                  value={profile.preferences.categories.join(", ")}
                  onChange={(e) =>
                    setProfile({
                      ...profile,
                      preferences: {
                        ...profile.preferences,
                        categories: e.target.value.split(",").map(s => s.trim()).filter(Boolean),
                      },
                    })
                  }
                />
              </div>
              <div>
                <label className="tertiary" style={{ fontSize: "var(--text-xs)" }}>
                  Preferred regions
                </label>
                <input
                  className="input"
                  placeholder="Worldwide, Africa, Europe, North America"
                  value={profile.preferences.regions.join(", ")}
                  onChange={(e) =>
                    setProfile({
                      ...profile,
                      preferences: {
                        ...profile.preferences,
                        regions: e.target.value.split(",").map(s => s.trim()).filter(Boolean),
                      },
                    })
                  }
                />
              </div>
            </div>
          </section>

          <div style={{ display: "flex", gap: "var(--sp-4)" }}>
            <button
              className="btn btn-primary"
              onClick={handleSave}
              disabled={saving}
            >
              {saving ? "Saving…" : "Save preferences"}
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => void signOut()}
            >
              Sign out
            </button>
          </div>
        </div>
      ) : (
        <p className="muted">Failed to load profile.</p>
      )}
    </main>
  );
}
