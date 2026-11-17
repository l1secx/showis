"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AtSign,
  GraduationCap,
  MapPin,
  Radar,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import { api } from "../../../lib/api";

interface SeedRow {
  platform: string;
  username: string;
  url: string;
}

const PLATFORMS = [
  "github", "gitlab", "reddit", "keybase", "steam", "devto",
  "dockerhub", "npm", "pypi", "chess", "hackernews", "gravatar",
  "instagram", "tiktok", "youtube", "medium", "telegram", "tryhackme",
  "hackthebox", "web", "unknown",
];

const DRAFT_KEY = "showis:new-case-draft";

/** Hedef kimliği modu — `name`: ad/soyad, `handle`: kullanıcı adı (nickname). */
type TargetMode = "name" | "handle";

const EMPTY = {
  mode: "name" as TargetMode,
  name: "",
  purpose: "",
  legal_basis: "self_check",
  notes: "",
  retention_days: 30,
  first_name: "",
  last_name: "",
  nickname: "",
  city: "",
  company: "",
  school: "",
};

type CaseForm = typeof EMPTY;

export default function NewCasePage() {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<CaseForm>(() => {
    if (typeof window === "undefined") return EMPTY;
    try {
      return { ...EMPTY, ...JSON.parse(window.sessionStorage.getItem(DRAFT_KEY) || "{}") };
    } catch {
      return EMPTY;
    }
  });
  const [seeds, setSeeds] = useState<SeedRow[]>([{ platform: "github", username: "", url: "" }]);
  const [autoScan, setAutoScan] = useState(true);
  const [scanMode, setScanMode] = useState("O2");

  // Hedef modu form durumunda tutulur; taslaktan gelen deger dogrulanir.
  const mode: TargetMode = form.mode === "handle" ? "handle" : "name";
  const setMode = (next: TargetMode) => setForm((f) => ({ ...f, mode: next }));

  const firstNameRef = useRef<HTMLInputElement>(null);
  const nicknameRef = useRef<HTMLInputElement>(null);

  // Mod degisince ilgili zorunlu alan odaklanir (alan sirasi moda gore degisir).
  useEffect(() => {
    (mode === "handle" ? nicknameRef : firstNameRef).current?.focus();
  }, [mode]);

  // Yazilanlar sekmeler arasinda kaybolmasin.
  useEffect(() => {
    window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(form));
  }, [form]);

  const set = (k: keyof CaseForm, v: string | number) => setForm((f) => ({ ...f, [k]: v }));

  const updateSeed = (i: number, k: keyof SeedRow, v: string) =>
    setSeeds((rows) => rows.map((r, idx) => (idx === i ? { ...r, [k]: v } : r)));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const hints: Record<string, string> = {};
      if (form.city) hints.city = form.city.trim();
      if (form.company) hints.company = form.company.trim();
      if (form.school) hints.school = form.school.trim();

      const nickname = form.nickname.replace(/^@/, "").trim();
      const fullname = `${form.first_name} ${form.last_name}`.trim();
      const label = mode === "handle" ? `@${nickname || "hedef"}` : fullname || "Hedef";
      const res = await api.createCase({
        name: form.name || `${label} — ${new Date().toLocaleDateString("tr-TR")}`,
        purpose: form.purpose || `${label} için kamuya açık kaynak taraması`,
        legal_basis: form.legal_basis,
        notes: form.notes || null,
        retention_days: Number(form.retention_days),
        target: {
          kind: mode,
          first_name: form.first_name,
          last_name: form.last_name,
          nickname,
          hints,
        },
        seeds: seeds
          .filter((s) => s.username || s.url)
          .map((s) => ({ platform: s.platform, username: s.username || null, url: s.url || null })),
      });

      window.sessionStorage.removeItem(DRAFT_KEY);
      router.push(
        autoScan
          ? `/cases/${res.id}/scan?start=1&mode=${scanMode}`
          : `/cases/${res.id}/scan`
      );
    } catch (err: any) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <div className="animate-in">
      <div className="mb-6 flex items-center gap-2 text-xs text-slate-500">
        <span>Vakalar</span>
        <span>/</span>
        <span className="text-slate-300">Yeni vaka</span>
      </div>

      <form onSubmit={submit} className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          <section className="card p-5">
            <div className="flex items-center gap-2">
              <UserRound size={15} className="text-blue-400" />
              <h2 className="text-sm font-semibold text-slate-100">Hedef</h2>
              <span className="ml-auto text-[11px] text-slate-500">
                {mode === "handle"
                  ? "kullanıcı adı tek başına yeterli"
                  : "sadece isim yeterli — ipuçları eşleşmeyi daraltır"}
              </span>
            </div>

            <div
              role="group"
              aria-label="Hedef türü"
              className="mt-4 inline-flex rounded-lg border border-slate-800 bg-slate-950 p-0.5"
            >
              {[
                { value: "name" as const, label: "İsimle ara", icon: UserRound },
                { value: "handle" as const, label: "Kullanıcı adıyla ara", icon: AtSign },
              ].map((m) => {
                const Icon = m.icon;
                const active = mode === m.value;
                return (
                  <button
                    key={m.value}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setMode(m.value)}
                    className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-colors ${
                      active
                        ? "bg-blue-500/15 text-blue-200"
                        : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    <Icon size={13} className={active ? "text-blue-400" : ""} />
                    {m.label}
                  </button>
                );
              })}
            </div>

            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              {mode === "handle" && (
                <label className="block sm:col-span-2">
                  <span className="label">
                    Kullanıcı adı <span className="text-blue-400">*</span>
                  </span>
                  <div className="relative">
                    {!form.nickname.startsWith("@") && (
                      <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 font-mono text-sm text-slate-500">
                        @
                      </span>
                    )}
                    <input
                      ref={nicknameRef}
                      required
                      className="input pl-7 font-mono"
                      placeholder="abdulkeremdemir"
                      value={form.nickname}
                      onChange={(e) => set("nickname", e.target.value)}
                    />
                  </div>
                </label>
              )}
              <label className="block">
                <span className="label">
                  Ad{" "}
                  {mode === "name" ? (
                    <span className="text-blue-400">*</span>
                  ) : (
                    <span className="font-normal tracking-normal text-slate-600">(ops.)</span>
                  )}
                </span>
                <input
                  ref={firstNameRef}
                  required={mode === "name"}
                  className="input"
                  placeholder="Kerem"
                  value={form.first_name}
                  onChange={(e) => set("first_name", e.target.value)}
                />
              </label>
              <label className="block">
                <span className="label">
                  Soyad <span className="font-normal tracking-normal text-slate-600">(ops.)</span>
                </span>
                <input
                  className="input"
                  placeholder="Boz"
                  value={form.last_name}
                  onChange={(e) => set("last_name", e.target.value)}
                />
              </label>
              {mode === "name" && (
                <label className="block sm:col-span-2">
                  <span className="label">
                    Nick <span className="font-normal tracking-normal text-slate-600">(ops.)</span>
                  </span>
                  <div className="relative">
                    {!form.nickname.startsWith("@") && (
                      <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 font-mono text-sm text-slate-500">
                        @
                      </span>
                    )}
                    <input
                      ref={nicknameRef}
                      className="input pl-7 font-mono"
                      placeholder="kerem"
                      value={form.nickname}
                      onChange={(e) => set("nickname", e.target.value)}
                    />
                  </div>
                </label>
              )}
            </div>
            {mode === "handle" && (
              <p className="mt-3 text-[11px] text-slate-500">
                Ad ve soyad opsiyonel — bulunan hesapların size ait olduğunu doğrulamak için kullanılır.
              </p>
            )}
            <div className="mt-4 grid gap-4 sm:grid-cols-3">
              <label className="block">
                <span className="label">
                  <GraduationCap size={11} className="mr-1 inline" />
                  Okul
                </span>
                <input
                  className="input"
                  placeholder="ODTÜ"
                  value={form.school}
                  onChange={(e) => set("school", e.target.value)}
                />
              </label>
              <label className="block">
                <span className="label">
                  <MapPin size={11} className="mr-1 inline" />
                  Şehir
                </span>
                <input
                  className="input"
                  placeholder="Ankara"
                  value={form.city}
                  onChange={(e) => set("city", e.target.value)}
                />
              </label>
              <label className="block">
                <span className="label">Şirket</span>
                <input
                  className="input"
                  placeholder="CyberSec Inc"
                  value={form.company}
                  onChange={(e) => set("company", e.target.value)}
                />
              </label>
            </div>
          </section>

          <section className="card p-5">
            <div className="flex items-center gap-2">
              <ShieldCheck size={15} className="text-blue-400" />
              <h2 className="text-sm font-semibold text-slate-100">Vaka Bilgileri</h2>
            </div>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="label">Vaka adı</span>
                <input
                  className="input"
                  placeholder="boş bırakılırsa hedeften üretilir"
                  value={form.name}
                  onChange={(e) => set("name", e.target.value)}
                />
              </label>
              <label className="block">
                <span className="label">Hukuki dayanak *</span>
                <select
                  className="input"
                  value={form.legal_basis}
                  onChange={(e) => set("legal_basis", e.target.value)}
                >
                  <option value="self_check">Öz kontrol (kendi hesabım)</option>
                  <option value="consent">Açık rıza</option>
                  <option value="legitimate_interest">Meşru menfaat</option>
                </select>
              </label>
            </div>
            <label className="mt-4 block">
              <span className="label">Amaç</span>
              <input
                className="input"
                placeholder="Örn: Kendi hesaplarımın görünürlüğünü ölçmek"
                value={form.purpose}
                onChange={(e) => set("purpose", e.target.value)}
              />
            </label>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="label">Notlar</span>
                <input
                  className="input"
                  value={form.notes}
                  onChange={(e) => set("notes", e.target.value)}
                />
              </label>
              <label className="block">
                <span className="label">Saklama süresi (gün)</span>
                <input
                  type="number"
                  min={1}
                  max={365}
                  className="input"
                  value={form.retention_days}
                  onChange={(e) => set("retention_days", Number(e.target.value))}
                />
              </label>
            </div>
          </section>

          <details className="card p-5">
            <summary className="cursor-pointer text-sm font-semibold text-slate-100">
              Seed hesaplar <span className="font-normal text-slate-500">(opsiyonel)</span>
            </summary>
            <div className="mt-4 space-y-3">
              {seeds.map((s, i) => (
                <div key={i} className="grid grid-cols-3 items-end gap-3">
                  <label className="block">
                    <span className="label">Platform</span>
                    <select
                      className="input"
                      value={s.platform}
                      onChange={(e) => updateSeed(i, "platform", e.target.value)}
                    >
                      {PLATFORMS.map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block">
                    <span className="label">Kullanıcı adı</span>
                    <input
                      className="input"
                      value={s.username}
                      onChange={(e) => updateSeed(i, "username", e.target.value)}
                    />
                  </label>
                  <label className="block">
                    <span className="label">URL</span>
                    <input
                      className="input"
                      value={s.url}
                      onChange={(e) => updateSeed(i, "url", e.target.value)}
                    />
                  </label>
                </div>
              ))}
              <button
                type="button"
                onClick={() => setSeeds((s) => [...s, { platform: "github", username: "", url: "" }])}
                className="btn-ghost btn-sm"
              >
                + Seed ekle
              </button>
            </div>
          </details>
        </div>

        <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
          <div className="card p-5">
            <div className="flex items-center gap-2">
              <Radar size={15} className="text-blue-400" />
              <h2 className="text-sm font-semibold text-slate-100">Tarama</h2>
            </div>
            <div className="mt-3 space-y-2">
              {[
                { value: "O1", label: "O1 · Hızlı", hint: "~30 sn · arama + API" },
                { value: "O2", label: "O2 · Standart", hint: "~2 dk · + site-check" },
                { value: "O3", label: "O3 · Derin", hint: "~3 dk · + tarayıcı render" },
              ].map((m) => (
                <button
                  key={m.value}
                  type="button"
                  onClick={() => setScanMode(m.value)}
                  className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left transition-colors ${
                    scanMode === m.value
                      ? "border-blue-500/50 bg-blue-500/10"
                      : "border-slate-800 bg-slate-950 hover:border-slate-700"
                  }`}
                >
                  <span className={scanMode === m.value ? "text-sm text-blue-200" : "text-sm text-slate-300"}>
                    {m.label}
                  </span>
                  <span className="font-mono text-[10px] text-slate-500">{m.hint}</span>
                </button>
              ))}
            </div>

            <label className="mt-4 flex cursor-pointer items-start gap-2 text-xs text-slate-400">
              <input
                type="checkbox"
                checked={autoScan}
                onChange={(e) => setAutoScan(e.target.checked)}
                className="mt-0.5 accent-[#06b6d4]"
              />
              Vaka oluşturulunca taramayı hemen başlat
            </label>

            <button type="submit" disabled={saving} className="btn-primary mt-4 w-full">
              {saving ? (
                "Oluşturuluyor…"
              ) : (
                <>
                  <Sparkles size={15} /> {autoScan ? "Oluştur ve tara" : "Vakayı oluştur"}
                </>
              )}
            </button>
            <button type="button" onClick={() => router.back()} className="btn-ghost mt-2 w-full">
              İptal
            </button>
            {error && (
              <p className="mt-3 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-300">
                {error}
              </p>
            )}
          </div>

          <div className="card p-5 text-xs leading-relaxed text-slate-400">
            <p className="section-title mb-2">Nasıl çalışır</p>
            <ol className="space-y-1.5">
              <li>1. Hedef kimliği (isim veya kullanıcı adı) ve ipuçlarıyla arama motoru sorguları üretilir.</li>
              <li>2. Her sonuç isim/kullanıcı adı benzerliği ve ipuçlarıyla skorlanır.</li>
              <li>3. Bulunan kullanıcı adları 48 sitede doğrulanır.</li>
              <li>4. Sonuçlar onaylı / aday / ipucu olarak Bulgular&apos;a düşer.</li>
            </ol>
            <p className="mt-3 text-[11px] text-slate-500">
              Yalnızca herkese açık kaynaklar taranır; giriş duvarı aşılmaz.
            </p>
          </div>
        </aside>
      </form>
    </div>
  );
}
