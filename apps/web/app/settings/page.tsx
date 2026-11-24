"use client";

import React, { useState, useEffect } from "react";
import { Check, Loader2, Sparkles, Trash2, Zap } from "lucide-react";
import { api, type AIProviderProfile, type AuthUser } from "../../lib/api";

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
  { key: "hibp", label: "HIBP" },
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

  // Çoklu AI sağlayıcı profilleri
  const [aiProviders, setAiProviders] = useState<AIProviderProfile[]>([]);
  const [aiPresets, setAiPresets] = useState<
    Record<string, { base_url: string; default_model: string; description: string }>
  >({});
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null);
  const [aiName, setAiName] = useState("OpenCode Go");
  const [aiPreset, setAiPreset] = useState("opencode-go");
  const [aiBaseUrl, setAiBaseUrl] = useState("https://opencode.ai/inference/go/openai/v1");
  const [aiModel, setAiModel] = useState("minimax-m3.1-flash");
  const [aiSecret, setAiSecret] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiPresetsLoading, setAiPresetsLoading] = useState(true);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [modelsBusy, setModelsBusy] = useState(false);

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

  const fetchAIProviders = async () => {
    try {
      const [profiles, presets] = await Promise.all([
        api.listAIProviders(),
        api.listAIProviderPresets(),
      ]);
      setAiProviders(profiles);
      setAiPresets(presets);
    } catch {
      // Sağlayıcı uçları erişilemezse form boş kalır; sayfa çökmez.
    } finally {
      setAiPresetsLoading(false);
    }
  };

  useEffect(() => {
    void fetchAIProviders();
  }, []);

  const getProvider = (key: string) => providers.find((p) => p.provider === key);

  const applyAIPreset = (preset: string) => {
    setAiPreset(preset);
    const meta = aiPresets[preset];
    if (meta) {
      setAiBaseUrl(meta.base_url);
      setAiModel(meta.default_model);
    }
  };

  const resetAIForm = () => {
    setEditingProviderId(null);
    setAiName("OpenCode Go");
    applyAIPreset("opencode-go");
    setAiSecret("");
    setModelOptions([]);
    setSelectedModels([]);
  };

  const editAIProvider = (provider: AIProviderProfile) => {
    setEditingProviderId(provider.id);
    setAiName(provider.name);
    setAiPreset(provider.preset);
    setAiBaseUrl(String(provider.config.base_url || ""));
    setAiModel(String(provider.config.model || ""));
    const models = Array.isArray(provider.config.models)
      ? provider.config.models.map((model: unknown) => String(model))
      : provider.config.model
        ? [String(provider.config.model)]
        : [];
    setModelOptions(models);
    setSelectedModels(models);
    setAiSecret("");
    setStatusMsg(null);
  };

  const saveAIProvider = async () => {
    setAiBusy(true);
    setStatusMsg(null);
    const current = editingProviderId
      ? aiProviders.find((provider) => provider.id === editingProviderId)?.config || {}
      : {};
    const model = aiModel.trim();
    const chosen = selectedModels.length ? selectedModels : model ? [model] : [];
    const config = {
      ...current,
      base_url: aiBaseUrl.trim(),
      model,
      models: chosen,
    };
    try {
      if (editingProviderId) {
        const updated = await api.updateAIProvider(editingProviderId, {
          name: aiName.trim(),
          preset: aiPreset,
          config,
          secret: aiSecret ? aiSecret : undefined,
        });
        setStatusMsg({
          type: updated.secret_required ? "error" : "success",
          text: updated.secret_required
            ? "Profil kaydedildi; base_url değiştiği için API anahtarı geçersiz kılındı. Yeni anahtarı girin."
            : "Profil güncellendi.",
        });
      } else {
        await api.createAIProvider({
          name: aiName.trim(),
          preset: aiPreset,
          config,
          secret: aiSecret || undefined,
          activate: aiProviders.length === 0 && Boolean(aiSecret),
        });
        setStatusMsg({ type: "success", text: "Profil eklendi." });
      }
      resetAIForm();
      await fetchAIProviders();
    } catch (e: any) {
      setStatusMsg({ type: "error", text: e.message || "Profil kaydedilemedi" });
    } finally {
      setAiBusy(false);
    }
  };

  const discoverModels = async () => {
    setModelsBusy(true);
    setStatusMsg(null);
    try {
      const result = await api.discoverAIProviderModels({
        provider_id: editingProviderId || undefined,
        base_url: aiBaseUrl.trim(),
        secret: aiSecret || undefined,
      });
      setModelOptions(result.models);
      setSelectedModels((current) => {
        const kept = current.filter((model) => result.models.includes(model));
        if (kept.length) return kept;
        return aiModel.trim() && result.models.includes(aiModel.trim())
          ? [aiModel.trim()]
          : result.models.slice(0, 1);
      });
      if (!aiModel.trim() && result.models[0]) setAiModel(result.models[0]);
      setStatusMsg({
        type: "success",
        text: `${result.models.length} model bulundu. Kullanılacakları seçin.`,
      });
    } catch (e: any) {
      setStatusMsg({ type: "error", text: e.message || "Modeller alınamadı" });
    } finally {
      setModelsBusy(false);
    }
  };

  const activateAIProvider = async (provider: AIProviderProfile) => {
    setAiBusy(true);
    try {
      await api.activateAIProvider(provider.id);
      setStatusMsg({ type: "success", text: `${provider.name} aktif sağlayıcı oldu.` });
      await fetchAIProviders();
    } catch (e: any) {
      setStatusMsg({ type: "error", text: e.message || "Profil aktifleştirilemedi" });
    } finally {
      setAiBusy(false);
    }
  };

  const testAIProvider = async (provider: AIProviderProfile) => {
    setAiBusy(true);
    setStatusMsg(null);
    try {
      const result = await api.testAIProvider(provider.id);
      setStatusMsg(
        result.ok
          ? { type: "success", text: `${provider.name}: bağlantı doğrulandı.` }
          : {
              type: "error",
              text: `${provider.name}: ${result.error || "bağlantı doğrulanamadı"}`,
            }
      );
    } catch (e: any) {
      setStatusMsg({ type: "error", text: e.message || "Bağlantı testi başarısız" });
    } finally {
      setAiBusy(false);
    }
  };

  const deleteAIProvider = async (provider: AIProviderProfile) => {
    if (!confirm(`"${provider.name}" profili silinsin mi?`)) return;
    setAiBusy(true);
    try {
      await api.deleteAIProvider(provider.id);
      if (editingProviderId === provider.id) resetAIForm();
      setStatusMsg({ type: "success", text: "Profil silindi." });
      await fetchAIProviders();
    } catch (e: any) {
      setStatusMsg({ type: "error", text: e.message || "Profil silinemedi" });
    } finally {
      setAiBusy(false);
    }
  };

  const handleSave = async (providerKey: string) => {
    setStatusMsg(null);
    const config: Record<string, any> = {};

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
                <div className="space-y-5 p-5">
                  <div>
                    <h2 className="section-heading">AI sağlayıcı profilleri</h2>
                    <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-3">
                      Birden fazla OpenAI uyumlu sağlayıcı ekleyebilirsiniz: OpenCode Go,
                      OpenCode Zen, OpenAI veya özel bir uç nokta. Anahtarlar sunucuda Fernet
                      ile şifrelenir, istemciye yalnız maskeli biçimde döner. Aynı anda tek
                      profil aktiftir.
                    </p>
                  </div>

                  <div className="border-y border-line">
                    {aiPresetsLoading ? (
                      <p className="flex items-center gap-2 py-4 text-[12.5px] text-ink-3">
                        <Loader2 size={13} className="animate-spin" aria-hidden />
                        Profiller yükleniyor
                      </p>
                    ) : aiProviders.length === 0 ? (
                      <p className="py-4 text-[12.5px] text-ink-3">
                        Henüz profil yok. Aşağıdan ilk sağlayıcıyı ekleyin.
                      </p>
                    ) : (
                      <ul className="divide-y divide-line">
                        {aiProviders.map((profile) => (
                          <li
                            key={profile.id}
                            className="flex flex-wrap items-start justify-between gap-3 py-3"
                          >
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="text-[13px] font-medium text-ink">
                                  {profile.name}
                                </span>
                                {profile.is_active && (
                                  <span className="status text-ok">
                                    <span
                                      className="h-1.5 w-1.5 rounded-full bg-ok"
                                      aria-hidden
                                    />
                                    aktif
                                  </span>
                                )}
                                {!profile.enabled && (
                                  <span className="status text-ink-3">pasif</span>
                                )}
                              </div>
                              <p className="mt-0.5 text-[12px] text-ink-3">
                                {profile.preset} · {profile.config.model || "model yok"} ·{" "}
                                {profile.secret_masked || "anahtar yok"}
                              </p>
                              <p className="numeric mt-0.5 break-all text-[11.5px] text-ink-3">
                                {profile.config.base_url}
                              </p>
                            </div>
                            <div className="flex flex-wrap items-center gap-1">
                              {!profile.is_active && profile.enabled && (
                                <button
                                  type="button"
                                  className="btn-quiet btn-sm"
                                  disabled={aiBusy}
                                  onClick={() => activateAIProvider(profile)}
                                >
                                  <Zap size={12} aria-hidden />
                                  Aktif yap
                                </button>
                              )}
                              <button
                                type="button"
                                className="btn-secondary btn-sm"
                                disabled={aiBusy}
                                onClick={() => testAIProvider(profile)}
                              >
                                Test
                              </button>
                              <button
                                type="button"
                                className="btn-quiet btn-sm"
                                onClick={() => editAIProvider(profile)}
                              >
                                Düzenle
                              </button>
                              <button
                                type="button"
                                className="btn-danger-quiet btn-sm"
                                disabled={aiBusy}
                                onClick={() => deleteAIProvider(profile)}
                              >
                                <Trash2 size={12} aria-hidden />
                              </button>
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div>
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="text-[12.5px] font-semibold text-ink">
                        {editingProviderId ? "Profili düzenle" : "Yeni profil"}
                      </h3>
                      {editingProviderId && (
                        <button type="button" onClick={resetAIForm} className="btn-quiet btn-sm">
                          Vazgeç
                        </button>
                      )}
                    </div>

                    <div className="mt-3 grid gap-4 sm:grid-cols-2">
                      <label className="block">
                        <span className="label">Profil adı</span>
                        <input
                          className="input"
                          placeholder="OpenCode Go"
                          value={aiName}
                          onChange={(e) => setAiName(e.target.value)}
                        />
                      </label>
                      <label className="block">
                        <span className="label">Hazır ayar</span>
                        <select
                          className="select"
                          value={aiPreset}
                          onChange={(e) => applyAIPreset(e.target.value)}
                        >
                          {Object.entries(aiPresets).map(([key, preset]) => (
                            <option key={key} value={key}>
                              {key === "opencode-go"
                                ? "OpenCode Go"
                                : key === "opencode-zen"
                                  ? "OpenCode Zen"
                                  : key === "openai"
                                    ? "OpenAI"
                                    : key === "openrouter"
                                      ? "OpenRouter"
                                      : key === "groq"
                                        ? "Groq"
                                        : key}
                            </option>
                          ))}
                          <option value="custom">Özel OpenAI uyumlu</option>
                        </select>
                      </label>
                      <label className="block">
                        <span className="label">Model</span>
                        <input
                          className="input font-mono"
                          placeholder="minimax-m3.1-flash"
                          value={aiModel}
                          onChange={(e) => setAiModel(e.target.value)}
                        />
                      </label>
                      <label className="block">
                        <span className="label">Base URL</span>
                        <input
                          className="input font-mono"
                          placeholder="https://opencode.ai/inference/go/openai/v1"
                          value={aiBaseUrl}
                          onChange={(e) => setAiBaseUrl(e.target.value)}
                        />
                      </label>
                    </div>

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={discoverModels}
                        disabled={modelsBusy || !aiBaseUrl.trim()}
                        className="btn-secondary btn-sm"
                      >
                        {modelsBusy ? (
                          <Loader2 size={13} className="animate-spin" aria-hidden />
                        ) : (
                          <Sparkles size={13} aria-hidden />
                        )}
                        Modelleri getir
                      </button>
                      <span className="text-[11.5px] text-ink-3">
                        API anahtarıyla erişilebilen modeller sağlayıcıdan okunur.
                      </span>
                    </div>

                    {modelOptions.length > 0 && (
                      <fieldset className="mt-4 border-t border-line pt-3">
                        <legend className="sr-only">
                          Tarama ve AI için izinli modeller
                        </legend>
                        <div className="flex items-center justify-between gap-2">
                          <span className="label">Tarama ve AI için izinli modeller</span>
                          <div className="flex gap-1">
                            <button
                              type="button"
                              onClick={() => setSelectedModels(modelOptions)}
                              className="btn-quiet btn-sm"
                            >
                              Tümünü seç
                            </button>
                            <button
                              type="button"
                              onClick={() => setSelectedModels([])}
                              className="btn-quiet btn-sm"
                            >
                              Temizle
                            </button>
                          </div>
                        </div>
                        <div className="mt-2 max-h-52 space-y-1.5 overflow-auto pr-1">
                          {modelOptions.map((model) => (
                            <label
                              key={model}
                              className="flex items-center gap-2 text-[12.5px] text-ink-2"
                            >
                              <input
                                type="checkbox"
                                checked={selectedModels.includes(model)}
                                onChange={(e) =>
                                  setSelectedModels((current) =>
                                    e.target.checked
                                      ? [...new Set([...current, model])]
                                      : current.filter((item) => item !== model)
                                  )
                                }
                                className="h-3.5 w-3.5 accent-[rgb(var(--accent))]"
                              />
                              <span className="numeric break-all">{model}</span>
                            </label>
                          ))}
                        </div>
                        <p className="mt-1.5 text-[11.5px] text-ink-3">
                          Seçilen modeller tarama ve araştırma ekranlarında model
                          seçicisinde görünür. Varsayılan model yukarıdaki “Model” alanıdır.
                        </p>
                      </fieldset>
                    )}

                    <label className="mt-4 block">
                      <span className="label">
                        {editingProviderId
                          ? "Yeni API anahtarı — boş bırakılırsa mevcut korunur"
                          : "API anahtarı"}
                      </span>
                      <input
                        type="password"
                        className="input font-mono"
                        placeholder="sk-…"
                        value={aiSecret}
                        onChange={(e) => setAiSecret(e.target.value)}
                      />
                      <span className="hint">
                        Base URL değişirse eski anahtar güvenlik gereği geçersiz kılınır.
                        Özel bir host, sunucudaki{" "}
                        <span className="numeric">SHOWIS_LLM_BASE_URL_ALLOWLIST</span>{" "}
                        listesine eklenmelidir.
                      </span>
                    </label>

                    <div className="mt-4 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={saveAIProvider}
                        disabled={
                          aiBusy || !aiName.trim() || !aiBaseUrl.trim() || !aiModel.trim()
                        }
                        className="btn-primary btn-sm"
                      >
                        {aiBusy ? (
                          <Loader2 size={13} className="animate-spin" aria-hidden />
                        ) : (
                          <Check size={13} aria-hidden />
                        )}
                        {editingProviderId ? "Güncelle" : "Profili ekle"}
                      </button>
                      {!editingProviderId && aiProviders.length === 0 && (
                        <span className="self-center text-[11.5px] text-ink-3">
                          İlk profil anahtarla birlikte otomatik aktifleşir.
                        </span>
                      )}
                    </div>
                  </div>

                  <p className="border-t border-line pt-3 text-[11.5px] leading-relaxed text-ink-3">
                    Eski tek <span className="numeric">llm</span> ayarı geriye dönük uyumluluk
                    için korunur; aktif profil yoksa yedek olarak kullanılır.
                  </p>
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

              {activeTab === "hibp" && (
                <div className="p-5">
                  <h2 className="section-heading">Have I Been Pwned</h2>
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-3">
                    E-posta sızıntı kontrolü anahtarsız çalışır (XposedOrNot ve
                    Hudson Rock). HIBP v3 anahtarı isteğe bağlıdır; girilirse
                    ek kaynak olarak sorgulanır ve sonuçlar birleştirilir.
                  </p>

                  {masked("hibp") && (
                    <p className="numeric mt-4 rounded-[6px] border border-line bg-sunken px-3 py-2 text-ink-2">
                      Kayıtlı anahtar: {masked("hibp")}
                    </p>
                  )}

                  <label className="mt-4 block">
                    <span className="label">
                      {masked("hibp")
                        ? "Yeni HIBP anahtarı — boş bırakılırsa korunur"
                        : "HIBP API anahtarı"}
                    </span>
                    <input
                      type="password"
                      placeholder="hibp-api-key"
                      value={secretInput}
                      onChange={(e) => setSecretInput(e.target.value)}
                      className="input font-mono"
                    />
                  </label>

                  <div className="mt-5 flex flex-wrap gap-2">
                    <button onClick={() => handleSave("hibp")} className="btn-primary btn-sm">
                      Kaydet
                    </button>
                    <button
                      onClick={() => handleTest("hibp")}
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
