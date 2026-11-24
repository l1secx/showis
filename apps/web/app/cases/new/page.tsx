"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, ChevronDown, Loader2 } from "lucide-react";
import { api, type TargetKind } from "../../../lib/api";

interface SeedRow {
  platform: string;
  username: string;
  url: string;
}

const PLATFORMS = [
  "github", "gitlab", "reddit", "keybase", "steam", "devto",
  "dockerhub", "npm", "pypi", "chess", "hackernews", "gravatar",
  "instagram", "tiktok", "youtube", "medium", "telegram", "tryhackme",
  "hackthebox", "bitbucket", "codeberg", "codepen", "codewars", "leetcode",
  "stackoverflow", "bluesky", "mastodon", "huggingface", "kaggle", "twitch",
  "kick", "discord", "behance", "dribbble", "pinterest", "tumblr", "linktr.ee",
  "about.me", "substack", "soundcloud", "duolingo", "lichess", "patreon",
  "kofi", "buymeacoffee", "gumroad", "hackerone", "bugcrowd", "web", "unknown",
];

const DRAFT_KEY = "showis:new-case-draft";
/** Handle modunda `Ad`/`Soyad` alanlarını açıklayan ipucu metninin kimliği. */
const NAME_HINT_ID = "target-name-hint";
/** Kullanıcı adı (nick) alanının uzunluk ipucunun kimliği. */
const NICK_HINT_ID = "target-nick-hint";
/** Ad modundaki opsiyonel nick alanının ayrı ipucu kimliği (ipuçları eşzamanlı yaşamaz). */
const NICK_HINT_ID_NAME = "target-nick-hint-name";

const EMPTY = {
  mode: "name" as TargetKind,
  name: "",
  purpose: "",
  legal_basis: "self_check",
  notes: "",
  retention_days: 30,
  first_name: "",
  last_name: "",
  nickname: "",
  email: "",
  phone: "",
  domain: "",
  ip: "",
  city: "",
  company: "",
  school: "",
};

type CaseForm = typeof EMPTY;

/**
 * Backend kuralı (`apps/api/showis/api/v1/cases.py::_check_nickname`) ile aynı:
 * baştaki `@` atılır, en az 3 karakter ve en az bir harf/rakam gerekir.
 * `required=false` (ad modundaki opsiyonel nick) boş değeri kabul eder.
 */
function nickError(raw: string, required: boolean): string | null {
  const value = raw.trim().replace(/^@+/, "").trim();
  if (!value) return required ? "Kullanıcı adı gerekli." : null;
  if (value.length < 3) return "en az 3 karakter";
  if (!/[\p{L}\p{N}]/u.test(value)) return "en az bir harf veya rakam içermeli";
  return null;
}

const NICK_RULE_HINT = "en az 3 karakter, en az 1 harf veya rakam";

