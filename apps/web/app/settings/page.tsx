"use client";

import React, { useState, useEffect } from "react";
import { Loader2 } from "lucide-react";
import { api, type AuthUser } from "../../lib/api";

interface ProviderSetting {
  id: string;
  provider: string;
  label?: string | null;
  config: Record<string, any>;
  secret_masked?: string | null;
  enabled: boolean;
}

const TABS = [
  { key: "github", label: "GitHub" },
  { key: "llm", label: "LLM" },
  { key: "brave", label: "Brave Search" },
  { key: "serpapi", label: "SerpAPI" },
];

export default function SettingsPage() {
  const [providers, setProviders] = useState<ProviderSetting[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<string>("github");
  const [secretInput, setSecretInput] = useState<string>("");
  const [statusMsg, setStatusMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [userToken, setUserToken] = useState("");
  const [me, setMe] = useState<{ username: string; role: string; via: string } | null>(null);
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [newUsername, setNewUsername] = useState("");
  const [newRole, setNewRole] = useState("analyst");
  const [newToken, setNewToken] = useState<string | null>(null);

  const refreshMe = async () => {
    try {
      setMe(await api.getMe());
    } catch {
      setMe(null);
    }
  };

  useEffect(() => {
    void refreshMe();
  }, []);

  useEffect(() => {
    if (me?.role !== "admin") {
      setUsers([]);
      return;
    }
    api.listUsers().then(setUsers).catch(() => setUsers([]));
  }, [me?.role]);

  const saveUserToken = async () => {
    if (!userToken.trim()) return;
    const response = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: userToken.trim() }),
    });
    if (!response.ok) {
      setStatusMsg({ type: "error", text: "Kullanıcı anahtarı geçersiz." });
      return;
    }
    setStatusMsg({ type: "success", text: "Kullanıcı oturumu kuruldu." });
    setUserToken("");
    await refreshMe();
  };

  const createUser = async () => {
    setNewToken(null);
    try {
      const result = await api.createUser(newUsername.trim(), newRole);
      setNewToken(result.token);
      setNewUsername("");
      setUsers(await api.listUsers());
    } catch (e: any) {
      setStatusMsg({ type: "error", text: e.message });
    }
  };

  // Form states
  const [llmBaseUrl, setLlmBaseUrl] = useState("https://opencode.ai/inference/go/openai/v1");
  const [llmModel, setLlmModel] = useState("minimax-m3.1-flash");
  const [llmPreset, setLlmPreset] = useState("opencode-go");

  const fetchProviders = async () => {
    try {
      const res = await fetch("/api/v1/settings/providers");
      if (res.ok) {
        const data = await res.json();
        setProviders(data);
      }
    } catch {
      // API erişilemezse ayar formları boş kalır; sayfa çökmmez.
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProviders();
  }, []);

  const getProvider = (key: string) => providers.find((p) => p.provider === key);

  const handleSave = async (providerKey: string) => {
    setStatusMsg(null);
    const config: Record<string, any> = {};

    if (providerKey === "llm") {
      config.base_url = llmBaseUrl;
      config.model = llmModel;
      config.preset = llmPreset;
    }

    try {
      const res = await fetch("/api/v1/settings/providers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: providerKey,
          label: providerKey.toUpperCase(),
          config,
          secret: secretInput ? secretInput : undefined,
          enabled: true,
        }),
      });

      if (res.ok) {
        setStatusMsg({ type: "success", text: "Ayar kaydedildi." });
        setSecretInput("");
        fetchProviders();
      } else {
        setStatusMsg({ type: "error", text: "Ayarlar kaydedilirken hata oluştu." });
      }
    } catch (e: any) {
      setStatusMsg({ type: "error", text: e.message || "Bağlantı hatası" });
    }
  };

  const handleTest = async (providerKey: string) => {
    const existing = getProvider(providerKey);
    if (!existing) {
      setStatusMsg({ type: "error", text: "Önce anahtarı kaydedin." });
      return;
    }
    setTesting(true);
    setStatusMsg(null);
    try {
      const res = await fetch(`/api/v1/settings/providers/${existing.id}/test`, {
        method: "POST",
      });
      const data = await res.json();
      if (data.ok) {
        setStatusMsg({ type: "success", text: "Bağlantı doğrulandı." });
      } else {
        setStatusMsg({
          type: "error",
          text: `Bağlantı testi başarısız: ${data.error || "bilinmeyen hata"}`,
        });
      }
    } catch (e: any) {
      setStatusMsg({ type: "error", text: e.message || "Bağlantı hatası" });
    } finally {
      setTesting(false);
    }
  };

  const masked = (key: string) => getProvider(key)?.secret_masked;

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="page-title">Sağlayıcı ayarları</h1>
        <p className="page-subtitle">
          Dış servis anahtarlarınızı bu ekrandan girersiniz. Anahtarlar sunucuda Fernet
          (AES-128-CBC) ile şifrelenir ve yalnızca maskeli biçimde gösterilir.
        </p>
      </div>

      <section className="panel p-5">
        <h2 className="section-heading">Kullanıcı oturumu</h2>
        <p className="mt-1 text-[12px] text-ink-3">
          Kişisel API anahtarınız rolünüzü belirler: admin, analyst veya viewer.
          Anahtar yoksa paylaşılan sunucu token’ı (admin) kullanılır.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="min-w-[260px] flex-1">
            <span className="label">Kullanıcı anahtarı</span>
            <input
              type="password"
              className="input font-mono"
              value={userToken}
              onChange={(e) => setUserToken(e.target.value)}
              placeholder="showis_…"
            />
          </label>
          <button type="button" onClick={saveUserToken} className="btn-secondary">
            Anahtarı uygula
          </button>
          <button
            type="button"
            onClick={async () => {
              await fetch("/api/session", { method: "DELETE" });
              setUserToken("");
              setStatusMsg({ type: "success", text: "Kullanıcı oturumu kapatıldı." });
              await refreshMe();
            }}
            className="btn-quiet"
          >
            Temizle
          </button>
        </div>
        <p className="mt-2 text-[12px] text-ink-3">
          {me
            ? `Aktif kimlik: ${me.username} (${me.role})`
            : "Kimlik doğrulanamadı; paylaşılan admin token’ı geçerli olabilir."}
        </p>

        {me?.role === "admin" && (
          <div className="mt-5 border-t border-line pt-4">
            <h3 className="text-[12.5px] font-semibold text-ink">Kullanıcı oluştur</h3>
            <div className="mt-2 flex flex-wrap items-end gap-2">
              <label className="min-w-[180px] flex-1">
                <span className="label">Kullanıcı adı</span>
                <input
                  className="input font-mono"
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  placeholder="analist-1"
                />
              </label>
              <label>
                <span className="label">Rol</span>
                <select
                  className="select w-auto"
                  value={newRole}
                  onChange={(e) => setNewRole(e.target.value)}
                >
                  <option value="analyst">analyst</option>
                  <option value="viewer">viewer</option>
                  <option value="admin">admin</option>
                </select>
              </label>
              <button
                type="button"
                onClick={createUser}
                disabled={!newUsername.trim()}
                className="btn-primary btn-sm"
              >
                Oluştur
              </button>
            </div>
            {newToken && (
              <p className="numeric mt-2 break-all rounded-[6px] border border-warn-line bg-warn-soft px-3 py-2 text-[12px] text-warn">
                Yeni anahtar (yalnız bir kez gösterilir): {newToken}
              </p>
            )}
            {users.length > 0 && (
              <ul className="mt-3 divide-y divide-line text-[12px]">
                {users.map((user) => (
                  <li key={user.username} className="flex items-center gap-3 py-1.5">
                    <span className="font-mono text-ink-2">{user.username}</span>
                    <span className="text-ink-3">{user.role}</span>
                    <span className={user.active ? "text-ok" : "text-danger"}>
                      {user.active ? "aktif" : "pasif"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      {statusMsg && (
        <div
          role="status"
          className={`rounded-[6px] border px-4 py-2.5 text-[13px] ${
            statusMsg.type === "success"
              ? "border-ok-line bg-ok-soft text-ok"
              : "border-danger-line bg-danger-soft text-danger"
          }`}
        >
          {statusMsg.text}
        </div>
      )}

      <div>
        <div className="flex gap-6 border-b border-line">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => {
                setActiveTab(tab.key);
                setStatusMsg(null);
                setSecretInput("");
              }}
              aria-current={activeTab === tab.key ? "page" : undefined}
              className={`relative pb-2.5 pt-1 text-[13px] transition-colors ${
                activeTab === tab.key
                  ? "font-medium text-ink after:absolute after:inset-x-0 after:bottom-[-1px] after:h-[2px] after:bg-accent"
                  : "text-ink-3 hover:text-ink"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="panel mt-5">
          {loading ? (
            <p className="flex items-center gap-2 px-5 py-8 text-[13px] text-ink-3">
              <Loader2 size={14} className="animate-spin" aria-hidden />
              Ayarlar yükleniyor
            </p>
          ) : (
            <>
              {activeTab === "github" && (
                <div className="p-5">
                  <h2 className="section-heading">GitHub Personal Access Token</h2>
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-3">
                    API limitini saatte 60 istek seviyesinden 5.000 istek seviyesine çıkarır.
                    Yalnızca herkese açık veri okuma izni yeterlidir.
                  </p>

                  {masked("github") && (
                    <p className="numeric mt-4 rounded-[6px] border border-line bg-sunken px-3 py-2 text-ink-2">
                      Kayıtlı token: {masked("github")}
                    </p>
                  )}

                  <label className="mt-4 block">
                    <span className="label">
                      {masked("github")
                        ? "Yeni token — değiştirmek istemiyorsanız boş bırakın"
                        : "GitHub token"}
                    </span>
                    <input
                      type="password"
                      placeholder="ghp_…"
                      value={secretInput}
                      onChange={(e) => setSecretInput(e.target.value)}
                      className="input font-mono"
                    />
                  </label>

                  <div className="mt-5 flex flex-wrap gap-2">
                    <button onClick={() => handleSave("github")} className="btn-primary btn-sm">
                      Kaydet
                    </button>
                    <button
                      onClick={() => handleTest("github")}
                      disabled={testing}
                      className="btn-secondary btn-sm"
                    >
                      {testing ? "Test ediliyor" : "Bağlantıyı test et"}
                    </button>
                  </div>
                </div>
              )}

              {activeTab === "llm" && (
                <div className="p-5">
                  <h2 className="section-heading">OpenAI uyumlu LLM sağlayıcısı</h2>
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-3">
                    OpenCode Go veya standart bir OpenAI uç noktası yapılandırılabilir. Anahtar
                    sunucuda tutulur; tarama verisi LLM'e gönderilmeden önce redaksiyondan
                    geçirilir.
                  </p>

                  <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    <label className="block">
                      <span className="label">Hazır ayar</span>
                      <select
                        value={llmPreset}
                        onChange={(e) => {
                          setLlmPreset(e.target.value);
                          if (e.target.value === "opencode-go") {
                            setLlmBaseUrl("https://opencode.ai/inference/go/openai/v1");
                            setLlmModel("minimax-m3.1-flash");
                          } else if (e.target.value === "opencode-zen") {
                            setLlmBaseUrl("https://opencode.ai/inference/openai/v1");
                            setLlmModel("gpt-4o-mini");
                          }
                        }}
                        className="select"
                      >
                        <option value="opencode-go">OpenCode Go (10 USD / ay)</option>
                        <option value="opencode-zen">OpenCode Zen</option>
                        <option value="custom">Özel OpenAI uyumlu</option>
                      </select>
                    </label>
                    <label className="block">
                      <span className="label">Model adı</span>
                      <input
                        type="text"
                        value={llmModel}
                        onChange={(e) => setLlmModel(e.target.value)}
                        className="input font-mono"
                      />
                    </label>
                  </div>

                  <label className="mt-4 block">
                    <span className="label">Base URL</span>
                    <input
                      type="text"
                      value={llmBaseUrl}
                      onChange={(e) => setLlmBaseUrl(e.target.value)}
                      className="input font-mono"
                    />
                  </label>

                  {masked("llm") && (
                    <p className="numeric mt-4 rounded-[6px] border border-line bg-sunken px-3 py-2 text-ink-2">
                      Kayıtlı anahtar: {masked("llm")}
                    </p>
                  )}

                  <label className="mt-4 block">
                    <span className="label">
                      {masked("llm")
                        ? "Yeni API anahtarı — boş bırakılırsa mevcut korunur"
                        : "API anahtarı"}
                    </span>
                    <input
                      type="password"
                      placeholder="sk-…"
                      value={secretInput}
                      onChange={(e) => setSecretInput(e.target.value)}
                      className="input font-mono"
                    />
                  </label>

                  <div className="mt-5 flex flex-wrap gap-2">
                    <button onClick={() => handleSave("llm")} className="btn-primary btn-sm">
                      Kaydet
                    </button>
                    <button onClick={() => handleTest("llm")} disabled={testing} className="btn-secondary btn-sm">
                      {testing ? "Test ediliyor" : "Bağlantıyı test et"}
                    </button>
                  </div>
                </div>
              )}

              {activeTab === "brave" && (
                <div className="p-5">
                  <h2 className="section-heading">Brave Search API</h2>
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-3">
                    Web ve platform arama motoru desteği için Brave Search API anahtarı.
                  </p>

                  {masked("brave") && (
                    <p className="numeric mt-4 rounded-[6px] border border-line bg-sunken px-3 py-2 text-ink-2">
                      Kayıtlı anahtar: {masked("brave")}
                    </p>
                  )}

                  <label className="mt-4 block">
                    <span className="label">
                      {masked("brave")
                        ? "Yeni anahtar — boş bırakılırsa korunur"
                        : "Brave API anahtarı"}
                    </span>
                    <input
                      type="password"
                      placeholder="BSA…"
                      value={secretInput}
                      onChange={(e) => setSecretInput(e.target.value)}
                      className="input font-mono"
                    />
                  </label>

                  <div className="mt-5 flex flex-wrap gap-2">
                    <button onClick={() => handleSave("brave")} className="btn-primary btn-sm">
                      Kaydet
                    </button>
                    <button onClick={() => handleTest("brave")} disabled={testing} className="btn-secondary btn-sm">
                      {testing ? "Test ediliyor" : "Bağlantıyı test et"}
                    </button>
                  </div>
                </div>
              )}

              {activeTab === "serpapi" && (
                <div className="p-5">
                  <h2 className="section-heading">SerpAPI (Google araması)</h2>
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-3">
                    Google arama sonuçları için alternatif sağlayıcı. Brave kullanılamadığında
                    devreye girer.
                  </p>

                  {masked("serpapi") && (
                    <p className="numeric mt-4 rounded-[6px] border border-line bg-sunken px-3 py-2 text-ink-2">
                      Kayıtlı anahtar: {masked("serpapi")}
                    </p>
                  )}

                  <label className="mt-4 block">
                    <span className="label">
                      {masked("serpapi")
                        ? "Yeni anahtar — boş bırakılırsa korunur"
                        : "SerpAPI anahtarı"}
                    </span>
                    <input
                      type="password"
                      placeholder="serpapi key…"
                      value={secretInput}
                      onChange={(e) => setSecretInput(e.target.value)}
                      className="input font-mono"
                    />
                  </label>

                  <div className="mt-5 flex flex-wrap gap-2">
                    <button onClick={() => handleSave("serpapi")} className="btn-primary btn-sm">
                      Kaydet
                    </button>
                    <button
                      onClick={() => handleTest("serpapi")}
                      disabled={testing}
                      className="btn-secondary btn-sm"
                    >
                      {testing ? "Test ediliyor" : "Bağlantıyı test et"}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
