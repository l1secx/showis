"use client";

import React, { useState, useEffect } from "react";

interface ProviderSetting {
  id: string;
  provider: string;
  label?: string | null;
  config: Record<string, any>;
  secret_masked?: string | null;
  enabled: boolean;
}

export default function SettingsPage() {
  const [providers, setProviders] = useState<ProviderSetting[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<string>("github");
  const [secretInput, setSecretInput] = useState<string>("");
  const [statusMsg, setStatusMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [testing, setTesting] = useState(false);

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
      // fallback for dev
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
    const existing = getProvider(providerKey);
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
        setStatusMsg({ type: "success", text: `${providerKey.toUpperCase()} ayarları başarıyla kaydedildi.` });
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
      setStatusMsg({ type: "error", text: "Önce anahtarı kaydediniz." });
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
        setStatusMsg({ type: "success", text: `Bağlantı testi başarılı! ${data.message || ""}` });
      } else {
        setStatusMsg({ type: "error", text: `Bağlantı testi başarısız: ${data.error || "Bilinmeyen hata"}` });
      }
    } catch (e: any) {
      setStatusMsg({ type: "error", text: `Test hatası: ${e.message}` });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto animate-in">
      <div className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight text-slate-100 mb-2">Ayarlar & Sağlayıcılar (BYOK)</h1>
        <p className="text-slate-400">
          Dış servis anahtarlarınızı buraya girebilirsiniz. Anahtarlarınız sunucuda Fernet (AES-128-CBC) ile şifrelenir ve maskeli gösterilir.
        </p>
      </div>

      {statusMsg && (
        <div
          className={`p-4 rounded-lg mb-6 border ${
            statusMsg.type === "success"
              ? "bg-emerald-50 border-emerald-300 text-emerald-800"
              : "bg-rose-50 border-rose-200 text-rose-800"
          }`}
        >
          {statusMsg.text}
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-slate-800 mb-6 gap-2">
        {["github", "llm", "brave", "serpapi"].map((tab) => (
          <button
            key={tab}
            onClick={() => {
              setActiveTab(tab);
              setStatusMsg(null);
              setSecretInput("");
            }}
            className={`px-4 py-2 font-medium text-sm transition-colors border-b-2 -mb-px ${
              activeTab === tab
                ? "border-blue-500 text-blue-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {tab.toUpperCase()}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-6 shadow-sm">
        {activeTab === "github" && (
          <div className="space-y-4">
            <h2 className="text-lg font-semibold text-slate-100">GitHub Personal Access Token (PAT)</h2>
            <p className="text-sm text-slate-400">
              API limitini 60 istek/saat seviyesinden 5.000 istek/saat seviyesine çıkarır. Yalnızca public okuma izni yeterlidir.
            </p>
            {getProvider("github")?.secret_masked && (
              <div className="text-xs bg-slate-900 p-2 rounded border border-slate-800 text-slate-300 font-mono">
                Mevcut Token: {getProvider("github")?.secret_masked}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                {getProvider("github")?.secret_masked ? "Yeni Token (değiştirmek istemiyorsanız boş bırakın)" : "GitHub PAT"}
              </label>
              <input
                type="password"
                placeholder="ghp_..."
                value={secretInput}
                onChange={(e) => setSecretInput(e.target.value)}
                className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-slate-200 text-sm focus:border-blue-500 focus:outline-none"
              />
            </div>
            <div className="flex gap-3 pt-2">
              <button
                onClick={() => handleSave("github")}
                className="bg-blue-600 hover:bg-blue-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition-colors"
              >
                Kaydet
              </button>
              <button
                onClick={() => handleTest("github")}
                disabled={testing}
                className="bg-slate-800 hover:bg-slate-800 text-slate-200 font-medium px-4 py-2 rounded-lg text-sm transition-colors disabled:opacity-50"
              >
                {testing ? "Test Ediliyor..." : "Bağlantıyı Test Et"}
              </button>
            </div>
          </div>
        )}

        {activeTab === "llm" && (
          <div className="space-y-4">
            <h2 className="text-lg font-semibold text-slate-100">OpenAI-Uyumlu LLM Sağlayıcısı</h2>
            <p className="text-sm text-slate-400">
              OpenCode Go veya standart OpenAI uç noktasını yapılandırın.
            </p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">Preset</label>
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
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-slate-200 text-sm focus:border-blue-500 focus:outline-none"
                >
                  <option value="opencode-go">OpenCode Go ($10/ay)</option>
                  <option value="opencode-zen">OpenCode Zen</option>
                  <option value="custom">Özel OpenAI-Uyumlu</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">Model Adı</label>
                <input
                  type="text"
                  value={llmModel}
                  onChange={(e) => setLlmModel(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-slate-200 text-sm focus:border-blue-500 focus:outline-none"
                />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">Base URL</label>
              <input
                type="text"
                value={llmBaseUrl}
                onChange={(e) => setLlmBaseUrl(e.target.value)}
                className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-slate-200 text-sm focus:border-blue-500 focus:outline-none"
              />
            </div>
            {getProvider("llm")?.secret_masked && (
              <div className="text-xs bg-slate-900 p-2 rounded border border-slate-800 text-slate-300 font-mono">
                Mevcut API Key: {getProvider("llm")?.secret_masked}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                {getProvider("llm")?.secret_masked ? "Yeni API Key (boş bırakılırsa mevcut korunur)" : "API Key"}
              </label>
              <input
                type="password"
                placeholder="sk-..."
                value={secretInput}
                onChange={(e) => setSecretInput(e.target.value)}
                className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-slate-200 text-sm focus:border-blue-500 focus:outline-none"
              />
            </div>
            <div className="flex gap-3 pt-2">
              <button
                onClick={() => handleSave("llm")}
                className="bg-blue-600 hover:bg-blue-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition-colors"
              >
                Kaydet
              </button>
              <button
                onClick={() => handleTest("llm")}
                disabled={testing}
                className="bg-slate-800 hover:bg-slate-800 text-slate-200 font-medium px-4 py-2 rounded-lg text-sm transition-colors disabled:opacity-50"
              >
                {testing ? "Test Ediliyor..." : "Bağlantıyı Test Et"}
              </button>
            </div>
          </div>
        )}

        {activeTab === "brave" && (
          <div className="space-y-4">
            <h2 className="text-lg font-semibold text-slate-100">Brave Search API</h2>
            <p className="text-sm text-slate-400">
              Web ve platform arama motoru desteği için Brave Search API anahtarı (önerilen).
            </p>
            {getProvider("brave")?.secret_masked && (
              <div className="text-xs bg-slate-900 p-2 rounded border border-slate-800 text-slate-300 font-mono">
                Mevcut Anahtar: {getProvider("brave")?.secret_masked}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                {getProvider("brave")?.secret_masked ? "Yeni Token (boş bırakılırsa korunur)" : "Brave API Key"}
              </label>
              <input
                type="password"
                placeholder="BSA..."
                value={secretInput}
                onChange={(e) => setSecretInput(e.target.value)}
                className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-slate-200 text-sm focus:border-blue-500 focus:outline-none"
              />
            </div>
            <div className="flex gap-3 pt-2">
              <button
                onClick={() => handleSave("brave")}
                className="bg-blue-600 hover:bg-blue-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition-colors"
              >
                Kaydet
              </button>
              <button
                onClick={() => handleTest("brave")}
                disabled={testing}
                className="bg-slate-800 hover:bg-slate-800 text-slate-200 font-medium px-4 py-2 rounded-lg text-sm transition-colors disabled:opacity-50"
              >
                {testing ? "Test Ediliyor..." : "Bağlantıyı Test Et"}
              </button>
            </div>
          </div>
        )}

        {activeTab === "serpapi" && (
          <div className="space-y-4">
            <h2 className="text-lg font-semibold text-slate-100">SerpAPI (Google Arama)</h2>
            <p className="text-sm text-slate-400">
              Google arama sonuçları için alternatif sağlayıcı.
            </p>
            {getProvider("serpapi")?.secret_masked && (
              <div className="text-xs bg-slate-900 p-2 rounded border border-slate-800 text-slate-300 font-mono">
                Mevcut Anahtar: {getProvider("serpapi")?.secret_masked}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                {getProvider("serpapi")?.secret_masked ? "Yeni Token (boş bırakılırsa korunur)" : "SerpAPI Key"}
              </label>
              <input
                type="password"
                placeholder="serpapi key..."
                value={secretInput}
                onChange={(e) => setSecretInput(e.target.value)}
                className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-slate-200 text-sm focus:border-blue-500 focus:outline-none"
              />
            </div>
            <div className="flex gap-3 pt-2">
              <button
                onClick={() => handleSave("serpapi")}
                className="bg-blue-600 hover:bg-blue-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition-colors"
              >
                Kaydet
              </button>
              <button
                onClick={() => handleTest("serpapi")}
                disabled={testing}
                className="bg-slate-800 hover:bg-slate-800 text-slate-200 font-medium px-4 py-2 rounded-lg text-sm transition-colors disabled:opacity-50"
              >
                {testing ? "Test Ediliyor..." : "Bağlantıyı Test Et"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