const SCAN_MODES = [
  { value: "O1", label: "Hızlı", hint: "Arama ve API çağrıları, site kontrolü yok" },
  { value: "O2", label: "Standart", hint: "Arama, API ve 6 kullanıcı adı site kontrolü" },
  { value: "O3", label: "Derin", hint: "Standart mod ve tarayıcı ile 10 kullanıcı adı" },
];

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
  // Hukuki dayanak + robots/ToS onayı: yeni kayıt için zorunlu operatör kapısı.
  const [authorizationConfirmed, setAuthorizationConfirmed] = useState(false);

  // Hedef modu form durumunda tutulur; taslaktan gelen deger dogrulanir.
  const mode: TargetKind = ["handle", "email", "phone", "domain", "ip"].includes(form.mode)
    ? form.mode
    : "name";
  const setMode = (next: TargetKind) => setForm((f) => ({ ...f, mode: next }));

  const firstNameRef = useRef<HTMLInputElement>(null);
  const nicknameRef = useRef<HTMLInputElement>(null);
  const identifierRef = useRef<HTMLInputElement>(null);

  // Mod degisince ilgili zorunlu alan odaklanir (alan sirasi moda gore degisir).
  useEffect(() => {
    if (mode === "handle") nicknameRef.current?.focus();
    else if (mode === "name") firstNameRef.current?.focus();
    else identifierRef.current?.focus();
  }, [mode]);

  // Yazilanlar sekmeler arasinda kaybolmasin.
  useEffect(() => {
    window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(form));
  }, [form]);

  const set = (k: keyof CaseForm, v: string | number) => setForm((f) => ({ ...f, [k]: v }));

  // Nick kuralı backend'de de var; UI'da erken göstermek 422 turunu önler.
  const handleNickError = nickError(form.nickname, mode === "handle");
  const nickInvalid = handleNickError !== null;

  const updateSeed = (i: number, k: keyof SeedRow, v: string) =>
    setSeeds((rows) => rows.map((r, idx) => (idx === i ? { ...r, [k]: v } : r)));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    // Buton devre dışı olsa da Enter ile gönderim mümkün: kuralı burada da uygula.
    const nickIssue = nickError(form.nickname, mode === "handle");
    if (nickIssue) {
      setError(`Kullanıcı adı geçersiz: ${nickIssue}`);
      nicknameRef.current?.focus();
      return;
    }
    if (!authorizationConfirmed) {
      setError(
        "Devam etmek için hukuki dayanağı ve üçüncü taraf sitelerin robots.txt/kullanım koşullarına uyacağınızı onaylayın."
      );
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const hints: Record<string, string> = {};
      if (form.city) hints.city = form.city.trim();
      if (form.company) hints.company = form.company.trim();
      if (form.school) hints.school = form.school.trim();

      const nickname = form.nickname.trim().replace(/^@/, "").trim();
      const fullname = `${form.first_name} ${form.last_name}`.trim();
      const identifier =
        mode === "email"
          ? form.email.trim().toLowerCase()
          : mode === "phone"
            ? form.phone.trim()
            : mode === "domain"
              ? form.domain.trim().toLowerCase()
              : mode === "ip"
                ? form.ip.trim()
                : "";
      const label =
        mode === "handle"
          ? `@${nickname || "hedef"}`
          : mode === "name"
            ? fullname || "Hedef"
            : identifier || "Hedef";
      const res = await api.createCase({
        name: form.name || `${label} — ${new Date().toLocaleDateString("tr-TR")}`,
        purpose: form.purpose || `${label} için kamuya açık kaynak taraması`,
        legal_basis: form.legal_basis,
        authorization_confirmed: true,
        notes: form.notes || null,
        retention_days: Number(form.retention_days),
        target: {
          kind: mode,
          first_name: form.first_name,
          last_name: form.last_name,
          nickname,
          email: form.email.trim(),
          phone: form.phone.trim(),
          domain: form.domain.trim(),
          ip: form.ip.trim(),
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
    <div className="space-y-6">
      <div>
        <nav className="flex items-center gap-1.5 text-[12px] text-ink-3">
          <Link href="/" className="transition-colors hover:text-ink">
            Vakalar
          </Link>
          <span className="text-ink-3" aria-hidden>
            /
          </span>
          <span className="text-ink-2">Yeni vaka</span>
        </nav>
        <h1 className="page-title mt-1.5">Yeni vaka</h1>
        <p className="page-subtitle">
          Kamuya açık kaynaklarda araştırılacak hedefi tanımlayın. Hukuki dayanak ve saklama
          süresi kayıtla birlikte saklanır.
        </p>
      </div>

      <form onSubmit={submit} className="grid grid-cols-1 gap-8 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {/* --- Hedef --- */}
          <section className="panel">
            <div className="panel-head">
              <h2 className="panel-title">Hedef</h2>
              <p className="text-[12px] text-ink-3">
                {mode === "handle"
                  ? "Kullanıcı adı tek başına yeterlidir"
                  : mode === "name"
                    ? "İsim tek başına yeterlidir, ipuçları doğrulamayı güçlendirir"
                    : "Tek kimlik anahtarı yeterlidir; ilgili ücretsiz kaynaklar taranır"}
              </p>
            </div>

            <div className="p-5">
              <div
                role="radiogroup"
                aria-label="Hedef türü"
                className="flex flex-wrap rounded-[6px] border border-line-2 bg-sunken p-0.5"
              >
                {[
                  { value: "name" as const, label: "İsim ve soyisim" },
                  { value: "handle" as const, label: "Kullanıcı adı" },
                  { value: "email" as const, label: "E-posta" },
                  { value: "phone" as const, label: "Telefon" },
                  { value: "domain" as const, label: "Domain" },
                  { value: "ip" as const, label: "IP" },
                ].map((m) => {
                  const active = mode === m.value;
                  return (
                    <button
                      key={m.value}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      onClick={() => setMode(m.value)}
                      className={`rounded-[4px] px-3 py-1.5 text-[12.5px] transition-colors ${
                        active
                          ? "bg-surface font-medium text-ink shadow-scrim"
                          : "text-ink-3 hover:text-ink"
                      }`}
                    >
                      {m.label}
                    </button>
                  );
                })}
              </div>

              <div className="mt-5 grid gap-4 sm:grid-cols-2">
                {mode === "handle" && (
                  <label className="block sm:col-span-2">
                    <span className="label">
                      Kullanıcı adı <span aria-hidden="true" className="text-danger">*</span>
                    </span>
                    <div className="relative">
                      {!form.nickname.startsWith("@") && (
                        <span
                          aria-hidden="true"
                          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 font-mono text-[13px] text-ink-3"
                        >
                          @
                        </span>
                      )}
                      <input
                        ref={nicknameRef}
                        required
                        minLength={3}
                        aria-invalid={nickInvalid}
                        aria-describedby={NICK_HINT_ID}
                        className="input pl-7 font-mono"
                        placeholder="abdulkeremdemir"
                        value={form.nickname}
                        onChange={(e) => set("nickname", e.target.value)}
                      />
                    </div>
                    <span
                      id={NICK_HINT_ID}
                      aria-live="polite"
                      className={nickInvalid ? "hint-error" : "hint"}
                    >
                      {handleNickError ?? NICK_RULE_HINT}
                    </span>
                  </label>
                )}
                {mode === "email" && (
                  <label className="block sm:col-span-2">
                    <span className="label">
                      E-posta <span aria-hidden="true" className="text-danger">*</span>
                    </span>
                    <input
                      ref={identifierRef}
                      type="email"
                      required
                      className="input font-mono"
                      placeholder="ornek@example.com"
                      value={form.email}
                      onChange={(e) => set("email", e.target.value)}
                    />
                    <span className="hint">Gravatar ve GitHub commit e-postası üzerinden aranır.</span>
                  </label>
                )}
                {mode === "phone" && (
                  <label className="block sm:col-span-2">
                    <span className="label">
                      Telefon <span aria-hidden="true" className="text-danger">*</span>
                    </span>
                    <input
                      ref={identifierRef}
                      type="tel"
                      required
                      className="input font-mono"
                      placeholder="+905551112233"
                      value={form.phone}
                      onChange={(e) => set("phone", e.target.value)}
                    />
                    <span className="hint">E.164 biçimi: + ülke kodu, ardından numara.</span>
                  </label>
                )}
                {mode === "domain" && (
                  <label className="block sm:col-span-2">
                    <span className="label">
                      Alan adı <span aria-hidden="true" className="text-danger">*</span>
                    </span>
                    <input
                      ref={identifierRef}
                      required
                      className="input font-mono"
                      placeholder="example.com"
                      value={form.domain}
                      onChange={(e) => set("domain", e.target.value)}
                    />
                    <span className="hint">RDAP, DNS, crt.sh ve Wayback CDX ile zenginleştirilir.</span>
                  </label>
                )}
                {mode === "ip" && (
                  <label className="block sm:col-span-2">
                    <span className="label">
                      IP adresi <span aria-hidden="true" className="text-danger">*</span>
                    </span>
                    <input
                      ref={identifierRef}
                      required
                      className="input font-mono"
                      placeholder="192.0.2.1"
                      value={form.ip}
                      onChange={(e) => set("ip", e.target.value)}
                    />
                    <span className="hint">RDAP ve ters DNS (PTR) ile zenginleştirilir.</span>
                  </label>
                )}
                <label className="block">
                  <span className="label">
                    Ad{" "}
                    {mode === "name" ? (
                      <span aria-hidden="true" className="text-danger">*</span>
                    ) : (
                      <span className="font-normal text-ink-3">(opsiyonel)</span>
                    )}
                  </span>
                  <input
                    ref={firstNameRef}
                    required={mode === "name"}
                    aria-describedby={mode === "handle" ? NAME_HINT_ID : undefined}
                    className="input"
                    placeholder="Kerem"
                    value={form.first_name}
                    onChange={(e) => set("first_name", e.target.value)}
                  />
                </label>
                <label className="block">
                  <span className="label">
                    Soyad <span className="font-normal text-ink-3">(opsiyonel)</span>
                  </span>
                  <input
                    aria-describedby={mode === "handle" ? NAME_HINT_ID : undefined}
                    className="input"
                    placeholder="Boz"
                    value={form.last_name}
                    onChange={(e) => set("last_name", e.target.value)}
                  />
                </label>
                {mode === "name" && (
                  <label className="block sm:col-span-2">
                    <span className="label">
                      Kullanıcı adı <span className="font-normal text-ink-3">(opsiyonel)</span>
                    </span>
                    <div className="relative">
                      {!form.nickname.startsWith("@") && (
                        <span
                          aria-hidden="true"
                          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 font-mono text-[13px] text-ink-3"
                        >
                          @
                        </span>
                      )}
                      <input
                        ref={nicknameRef}
                        minLength={3}
                        aria-invalid={nickInvalid}
                        aria-describedby={NICK_HINT_ID_NAME}
                        className="input pl-7 font-mono"
                        placeholder="kerem"
                        value={form.nickname}
                        onChange={(e) => set("nickname", e.target.value)}
                      />
                    </div>
                    <span
                      id={NICK_HINT_ID_NAME}
                      aria-live="polite"
                      className={nickInvalid ? "hint-error" : "hint"}
                    >
                      {handleNickError ?? `opsiyonel — ${NICK_RULE_HINT}`}
                    </span>
                  </label>
                )}
              </div>
              {mode === "handle" && (
                <p id={NAME_HINT_ID} className="hint">
                  Ad ve soyad isteğe bağlıdır; bulunan hesapların size ait olduğunu
                  doğrulamak için kullanılır.
                </p>
              )}

              <div className="mt-5 grid gap-4 sm:grid-cols-3">
                <label className="block">
                  <span className="label">Okul</span>
                  <input
                    className="input"
                    placeholder="ODTÜ"
                    value={form.school}
                    onChange={(e) => set("school", e.target.value)}
                  />
                </label>
                <label className="block">
                  <span className="label">Şehir</span>
                  <input
                    className="input"
                    placeholder="Ankara"
                    value={form.city}
                    onChange={(e) => set("city", e.target.value)}
                  />
                </label>
                <label className="block">
                  <span className="label">Kurum</span>
                  <input
                    className="input"
                    placeholder="CyberSec Inc"
                    value={form.company}
                    onChange={(e) => set("company", e.target.value)}
                  />
                </label>
              </div>
            </div>
          </section>

          {/* --- Kayıt bilgileri --- */}
          <section className="panel">
            <div className="panel-head">
              <h2 className="panel-title">Kayıt bilgileri</h2>
            </div>
            <div className="grid gap-4 p-5 sm:grid-cols-2">
              <label className="block">
                <span className="label">Vaka adı</span>
                <input
                  className="input"
                  placeholder="Boş bırakılırsa hedeften üretilir"
                  value={form.name}
                  onChange={(e) => set("name", e.target.value)}
                />
              </label>
              <label className="block">
                <span className="label">
                  Hukuki dayanak <span aria-hidden="true" className="text-danger">*</span>
                </span>
                <select
                  className="select"
                  value={form.legal_basis}
                  onChange={(e) => set("legal_basis", e.target.value)}
                >
                  <option value="self_check">Öz kontrol — kendi hesabım</option>
                  <option value="consent">Açık rıza</option>
                  <option value="legitimate_interest">Meşru menfaat</option>
                </select>
              </label>
              <label className="block sm:col-span-2">
                <span className="label">Amaç</span>
                <input
                  className="input"
                  placeholder="Örn: Kendi hesaplarımın görünürlüğünü ölçmek"
                  value={form.purpose}
                  onChange={(e) => set("purpose", e.target.value)}
                />
              </label>
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

          {/* --- Seed hesaplar --- */}
          <details className="panel group">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-4 text-[13.5px] font-medium text-ink marker:content-none">
              <ChevronDown
                size={14}
                aria-hidden
                className="text-ink-3 transition-transform group-open:rotate-180"
              />
              Seed hesaplar
              <span className="text-[12px] font-normal text-ink-3">opsiyonel</span>
            </summary>
            <div className="space-y-3 border-t border-line p-5">
              {seeds.map((s, i) => (
                <div key={i} className="grid grid-cols-3 items-end gap-3">
                  <label className="block">
                    <span className="label">Platform</span>
                    <select
                      className="select"
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
                className="btn-secondary btn-sm"
              >
                Seed ekle
              </button>
            </div>
          </details>
        </div>

        {/* --- Yan panel: tarama modu ve kaydı oluştur --- */}
        <aside className="space-y-5 lg:sticky lg:top-20 lg:self-start">
          <div className="panel">
            <div className="panel-head">
              <h2 className="panel-title">Tarama modu</h2>
            </div>

            <div className="p-5">
              <fieldset className="space-y-2">
                <legend className="sr-only">Tarama modu</legend>
                {SCAN_MODES.map((m) => {
                  const active = scanMode === m.value;
                  return (
                    <label
                      key={m.value}
                      className={`flex cursor-pointer items-start gap-2.5 rounded-[6px] border px-3 py-2.5 transition-colors ${
                        active
                          ? "border-accent bg-accent-soft"
                          : "border-line-2 bg-surface hover:border-ink-4"
                      }`}
                    >
                      <input
                        type="radio"
                        name="scan-mode"
                        value={m.value}
                        checked={active}
                        onChange={() => setScanMode(m.value)}
                        className="mt-[3px] h-3.5 w-3.5 shrink-0 accent-[rgb(var(--accent))]"
                      />
                      <span className="min-w-0">
                        <span className="block text-[13px] font-medium text-ink">
                          {m.value} — {m.label}
                        </span>
                        <span className="mt-0.5 block text-[12px] leading-snug text-ink-3">
                          {m.hint}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </fieldset>

              <label className="mt-4 flex cursor-pointer items-start gap-2.5 border-t border-line pt-4 text-[12.5px] text-ink-2">
                <input
                  type="checkbox"
                  checked={autoScan}
                  onChange={(e) => setAutoScan(e.target.checked)}
                  className="mt-[3px] h-3.5 w-3.5 shrink-0 rounded-[3px] accent-[rgb(var(--accent))]"
                />
                <span>Kayıt oluşturulunca taramayı hemen başlat</span>
              </label>

              <label className="mt-3 flex cursor-pointer items-start gap-2.5 rounded-[6px] border border-warn-line bg-warn-soft px-3 py-2.5 text-[12.5px] leading-snug text-warn">
                <input
                  type="checkbox"
                  checked={authorizationConfirmed}
                  onChange={(e) => setAuthorizationConfirmed(e.target.checked)}
                  aria-describedby="authorization-hint"
                  className="mt-[3px] h-3.5 w-3.5 shrink-0 rounded-[3px] accent-[rgb(var(--accent))]"
                />
                <span>
                  Hukuki dayanağımı ve yalnızca kamuya açık kaynakları, üçüncü taraf
                  sitelerin <strong className="font-semibold">robots.txt</strong> ve
                  kullanım koşullarına uygun şekilde tarayacağımı onaylıyorum.
                  <span id="authorization-hint" className="mt-1 block text-[11.5px] font-normal">
                    Giriş duvarı, CAPTCHA veya erişim kontrolü aşılmaz; veri sahibi
                    talepleri ve saklama süresi operatör sorumluluğundadır.
                  </span>
                </span>
              </label>

              {error && (
                <p
                  role="alert"
                  className="mt-4 rounded-[6px] border border-danger-line bg-danger-soft px-3 py-2 text-[12.5px] text-danger"
                >
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={saving || nickInvalid || !authorizationConfirmed}
                className="btn-primary mt-5 w-full"
              >
                {saving ? (
                  <>
                    <Loader2 size={14} className="animate-spin" aria-hidden />
                    Kayıt oluşturuluyor
                  </>
                ) : autoScan ? (
                  "Oluştur ve taramayı başlat"
                ) : (
                  <>
                    <Check size={14} aria-hidden />
                    Vakayı oluştur
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={() => router.back()}
                className="btn-quiet mt-1 w-full"
              >
                Vazgeç
              </button>
            </div>
          </div>

          {/* Süreç: gerçekten sıralı bir adım dizisi olduğu için numaralı. */}
          <div className="panel p-5">
            <h2 className="section-heading">Nasıl çalışır</h2>
            <ol className="mt-3 space-y-2.5">
              {[
                "Girdiğiniz tüm alanlar vaka araştırma havuzuna eklenir.",
                "Hedef kimliği, ipuçları ve havuz kayıtlarından arama sorguları üretilir.",
                "Her sonuç isim ve kullanıcı adı benzerliğiyle skorlanır.",
                "Bulunan kullanıcı adları 48 sitede doğrulanır.",
                "Bulgu ve AI önerileri onayınızdan sonra kesinleşir.",
              ].map((step, i) => (
                <li key={i} className="flex gap-2.5 text-[12.5px] leading-relaxed text-ink-3">
                  <span className="numeric w-4 shrink-0 text-ink-3">{i + 1}.</span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
            <p className="mt-3 border-t border-line pt-3 text-[12px] leading-relaxed text-ink-3">
              Yalnızca herkese açık kaynaklar taranır; giriş duvarı aşılmaz.
            </p>
          </div>
        </aside>
      </form>
    </div>
  );
}
