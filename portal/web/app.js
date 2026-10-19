/* Vortex Veri — dashboard front-end.
   No build step and no CDN: charts are hand-drawn SVG so the portal works
   offline and starts instantly. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const SVGNS = "http://www.w3.org/2000/svg";
let csrfToken = "";
let currentUser = null;
const ICONS = {
  columns: `<svg class="icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M4 5h16M4 12h16M4 19h16M9 5v14M16 5v14"/></svg>`,
  refresh: `<svg class="icon icon-refresh" aria-hidden="true" viewBox="0 0 24 24"><path d="M20 7v5h-5"/><path d="M4 17v-5h5"/><path d="M6.1 8.1A7 7 0 0 1 18.8 10L20 12"/><path d="M17.9 15.9A7 7 0 0 1 5.2 14L4 12"/></svg>`,
};
const PAGE_INFO = {
  overview: ["Performance overview", "Monitor spend, traffic, leads, and completed outcomes in one view."],
  paid: ["Paid campaign performance", "Compare Google and Meta Ads with consistent campaign and CRM metrics."],
  web: ["Website performance", "Understand GA4 traffic quality by channel, source, or campaign."],
  search: ["Organic search", "Review search demand and visibility by query or landing page."],
  crm: ["CRM funnel", "Follow demand and supply leads from creation to completed outcomes."],
  connections: ["Data connections", "Manage source accounts and review every data refresh."],
  workspace: ["Reporting workspace", "Explore data, govern metrics, build dashboards, and manage delivery."],
  hub: ["Data hub", "Build reusable reporting views, metrics, exports, automations, and connections."],
  settings: ["Profile & settings", "Manage your portal profile, security, notifications, and appearance."],
};

const state = {
  start: null, end: null, currency: "INR",
  dataTo: null,
  campaignSrc: "", campaignFunnel: "", campaignGoogleAccount: "", campaignQuery: "", campaignRows: [],
  campaignColumns: new Set(),
  campaignWidths: {}, campaignRowHeights: {}, campaignRowHeight: 38,
  tableViews: {},
  webDim: "channel", searchDim: "query",
  customMetrics: [], overview: null, meta: null,
  connections: [], activeSourceId: null,
};
let columnDrawerContext = null;
let profilePreferences = {
  theme: "system",
  accent: "",
  notifications: { browser: false, sync_failures: true, sync_complete: false },
};
let draftAccent = "";
let gmailStatus = null;

const PAID_PLATFORM_META = {
  google_ads: { label: "Google Ads", color: "#4285f4", badge: "google" },
  meta_ads: { label: "Meta Ads", color: "#0668e1", badge: "meta" },
  linkedin_ads: { label: "LinkedIn Ads", color: "#0a66c2", badge: "linkedin" },
  tiktok_ads: { label: "TikTok Ads", color: "#111111", badge: "tiktok" },
};

const TABLE_DENSITY_HEIGHTS = { compact: 32, comfortable: 38, spacious: 48 };
const PAID_ESSENTIAL_COLUMNS = [
  "campaign", "funnel", "type", "spend", "currency", "impressions", "clicks", "ctr", "cpc",
  "conversions", "crm_leads", "crm_sales", "cpl", "cac", "cpa",
];

/* ------------------------------------------------------------- formatting */
const nf = new Intl.NumberFormat("en-IN");
const nf1 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });

function money(v, compact = false) {
  if (v == null || Number.isNaN(v)) return "—";
  const sym = state.currency === "INR" ? "₹" : "";
  if (compact) {
    if (Math.abs(v) >= 1e7) return `${sym}${nf1.format(v / 1e7)}Cr`;
    if (Math.abs(v) >= 1e5) return `${sym}${nf1.format(v / 1e5)}L`;
    if (Math.abs(v) >= 1e3) return `${sym}${nf1.format(v / 1e3)}k`;
  }
  return sym + nf.format(Math.round(v));
}
function moneyInCurrency(v, currency) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  if (!currency) return nf.format(Math.round(Number(v)));
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency", currency: String(currency).toUpperCase(), maximumFractionDigits: 0,
    }).format(Number(v));
  } catch (_) {
    return `${String(currency).toUpperCase()} ${nf.format(Math.round(Number(v)))}`;
  }
}
const num = (v) => (v == null ? "—" : nf.format(Math.round(v)));
const dec = (v) => (v == null ? "—" : nf1.format(v));
const pct = (v) => (v == null ? "—" : `${nf1.format(v)}%`);

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function toast(msg, tone = "") {
  const t = $("#toast");
  t.textContent = msg;
  const isError = tone === "error" || /^(could not|sync failed|choose |keep at least)/i.test(msg);
  t.classList.toggle("error", isError);
  t.setAttribute("role", isError ? "alert" : "status");
  t.classList.add("show");
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove("show"), 3200);
  const notificationPrefs = profilePreferences.notifications || {};
  const isSyncNotice = /sync|refresh/i.test(msg) || /source[s]? updated/i.test(msg);
  const shouldNotify = document.hidden && notificationPrefs.browser && isSyncNotice
    && ((isError && notificationPrefs.sync_failures) || (!isError && notificationPrefs.sync_complete));
  if (shouldNotify && "Notification" in window && Notification.permission === "granted") {
    try { new Notification(isError ? "Portal sync needs attention" : "Portal sync complete", { body: msg }); }
    catch (_) { /* browser notification delivery is best effort */ }
  }
}

function applyTheme(theme, accent = profilePreferences.accent || "") {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else document.documentElement.removeAttribute("data-theme");
  if (/^#[0-9a-f]{6}$/i.test(accent)) {
    document.documentElement.style.setProperty("--series-1", accent);
    document.documentElement.style.setProperty("--primary-soft", `color-mix(in srgb, ${accent} 10%, transparent)`);
    document.documentElement.style.setProperty("--hover", `color-mix(in srgb, ${accent} 7%, transparent)`);
  } else {
    document.documentElement.style.removeProperty("--series-1");
    document.documentElement.style.removeProperty("--primary-soft");
    document.documentElement.style.removeProperty("--hover");
  }
  profilePreferences.theme = theme;
  updateBrandMark();
}

function updateBrandMark() {
  const mark = $("#brandMark");
  if (!mark) return;
  const explicitTheme = document.documentElement.dataset.theme;
  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches || false;
  const dark = explicitTheme ? explicitTheme === "dark" : prefersDark;
  mark.src = dark ? "/static/assets/vortex-veri-mark-dark.png" : "/static/assets/vortex-veri-mark.png";
  mark.classList.toggle("is-dark", dark);
}

function renderAppearanceSettings(preferences = profilePreferences) {
  const theme = preferences.theme || "system";
  draftAccent = preferences.accent || "";
  $$('input[name="themePreference"]').forEach((input) => { input.checked = input.value === theme; });
  $$('input[name="accentPreference"]').forEach((input) => {
    input.checked = input.value.toLowerCase() === draftAccent.toLowerCase();
    input.closest(".accent-option")?.classList.toggle("selected", input.checked);
  });
  const custom = /^#[0-9a-f]{6}$/i.test(draftAccent);
  $("#accentColorPicker").value = custom ? draftAccent : "#0f5262";
  $("#accentHex").value = custom ? draftAccent.toUpperCase() : "#0F5262";
  $(".accent-custom")?.classList.toggle("selected", custom);
  applyTheme(theme, draftAccent);
}

function previewAppearance() {
  const theme = $('input[name="themePreference"]:checked')?.value || "system";
  applyTheme(theme, draftAccent);
  const message = $("#appearanceSaveState");
  if (message) message.textContent = "Previewing unsaved changes";
}

function selectSettingsPanel(name) {
  const allowed = new Set(["profile", "security", "notifications", "appearance", "apps"]);
  if (!allowed.has(name) || (name === "apps" && currentUser?.role !== "admin")) name = "profile";
  $$('[data-settings-panel]').forEach((button) => {
    const selected = button.dataset.settingsPanel === name;
    button.classList.toggle("on", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $$('[data-settings-content]').forEach((panel) => { panel.hidden = panel.dataset.settingsContent !== name; });
  if (name === "apps" && currentUser?.role === "admin") loadGmailSettings().catch((err) => setGmailError(err.message));
  if (name === "security" && currentUser?.role === "admin") loadSettingsInvitations().catch((err) => setSettingsMessage("settingsInviteMessage", err.message, true));
}

function setSettingsMessage(id, message, error = false) {
  const node = $(`#${id}`);
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("error", error);
}

async function loadSettingsInvitations() {
  const list = $("#settingsInvitationList");
  if (!list || currentUser?.role !== "admin") return;
  list.innerHTML = '<div class="empty compact">Loading invitations…</div>';
  const invitations = await api("/api/invitations");
  if (!Array.isArray(invitations) || !invitations.length) {
    list.innerHTML = '<div class="empty compact">No pending invitations.</div>';
    return;
  }
  list.innerHTML = invitations.map((invite) => `<div class="settings-invitation-row"><div><b>${esc(invite.display_name || invite.email)}</b><small>${esc(invite.email)} · Viewer · ${esc(invite.status)} · Expires ${esc(invite.expires_at)}</small></div><button type="button" data-settings-revoke-invite="${Number(invite.id)}">Revoke</button></div>`).join("");
}

async function createSettingsInvitation(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  setButtonBusy(button, true);
  setSettingsMessage("settingsInviteMessage", "Creating one-time invite…");
  $("#settingsInviteCreated").hidden = true;
  try {
    const invite = await api("/api/invitations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: $("#settingsInviteEmail").value.trim(), display_name: $("#settingsInviteName").value.trim() }) });
    const link = `${location.origin}/invite#token=${encodeURIComponent(invite.token)}`;
    $("#settingsInviteLink").value = link;
    $("#settingsInviteExpiry").textContent = `Expires ${invite.expires_at}. The token is shown only once, so copy and share this link privately now.`;
    $("#settingsInviteCreated").hidden = false;
    $("#settingsInviteName").value = "";
    $("#settingsInviteEmail").value = "";
    setSettingsMessage("settingsInviteMessage", "Viewer invitation created.");
    await loadSettingsInvitations();
  } catch (err) {
    setSettingsMessage("settingsInviteMessage", err.message, true);
  } finally { setButtonBusy(button, false); }
}

async function copySettingsInvitation() {
  const input = $("#settingsInviteLink");
  if (!input?.value) return;
  try {
    await navigator.clipboard.writeText(input.value);
    setSettingsMessage("settingsInviteMessage", "Invite link copied. Share it privately with the invitee.");
  } catch {
    input.focus(); input.select();
    setSettingsMessage("settingsInviteMessage", "The link is selected. Copy it and share it privately.", true);
  }
}

function renderProfileSettings(saved) {
  currentUser = saved.user;
  profilePreferences = saved.preferences;
  applyRoleUI(saved.user);
  renderAppearanceSettings(saved.preferences);
  $("#settingsAvatar").textContent = (saved.user.display_name || saved.user.email).trim().slice(0, 1).toUpperCase();
  $("#settingsIdentityName").textContent = saved.user.display_name || saved.user.email;
  $("#settingsIdentityRole").textContent = saved.user.role[0].toUpperCase() + saved.user.role.slice(1);
  $("#profileName").value = saved.user.display_name || "";
  $("#profileEmail").value = saved.user.email;
  $("#profileRole").value = saved.user.role[0].toUpperCase() + saved.user.role.slice(1);
  $("#accountLabel").textContent = saved.user.display_name || saved.user.email;
  $("#accountRole").textContent = saved.user.role;
  $("#accountAvatar").textContent = (saved.user.display_name || saved.user.email || "U").trim().slice(0, 1).toUpperCase();
  $("#browserNotifications").checked = saved.preferences.notifications.browser;
  $("#notifySyncFailures").checked = saved.preferences.notifications.sync_failures;
  $("#notifySyncComplete").checked = saved.preferences.notifications.sync_complete;
  return saved;
}

async function loadProfileSettings() {
  return renderProfileSettings(await api("/api/auth/profile"));
}

async function loadGmailSettings() {
  const info = await api("/api/auth/gmail");
  gmailStatus = info;
  const pill = $("#gmailStatusPill"), card = $("#gmailConnectionCard");
  const configure = $("#gmailConfigure"), connect = $("#gmailConnect");
  const test = $("#gmailTest"), disconnect = $("#gmailDisconnect");
  const state = !info.exists ? "Not set up" : info.connected ? "Connected" : info.needs_setup ? "Setup needed" : "Authorization needed";
  pill.textContent = state;
  pill.className = `settings-status ${info.connected ? "is-good" : "is-idle"}`;
  card.innerHTML = info.connected
    ? `<span class="gmail-account-mark" aria-hidden="true">G</span><div><small class="gmail-account-label">Authorized Google account</small><b>${esc(info.account_email || "Account email unavailable")}</b><small>${info.account_email ? "Gmail send access is granted. Inbox access is not granted." : "Reconnect to verify which Google account is authorized."}</small></div>`
    : `<span class="gmail-account-mark is-idle" aria-hidden="true">G</span><div><b>${info.exists ? "Gmail access not granted" : "No Gmail account authorized"}</b><small>${info.exists ? (info.needs_setup ? "Add the Google OAuth app credentials, then choose Grant Gmail access." : "Choose Grant Gmail access and approve send-only permission in Google.") : "Set up Gmail access first. Then choose the Google account and grant send-only permission."}</small></div>`;
  configure.hidden = false;
  configure.textContent = info.exists ? "Edit OAuth setup" : "Set up Gmail access";
  configure.classList.toggle("primary", !info.exists || info.needs_setup);
  connect.hidden = !info.exists || info.needs_setup;
  connect.textContent = info.connected ? "Switch Google account" : "Grant Gmail access";
  test.hidden = !info.connected;
  disconnect.hidden = !info.exists;
  return info;
}

function setGmailError(message) {
  const card = $("#gmailConnectionCard");
  if (card) card.innerHTML = `<div class="settings-inline-error" role="alert">${esc(message)}</div>`;
  $("#gmailStatusPill").textContent = "Could not load";
  $("#gmailStatusPill").className = "settings-status is-error";
}

async function loadSettings() {
  const profile = await loadProfileSettings();
  if (profile.user.role === "admin" && !$("[data-settings-content='apps']").hidden) {
    await loadGmailSettings();
  }
}

function setButtonBusy(button, busy) {
  if (!button) return;
  button.disabled = busy;
  button.classList.toggle("is-busy", busy);
  if (busy) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
}

function syncSummary(results = []) {
  const successful = results.filter((item) => item.status === "ok");
  const empty = results.filter((item) => item.status === "empty");
  const skipped = results.filter((item) => item.status === "skipped");
  const failed = results.filter((item) => item.status === "error");
  const rows = successful.reduce((total, item) => total + Number(item.rows || 0), 0);
  const updated = successful.length
    ? `${successful.length} source${successful.length === 1 ? "" : "s"} updated · ${nf.format(rows)} rows`
    : "No sources updated";
  if (failed.length) {
    const names = failed.map((item) => item.name).join(", ");
    const emptyNote = empty.length ? `. ${empty.length} returned no rows` : "";
    return { message: `${updated}. ${failed.length} failed: ${names}${emptyNote}`, tone: "error" };
  }
  if (empty.length) {
    const names = empty.map((item) => item.name).join(", ");
    const skippedNote = skipped.length ? `. ${skipped.length} skipped` : "";
    return { message: `${updated}. No rows returned by: ${names}${skippedNote}. Check the source and selected dates.`, tone: "error" };
  }
  if (skipped.length) return { message: `${updated}. ${skipped.length} source${skipped.length === 1 ? " was" : "s were"} skipped`, tone: "" };
  if (!results.length) return { message: "No connected sources were available to sync.", tone: "error" };
  return { message: updated, tone: "" };
}

async function syncLatest(button) {
  setButtonBusy(button, true);
  try {
    const response = await api("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wait: true }),
    });
    await refreshPortal(false);
    const summary = syncSummary(response.results || []);
    toast(summary.message, summary.tone);
    return response;
  } catch (err) {
    toast(`Could not sync latest data: ${err.message}`, "error");
    return null;
  } finally {
    setButtonBusy(button, false);
  }
}

async function api(path, opts = {}) {
  const headers = new Headers(opts.headers || {});
  const method = String(opts.method || "GET").toUpperCase();
  if (!/^(GET|HEAD|OPTIONS)$/.test(method) && csrfToken && !path.startsWith("/api/auth/login") && !path.startsWith("/api/auth/setup")) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const res = await fetch(path, { ...opts, headers, credentials: "same-origin" });
  if (res.status === 401 && !path.startsWith("/api/auth/status")) {
    location.replace(`/login${location.hash}`);
    throw new Error("Your portal session expired. Sign in again.");
  }
  if (!res.ok) {
    const raw = await res.text();
    let message = raw;
    try { message = JSON.parse(raw).error || raw; } catch (_) { /* plain text */ }
    throw new Error(message || `Request failed (${res.status})`);
  }
  return res.json();
}

const UI_PREFERENCE_KEYS = new Set([
  "savedPortalViews", "customMetrics", "calendarDatePresets", "campaignColumns",
  "portalTableColumns", "portalTableWidths", "portalTableRowHeights",
  "campaignWidths", "campaignRowHeights", "campaignRowHeight",
  "portalTableDensity", "portalFreezeFirstColumn", "portalTableStripes",
]);
const UI_PREFERENCE_JSON_KEYS = new Set([
  "savedPortalViews", "customMetrics", "calendarDatePresets", "campaignColumns",
  "portalTableColumns", "portalTableWidths", "portalTableRowHeights",
  "campaignWidths", "campaignRowHeights",
]);
const UI_PREFERENCE_SIZING_KEYS = new Set([
  "portalTableWidths", "portalTableRowHeights", "campaignWidths", "campaignRowHeights", "campaignRowHeight",
]);
const uiPreferenceTimers = new Map();
const uiPreferenceWrites = new Map();

function parseUiPreference(key, raw) {
  return UI_PREFERENCE_JSON_KEYS.has(key) ? JSON.parse(raw) : raw;
}

function queueUiPreferenceWrite(key, value) {
  const previous = uiPreferenceWrites.get(key) || Promise.resolve();
  const next = previous.catch(() => {}).then(() => api("/api/ui-preferences", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  }));
  uiPreferenceWrites.set(key, next);
  next.catch((error) => toast(`Could not save ${key}: ${error.message}`, "error"));
}

function saveUiPreferenceRaw(key, raw) {
  if (!UI_PREFERENCE_KEYS.has(key)) throw new Error(`Unsupported UI preference: ${key}`);
  localStorage.setItem(key, raw);
  if (uiPreferenceTimers.has(key)) clearTimeout(uiPreferenceTimers.get(key));
  const save = () => {
    uiPreferenceTimers.delete(key);
    queueUiPreferenceWrite(key, parseUiPreference(key, raw));
  };
  if (UI_PREFERENCE_SIZING_KEYS.has(key)) uiPreferenceTimers.set(key, setTimeout(save, 400));
  else save();
}

function removeUiPreference(key) {
  if (!UI_PREFERENCE_KEYS.has(key)) throw new Error(`Unsupported UI preference: ${key}`);
  localStorage.removeItem(key);
  if (uiPreferenceTimers.has(key)) clearTimeout(uiPreferenceTimers.get(key));
  uiPreferenceTimers.delete(key);
  queueUiPreferenceWrite(key, null);
}

async function hydrateUiPreferences(user) {
  const owner = String(user.id ?? user.email);
  const previousOwner = localStorage.getItem("portalUiPreferencesMigratedFor");
  if (previousOwner && previousOwner !== owner) {
    UI_PREFERENCE_KEYS.forEach((key) => localStorage.removeItem(key));
  }
  const response = await api("/api/ui-preferences");
  const saved = response.preferences || {};
  const migrateLegacy = !previousOwner;
  const migrationFailures = [];
  for (const key of UI_PREFERENCE_KEYS) {
    if (Object.hasOwn(saved, key)) {
      localStorage.setItem(key, UI_PREFERENCE_JSON_KEYS.has(key)
        ? JSON.stringify(saved[key]) : String(saved[key]));
    } else if (migrateLegacy && localStorage.getItem(key) !== null) {
      const raw = localStorage.getItem(key);
      let value;
      try { value = parseUiPreference(key, raw); }
      catch (_) { localStorage.removeItem(key); continue; }
      try {
        await api("/api/ui-preferences", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key, value }),
        });
      } catch (error) {
        migrationFailures.push(key);
      }
    } else {
      localStorage.removeItem(key);
    }
  }
  if (migrationFailures.length) throw new Error(`Could not move ${migrationFailures.join(", ")} to your account. Retry by reloading.`);
  localStorage.setItem("portalUiPreferencesMigratedFor", owner);
}

function applyRoleUI(user) {
  currentUser = user;
  $("#portalAccount").hidden = false;
  $("#accountLabel").textContent = user.display_name || user.email;
  $("#accountRole").textContent = user.role;
  $("#accountAvatar").textContent = (user.display_name || user.email || "U").trim().slice(0, 1).toUpperCase();
  const isAdmin = user.role === "admin";
  const canUseWorkspace = user.role === "admin" || user.role === "analyst";
  $$('[data-admin-only]').forEach((node) => { node.hidden = !isAdmin; });
  $$('[data-analyst-only]').forEach((node) => { node.hidden = !canUseWorkspace; });
  if (!canUseWorkspace && ["workspace", "connections"].includes(location.hash.slice(1))) {
    history.replaceState(null, "", "#overview");
  } else if (!isAdmin && location.hash.slice(1) === "connections") {
    history.replaceState(null, "", "#overview");
  }
  if (user.role === "viewer") {
    const sourceTabs = $("#sourceTabs");
    if (sourceTabs) sourceTabs.hidden = true;
  }
}

function connectionIssue(message = "") {
  const lower = String(message).toLowerCase();
  if (lower.includes("invalid_client") || lower.includes("client secret is invalid"))
    return ["OAuth credentials rejected", "Open Edit, replace the client secret, save, then press Connect again."];
  if (lower.includes("insufficient permission") || lower.includes("does not have sufficient permission"))
    return ["Account permission required", "Ask the property owner to grant this Google account access, then reconnect."];
  if (lower.includes("http 401") || lower.includes("unauthor"))
    return ["Authentication expired or invalid", "Open Edit and reconnect the account or replace its API key."];
  if (lower.includes("http 403") || lower.includes("forbidden"))
    return ["This account cannot access the requested data", "Check the account permissions and selected property, then sync again."];
  if (lower.includes("http 429") || lower.includes("rate limit"))
    return ["The source is temporarily rate limited", "Wait a few minutes, then press Sync now."];
  if (lower.includes("could not reach") || lower.includes("timed out"))
    return ["The data source could not be reached", "Check the internet connection and try Sync now again."];
  return ["The latest sync failed", "Review the technical detail below, update the connection if needed, then press Sync now."];
}

function qs() {
  return `start=${state.start}&end=${state.end}`;
}

function renderSourceStatus(sources = []) {
  const node = $("#sourceStatus");
  node.hidden = activeTab === "settings";
  const healthText = $("#healthText");
  const healthDot = $("#healthDot");
  const crmCampaign = sources.find((source) => source.id === "crm_campaign");
  const crmFunnel = sources.find((source) => source.id === "crm");
  const visibleSources = sources.filter((source) => !["crm_campaign", "crm"].includes(source.id));
  if (crmCampaign || crmFunnel) {
    const campaignText = crmCampaign
      ? crmCampaign.detail || `Campaign leads ${({ current: "current", delayed: "delayed", error: "refresh failed" }[crmCampaign.status] || "unavailable")} ${crmCampaign.data_to ? `through ${crmCampaign.data_to}` : ""}`.trim()
      : "Campaign leads unavailable";
    const funnelText = crmFunnel
      ? crmFunnel.detail || `Funnel stages ${({ current: "current", delayed: "delayed", error: "refresh failed" }[crmFunnel.status] || "unavailable")} ${crmFunnel.data_to ? `through ${crmFunnel.data_to}` : ""}`.trim()
      : "Funnel stages unavailable";
    visibleSources.push({
      id: "crm", label: "Superleap CRM",
      status: [crmCampaign?.status, crmFunnel?.status].includes("error") ? "error"
        : [crmCampaign?.status, crmFunnel?.status].includes("delayed") ? "delayed"
          : crmFunnel?.status || crmCampaign?.status || "empty",
      detail: `${campaignText}; ${funnelText}`,
    });
  }
  const attention = visibleSources.filter((source) => !["current", "disabled"].includes(source.status)).length;
  if (healthText) healthText.textContent = attention
    ? `${attention} data source${attention === 1 ? "" : "s"} need attention`
    : "All data sources current";
  if (healthDot) healthDot.classList.toggle("warning", attention > 0);
  const list = $("#sourceStatusList");
  const count = $("#sourceStatusCount");
  if (count) count.textContent = String(visibleSources.length);
  list.innerHTML = visibleSources.map((source) => {
    const stateLabel = { current: "Current", delayed: "Delayed", empty: "Unavailable", error: "Refresh failed", disabled: "Turned off" }[source.status] || "Unknown";
    const rawDetail = source.detail || (source.data_to
      ? `through ${source.data_to}`
      : "awaiting data");
    const detail = source.status === "error" ? connectionIssue(rawDetail)[0] : rawDetail;
    return `<div class="source-pill ${source.status}" role="listitem" title="${esc(`${source.label}: ${stateLabel} · ${detail}`)}">
      <i></i><span><b>${esc(source.label)}</b> <em>${stateLabel}</em><small>${esc(detail)}</small></span>
    </div>`;
  }).join("");
}

function sourceStatusFromConnections(connections) {
  const definitions = [
    ["google_ads", "Google Ads"], ["meta_ads", "Meta Ads"],
    ["ga4", "GA4"], ["gsc", "Search Console"],
  ];
  const sources = definitions.map(([id, label]) => {
    const conn = connections.find((c) => c.provider === id);
    if (!conn) return { id, label, status: "empty", detail: "not connected" };
    if (conn.last_status === "ok") {
      return { id, label, status: "current", detail: "sync healthy" };
    }
    if (conn.last_status === "error") {
      return { id, label, status: "delayed", detail: "needs attention" };
    }
    if (conn.last_status === "empty") {
      return { id, label, status: "delayed", detail: "latest sync returned no rows" };
    }
    return { id, label, status: "empty", detail: "setup incomplete" };
  });
  const directCrm = connections.filter((c) => c.provider === "superleap_crm" && c.enabled);
  const legacyCrm = connections.filter((c) => c.provider === "csv" && c.name.startsWith("Superleap CRM"));
  const crm = directCrm.length ? directCrm : legacyCrm;
  const crmOk = crm.length > 0 && crm.every((c) => c.last_status === "ok");
  const range = crm.map((c) => c.last_message || "").join(" ").match(/→\s*(\d{4}-\d{2}-\d{2})/);
  const crmFailed = crm.some((c) => c.last_status === "error");
  sources.push({
    id: "crm", label: "Superleap CRM",
    status: crmOk ? "current" : crmFailed ? "error" : (crm.length ? "delayed" : "empty"),
    detail: crmOk && range ? `through ${range[1]}` : (crm.some((c) => c.last_status === "empty")
      ? "latest sync returned no rows" : (crm.length ? "needs attention" : "not connected")),
  });
  return sources;
}

/* ------------------------------------------------------------ date range */
function portalTodayIso() {
  const timezone = state.meta?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date()).filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function latestReportingDate() {
  const today = portalTodayIso();
  return state.dataTo && state.dataTo <= today ? state.dataTo : today;
}

function isoDaysAgo(n) {
  return shiftIso(portalTodayIso(), -n);
}

function shiftIso(day, amount) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + amount);
  return d.toISOString().slice(0, 10);
}

function setRange(days) {
  state.end = latestReportingDate();
  state.start = shiftIso(state.end, -(days - 1));
  $("#start").value = state.start;
  $("#end").value = state.end;
  updateDateRangeLabel();
}

function displayDate(day) {
  if (!day) return "";
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  }).format(new Date(`${day}T00:00:00Z`));
}

function updateDateRangeLabel() {
  $("#dateRangeLabel").textContent = `${displayDate(state.start)} – ${displayDate(state.end)}`;
}

function closeDateRange() {
  $("#dateRangeMenu").hidden = true;
  $("#dateRangeToggle").setAttribute("aria-expanded", "false");
  $("#start").value = state.start;
  $("#end").value = state.end;
}

let calendarDraftStart = "";
let calendarDraftEnd = "";
let calendarMode = "start";
let calendarMonth = "";
const CALENDAR_WHEEL_ROW_HEIGHT = 36;
const calendarWheelScrollTimers = { month: null, year: null };
const DEFAULT_DATE_PRESETS = [
  { id: "default-7", label: "7 days", days: 7 },
  { id: "default-30", label: "30 days", days: 30 },
  { id: "default-90", label: "90 days", days: 90 },
];

function getCustomDatePresets() {
  try {
    const saved = JSON.parse(localStorage.getItem("calendarDatePresets") || "[]");
    return Array.isArray(saved) ? saved.filter((item) => item?.id && item?.label && Number(item?.days) > 0) : [];
  } catch (_) { return []; }
}

function renderCalendarPresets() {
  const custom = getCustomDatePresets();
  $("#calendarPresets").innerHTML = [...DEFAULT_DATE_PRESETS, ...custom].map((preset) => `
    <span class="calendar-preset-chip">
      <button type="button" data-calendar-preset="${Number(preset.days)}">${esc(preset.label)}</button>
      ${preset.id.startsWith("default-") ? "" : `<button type="button" class="calendar-preset-remove" data-delete-calendar-preset="${esc(preset.id)}" aria-label="Delete ${esc(preset.label)} preset">×</button>`}
    </span>`).join("");
}

function selectCalendarPreset(days) {
  calendarDraftEnd = latestReportingDate();
  calendarDraftStart = shiftIso(calendarDraftEnd, -(days - 1));
  calendarMonth = monthIso(calendarDraftEnd);
  setCalendarMode("end");
  renderCalendar();
}

function openDatePresetCreator() {
  modal("Create date preset", `
    <div class="preset-form">
      <label>Preset name<input id="datePresetName" maxlength="18" placeholder="Example: Last 14 days"></label>
      <label>Number of days<input id="datePresetDays" type="number" min="1" max="3650" value="14"></label>
      <p class="callout">The preset will always end on the latest available reporting date.</p>
    </div>`,
  `<button id="cancelDatePreset">Cancel</button><button id="saveDatePreset" class="primary">Create preset</button>`);
  $("#cancelDatePreset").addEventListener("click", closeModal);
  $("#saveDatePreset").addEventListener("click", () => {
    const label = $("#datePresetName").value.trim();
    const days = Number($("#datePresetDays").value);
    if (!label || !Number.isInteger(days) || days < 1 || days > 3650) {
      toast("Choose a preset name and a valid number of days"); return;
    }
    const presets = getCustomDatePresets();
    const existing = presets.find((preset) => preset.label.trim().toLocaleLowerCase()
      === label.toLocaleLowerCase());
    if (existing) { existing.label = label; existing.days = days; }
    else presets.push({ id: `date-preset-${Date.now()}`, label, days });
    saveUiPreferenceRaw("calendarDatePresets", JSON.stringify(presets.slice(-8)));
    renderCalendarPresets(); closeModal(); toast(`${label} preset ${existing ? "updated" : "created"}`);
  });
}

function monthIso(day) {
  return `${day.slice(0, 7)}-01`;
}

function calendarDate(day, options = {}) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC", ...options,
  }).format(new Date(`${day}T00:00:00Z`));
}

function calendarYearOptions() {
  const currentYear = Number(portalTodayIso().slice(0, 4));
  const firstDataYear = Number(state.meta?.data_from?.slice(0, 4));
  const selectedYear = Number(calendarMonth.slice(0, 4));
  const firstYear = Math.min(currentYear - 10, firstDataYear || currentYear - 10, selectedYear);
  const lastYear = Math.max(currentYear, 2035, selectedYear);
  return Array.from({ length: lastYear - firstYear + 1 }, (_, index) => String(firstYear + index));
}

function renderCalendarWheels() {
  const months = Array.from({ length: 12 }, (_, index) => {
    const month = String(index + 1).padStart(2, "0");
    const label = calendarDate(`2000-${month}-01`, { month: "long" });
    return `<div class="calendar-wheel-option" role="option" aria-selected="false" data-calendar-wheel-value="${month}">${esc(label)}</div>`;
  });
  const years = calendarYearOptions().map((year) =>
    `<div class="calendar-wheel-option" role="option" aria-selected="false" data-calendar-wheel-value="${year}">${year}</div>`);
  $("#calendarMonthWheel").innerHTML = months.join("");
  $("#calendarYearWheel").innerHTML = years.join("");
  syncCalendarWheelSelection(true);
}

function syncCalendarWheelSelection(center = false) {
  if (!calendarMonth) return;
  const month = calendarMonth.slice(5, 7);
  const year = calendarMonth.slice(0, 4);
  [["#calendarMonthWheel", month], ["#calendarYearWheel", year]].forEach(([selector, value]) => {
    const wheel = $(selector);
    if (!wheel) return;
    const options = [...wheel.querySelectorAll("[data-calendar-wheel-value]")];
    options.forEach((option, index) => {
      const selected = option.dataset.calendarWheelValue === value;
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-selected", String(selected));
      if (center && selected) wheel.scrollTop = index * CALENDAR_WHEEL_ROW_HEIGHT;
    });
  });
}

function calendarWheelValue(wheel) {
  const options = [...wheel.querySelectorAll("[data-calendar-wheel-value]")];
  if (!options.length) return "";
  const index = Math.max(0, Math.min(options.length - 1,
    Math.round(wheel.scrollTop / CALENDAR_WHEEL_ROW_HEIGHT)));
  return options[index].dataset.calendarWheelValue;
}

function updateCalendarPeriodFromWheels() {
  const month = calendarWheelValue($("#calendarMonthWheel"));
  const year = calendarWheelValue($("#calendarYearWheel"));
  if (!month || !year) return;
  calendarMonth = `${year}-${month}-01`;
  renderCalendar();
}

function selectCalendarWheelOption(wheel, value) {
  const options = [...wheel.querySelectorAll("[data-calendar-wheel-value]")];
  const index = options.findIndex((option) => option.dataset.calendarWheelValue === value);
  if (index < 0) return;
  wheel.scrollTop = index * CALENDAR_WHEEL_ROW_HEIGHT;
  updateCalendarPeriodFromWheels();
  wheel.focus();
}

function setCalendarPeriodPicker(open, restoreFocus = true) {
  const picker = $("#calendarPeriodPicker");
  const toggle = $("#calendarPeriodToggle");
  picker.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
  if (open) {
    renderCalendarWheels();
    $("#calendarMonthWheel").focus();
  } else if (restoreFocus) toggle.focus();
}

function setCalendarMode(mode) {
  calendarMode = mode;
  $$('[data-calendar-mode]').forEach((button) => {
    const selected = button.dataset.calendarMode === mode;
    button.classList.toggle("on", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function renderCalendar(focusDay = "") {
  const first = new Date(`${calendarMonth}T00:00:00Z`);
  const mondayOffset = (first.getUTCDay() + 6) % 7;
  const gridStart = shiftIso(calendarMonth, -mondayOffset);
  const today = portalTodayIso();
  $("#calendarPeriodLabel").textContent = calendarDate(calendarMonth, { month: "long", year: "numeric" });
  syncCalendarWheelSelection(!$("#calendarPeriodPicker").hidden);
  const currentMonth = monthIso(today);
  $("#calendarNext").disabled = calendarMonth >= currentMonth;
  $("#calendarStartValue").textContent = calendarDate(calendarDraftStart, { day: "numeric", month: "short" });
  $("#calendarEndValue").textContent = calendarDate(calendarDraftEnd, { day: "numeric", month: "short" });
  $("#calendarGrid").innerHTML = Array.from({ length: 42 }, (_, index) => {
    const day = shiftIso(gridStart, index);
    const outside = day.slice(0, 7) !== calendarMonth.slice(0, 7);
    const endpoint = day === calendarDraftStart || day === calendarDraftEnd;
    const inRange = day >= calendarDraftStart && day <= calendarDraftEnd;
    const future = day > today;
    const classes = [outside && "outside", inRange && "in-range", endpoint && "endpoint", day === today && "today", future && "future"].filter(Boolean).join(" ");
    const label = calendarDate(day, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    return `<button type="button" role="gridcell" class="${classes}" data-calendar-day="${day}" aria-label="${esc(label)}" aria-pressed="${endpoint}"${future ? " disabled aria-disabled=\"true\"" : ""}><span>${Number(day.slice(8))}</span></button>`;
  }).join("");
  if (focusDay) requestAnimationFrame(() => $(`[data-calendar-day="${focusDay}"]`)?.focus());
}

function openDateRange() {
  const today = portalTodayIso();
  calendarDraftEnd = state.end && state.end <= today ? state.end : latestReportingDate();
  calendarDraftStart = state.start && state.start <= calendarDraftEnd
    ? state.start : shiftIso(calendarDraftEnd, -29);
  calendarMonth = monthIso(calendarDraftEnd || calendarDraftStart);
  setCalendarMode("start");
  renderCalendarPresets();
  renderCalendar();
  $("#dateRangeMenu").hidden = false;
  $("#dateRangeToggle").setAttribute("aria-expanded", "true");
}

function chooseCalendarDay(day) {
  if (day > portalTodayIso()) return;
  if (!$("#calendarPeriodPicker").hidden) setCalendarPeriodPicker(false, false);
  if (calendarMode === "start") {
    calendarDraftStart = day;
    if (!calendarDraftEnd || calendarDraftEnd < day) calendarDraftEnd = day;
    setCalendarMode("end");
  } else {
    if (day < calendarDraftStart) {
      calendarDraftEnd = calendarDraftStart;
      calendarDraftStart = day;
    } else calendarDraftEnd = day;
  }
  calendarMonth = monthIso(day);
  renderCalendar(day);
}

function moveCalendarMonth(amount) {
  const current = new Date(`${calendarMonth}T00:00:00Z`);
  current.setUTCMonth(current.getUTCMonth() + amount);
  const nextMonth = current.toISOString().slice(0, 10);
  calendarMonth = nextMonth > monthIso(portalTodayIso()) ? monthIso(portalTodayIso()) : nextMonth;
  renderCalendar();
}

/* --------------------------------------------------------------- tooltip */
const tip = () => $("#tip");

function showTip(html, x, y) {
  const t = tip();
  t.innerHTML = html;
  t.style.opacity = "1";
  const r = t.getBoundingClientRect();
  const left = Math.min(x + 14, window.innerWidth - r.width - 10);
  const top = Math.max(8, Math.min(y - r.height - 12, window.innerHeight - r.height - 8));
  t.style.left = `${left}px`;
  t.style.top = `${top}px`;
}
const hideTip = () => { tip().style.opacity = "0"; };

/* ---------------------------------------------------------- line chart */
function el(tag, attrs = {}) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

function niceMax(v) {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  return Math.ceil(v / mag * 2) / 2 * mag;
}

/**
 * One value axis only — two measures of different scale get two charts,
 * never a second y-axis.
 */
function lineChart(svg, days, series, fmt) {
  svg.innerHTML = "";
  const W = svg.clientWidth || svg.parentElement.clientWidth || 600;
  const H = +svg.getAttribute("height");
  const P = { t: 12, r: 58, b: 26, l: 56 };
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

  const live = series.filter((s) => s.values.some((v) => Number.isFinite(v)));
  if (!days.length || !live.length) {
    svg.appendChild(Object.assign(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle" }),
      { textContent: "No data in this range" }));
    return;
  }

  const max = niceMax(Math.max(...live.flatMap((s) => s.values.filter(Number.isFinite))) * 1.08);
  const px = (i) => P.l + (days.length === 1 ? 0 : (i * (W - P.l - P.r)) / (days.length - 1));
  const py = (v) => H - P.b - (v / max) * (H - P.t - P.b);

  // gridlines + y ticks
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i;
    const y = py(v);
    svg.appendChild(el("line", { class: "gridline", x1: P.l, x2: W - P.r, y1: y, y2: y }));
    const tx = el("text", { x: P.l - 8, y: y + 4, "text-anchor": "end" });
    tx.textContent = fmt(v, true);
    svg.appendChild(tx);
  }
  svg.appendChild(el("line", { class: "axis", x1: P.l, x2: W - P.r, y1: py(0), y2: py(0) }));

  // x ticks — first, middle, last only, so labels never collide
  [0, Math.floor((days.length - 1) / 2), days.length - 1].forEach((i, k, arr) => {
    if (arr.indexOf(i) !== k) return;
    const tx = el("text", {
      x: px(i), y: H - 8,
      "text-anchor": i === 0 ? "start" : i === days.length - 1 ? "end" : "middle",
    });
    const d = new Date(days[i] + "T00:00:00");
    tx.textContent = d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
    svg.appendChild(tx);
  });

  // series lines + end labels (direct labelling; legend still present)
  live.forEach((s) => {
    let segment = [];
    const flush = () => {
      if (!segment.length) return;
      svg.appendChild(el("polyline", { points: segment.join(" "), fill: "none", stroke: s.color,
        "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      segment = [];
    };
    s.values.forEach((v, i) => { if (Number.isFinite(v)) segment.push(`${px(i)},${py(v)}`); else flush(); });
    flush();
    const lastIndex = s.values.findLastIndex(Number.isFinite);
    const last = s.values[lastIndex];
    const lbl = el("text", {
      x: px(lastIndex) + 7, y: py(last) + 4, fill: s.color,
      "font-size": 11, "font-weight": 600,
    });
    lbl.textContent = fmt(last, true);
    svg.appendChild(lbl);
  });

  // hover layer
  const cross = el("line", { class: "axis", y1: P.t, y2: H - P.b, opacity: 0 });
  svg.appendChild(cross);
  const dots = live.map((s) => {
    const c = el("circle", {
      r: 4.5, fill: s.color, stroke: cssVar("--surface-1"), "stroke-width": 2, opacity: 0,
    });
    svg.appendChild(c);
    return c;
  });
  const hit = el("rect", { class: "hit", x: P.l, y: 0, width: W - P.l - P.r, height: H });
  svg.appendChild(hit);

  hit.addEventListener("mousemove", (ev) => {
    const box = svg.getBoundingClientRect();
    const rel = ((ev.clientX - box.left) / box.width) * W;
    const step = days.length === 1 ? 1 : (W - P.l - P.r) / (days.length - 1);
    const i = Math.max(0, Math.min(days.length - 1, Math.round((rel - P.l) / step)));
    cross.setAttribute("x1", px(i));
    cross.setAttribute("x2", px(i));
    cross.setAttribute("opacity", 1);
    live.forEach((s, k) => {
      dots[k].setAttribute("cx", px(i));
      const value = s.values[i];
      dots[k].setAttribute("cy", Number.isFinite(value) ? py(value) : 0);
      dots[k].setAttribute("opacity", Number.isFinite(value) ? 1 : 0);
    });
    const d = new Date(days[i] + "T00:00:00");
    const rows = live.map((s) =>
      `<div class="tr"><span><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${s.color};margin-right:6px"></i>${s.label}</span><span>${Number.isFinite(s.values[i]) ? fmt(s.values[i]) : "No data"}</span></div>`
    ).join("");
    showTip(
      `<div class="th">${d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}</div>${rows}`,
      ev.clientX, ev.clientY
    );
  });
  hit.addEventListener("mouseleave", () => {
    cross.setAttribute("opacity", 0);
    dots.forEach((d) => d.setAttribute("opacity", 0));
    hideTip();
  });
}

function legend(node, series) {
  node.innerHTML = series.map((s) =>
    `<span><i style="background:${s.color}"></i>${s.label}</span>`
  ).join("");
}

/* ----------------------------------------------------------- table helper */
function tableColumnId(column, index) {
  if (column.id) return column.id;
  const slug = String(column.label || `column-${index + 1}`)
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return `${slug || "column"}-${index}`;
}

function storedObject(key) {
  try { return JSON.parse(localStorage.getItem(key) || "{}"); }
  catch (_) { return {}; }
}

function standardRowKey(row, index) {
  const identity = row.id ?? row.key ?? row.campaign_id ?? row.campaign
    ?? row.channel ?? row.source ?? row.city ?? row.lead_type ?? row.connector ?? index;
  return String(identity);
}

function setupStandardTableSizing(node, columns, rows) {
  if (!node.tHead || !node.id) return;
  node.classList.add("resizable-table");
  const widthStore = storedObject("portalTableWidths");
  const heightStore = storedObject("portalTableRowHeights");
  const headers = $$('th[data-column]', node);
  const currentWidths = {};

  columns.forEach((column, index) => {
    const key = `${node.id}:${column.id}`;
    const saved = Number(widthStore[key]);
    const measured = headers[index]?.getBoundingClientRect().width || 100;
    currentWidths[column.id] = saved >= 64 && saved <= 720
      ? saved : Math.max(64, Math.min(420, measured));
  });

  const applyWidths = () => {
    let total = 0;
    columns.forEach((column) => {
      const width = currentWidths[column.id];
      total += width;
      const col = node.querySelector(`col[data-column="${column.id}"]`);
      if (col) col.style.width = `${width}px`;
    });
    node.style.width = `${total}px`;
  };
  applyWidths();

  headers.forEach((th, index) => {
    const column = columns[index];
    if (!column) return;
    const handle = document.createElement("span");
    handle.className = "column-resizer";
    handle.title = `Resize ${column.label} column`;
    handle.tabIndex = 0;
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("aria-label", `Resize ${column.label} column. Use left and right arrow keys.`);
    th.appendChild(handle);
    handle.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      handle.setPointerCapture(ev.pointerId);
      document.body.classList.add("resizing-column");
      const startX = ev.clientX;
      const startWidth = currentWidths[column.id];
      const move = (moveEv) => {
        currentWidths[column.id] = Math.max(64, Math.min(720,
          startWidth + moveEv.clientX - startX));
        applyWidths();
      };
      const stop = () => {
        document.body.classList.remove("resizing-column");
        widthStore[`${node.id}:${column.id}`] = Math.round(currentWidths[column.id]);
        saveUiPreferenceRaw("portalTableWidths", JSON.stringify(widthStore));
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", stop);
        handle.removeEventListener("pointercancel", stop);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", stop);
      handle.addEventListener("pointercancel", stop);
    });
    handle.addEventListener("dblclick", (ev) => {
      ev.preventDefault();
      const cells = $$(`td[data-column="${column.id}"]`, node);
      const contentWidth = Math.max(th.scrollWidth, ...cells.map((cell) => cell.scrollWidth));
      currentWidths[column.id] = Math.max(64, Math.min(720, contentWidth + 18));
      widthStore[`${node.id}:${column.id}`] = Math.round(currentWidths[column.id]);
      saveUiPreferenceRaw("portalTableWidths", JSON.stringify(widthStore));
      applyWidths();
      toast(`${column.label} column auto-fitted`);
    });
    handle.addEventListener("keydown", (ev) => {
      if (!["ArrowLeft", "ArrowRight", "Home"].includes(ev.key)) return;
      ev.preventDefault();
      currentWidths[column.id] = ev.key === "Home" ? 100 : Math.max(64, Math.min(720,
        currentWidths[column.id] + (ev.key === "ArrowRight" ? 12 : -12)));
      widthStore[`${node.id}:${column.id}`] = Math.round(currentWidths[column.id]);
      saveUiPreferenceRaw("portalTableWidths", JSON.stringify(widthStore));
      applyWidths();
    });
  });

  $$('tbody tr', node).forEach((tr, index) => {
    const key = `${node.id}:${standardRowKey(rows[index] || {}, index)}`;
    const saved = Number(heightStore[key]);
    if (saved >= 28 && saved <= 160) tr.style.setProperty("--table-row-height", `${saved}px`);
    $$('td', tr).forEach((cell) => {
      const handle = document.createElement("span");
      handle.className = "row-resizer";
      handle.title = "Resize this row";
      cell.appendChild(handle);
      handle.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        handle.setPointerCapture(ev.pointerId);
        document.body.classList.add("resizing-row");
        const startY = ev.clientY;
        const startHeight = tr.getBoundingClientRect().height;
        const move = (moveEv) => {
          const height = Math.max(28, Math.min(160, startHeight + moveEv.clientY - startY));
          heightStore[key] = Math.round(height);
          tr.style.setProperty("--table-row-height", `${height}px`);
        };
        const stop = () => {
          document.body.classList.remove("resizing-row");
          saveUiPreferenceRaw("portalTableRowHeights", JSON.stringify(heightStore));
          handle.removeEventListener("pointermove", move);
          handle.removeEventListener("pointerup", stop);
          handle.removeEventListener("pointercancel", stop);
        };
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", stop);
        handle.addEventListener("pointercancel", stop);
      });
      handle.addEventListener("dblclick", (ev) => {
        ev.preventDefault();
        delete heightStore[key];
        tr.style.removeProperty("--table-row-height");
        saveUiPreferenceRaw("portalTableRowHeights", JSON.stringify(heightStore));
      });
    });
  });
}

function searchableText(column, row) {
  const holder = document.createElement("span");
  holder.innerHTML = String(column.get(row) ?? "");
  return holder.textContent || "";
}

function tableAppearancePreference() {
  return {
    density: localStorage.getItem("portalTableDensity") || "comfortable",
    freezeFirst: localStorage.getItem("portalFreezeFirstColumn") !== "false",
    stripes: localStorage.getItem("portalTableStripes") !== "false",
  };
}

function applyTableAppearance(preference = tableAppearancePreference()) {
  const density = TABLE_DENSITY_HEIGHTS[preference.density] ? preference.density : "comfortable";
  document.documentElement.dataset.tableDensity = density;
  document.documentElement.classList.toggle("no-freeze-first-column", !preference.freezeFirst);
  document.documentElement.classList.toggle("no-table-stripes", !preference.stripes);
}

function renderColumnDrawerFields() {
  if (!columnDrawerContext) return;
  const query = $("#columnDrawerSearch").value.trim().toLowerCase();
  const fields = columnDrawerContext.columns.filter((column) =>
    !query || column.label.toLowerCase().includes(query));
  const field = (column) => `
    <label class="column-drawer-field">
      <input type="checkbox" value="${esc(column.id)}" ${columnDrawerContext.draft.has(column.id) ? "checked" : ""}>
      <span><b>${esc(column.label)}</b><small>${column.num ? "Metric" : "Dimension"}</small></span>
    </label>`;
  const groups = [
    ["Fields", "Names and reporting breakdowns", fields.filter((column) => !column.num)],
    ["Metrics", "Performance and outcome measures", fields.filter((column) => column.num)],
  ].filter(([, , columns]) => columns.length);
  $("#columnDrawerFields").innerHTML = groups.map(([title, description, columns]) => `
    <section class="column-drawer-group" aria-label="${title}">
      <div class="column-drawer-group-head"><div><h3>${title}</h3><p>${description}</p></div><span>${columns.length}</span></div>
      <div class="column-drawer-field-grid">${columns.map(field).join("")}</div>
    </section>`).join("");
  $("#columnDrawerEmpty").hidden = fields.length > 0;
  $("#columnDrawerCount").textContent = `${columnDrawerContext.draft.size} of ${columnDrawerContext.columns.length} selected`;
}

function selectColumnDrawerTab(name) {
  $$("[data-column-panel]").forEach((button) => {
    const active = button.dataset.columnPanel === name;
    button.classList.toggle("on", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  $$("[data-column-section]").forEach((section) => {
    section.hidden = section.dataset.columnSection !== name;
  });
}

function openColumnDrawer({ type, node, columns, opener }) {
  const selected = type === "paid" ? state.campaignColumns : state.tableViews[node.id].columns;
  const preference = tableAppearancePreference();
  const essentials = type === "paid"
    ? PAID_ESSENTIAL_COLUMNS.filter((id) => columns.some((column) => column.id === id))
    : columns.slice(0, Math.min(6, columns.length)).map((column) => column.id);
  columnDrawerContext = {
    type, node, columns, opener,
    draft: new Set(selected),
    defaults: new Set(type === "paid" ? essentials : columns.map((column) => column.id)),
    preference: { ...preference }, initialPreference: { ...preference }, resetWidths: false, resetHeights: false,
  };
  const heading = node.closest(".card")?.querySelector("h2")?.textContent?.trim();
  $("#columnDrawerTitle").textContent = heading ? `${heading} columns` : "Modify columns";
  $("#columnDrawerSearch").value = "";
  $("#columnDrawerSearch").placeholder = `Search ${columns.length} columns…`;
  $("#columnFreezeFirst").checked = preference.freezeFirst;
  $("#columnRowStripes").checked = preference.stripes;
  $$("[data-density]").forEach((button) => {
    const selected = button.dataset.density === preference.density;
    button.classList.toggle("on", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  $$(".column-reset-list button").forEach((button) => button.classList.remove("queued"));
  selectColumnDrawerTab("columns");
  renderColumnDrawerFields();
  $("#columnDrawer").hidden = false;
  document.body.classList.add("drawer-open");
  opener?.setAttribute("aria-expanded", "true");
  requestAnimationFrame(() => $("#columnDrawerSearch").focus());
}

function closeColumnDrawer() {
  if (!columnDrawerContext) return;
  const opener = columnDrawerContext.opener;
  $("#columnDrawer").hidden = true;
  document.body.classList.remove("drawer-open");
  opener?.setAttribute("aria-expanded", "false");
  columnDrawerContext = null;
  opener?.focus();
}

function clearStoredTableSizing(context) {
  if (context.type === "paid") {
    if (context.resetWidths) {
      state.campaignWidths = {};
      removeUiPreference("campaignWidths");
    }
    if (context.resetHeights) {
      state.campaignRowHeights = {};
      removeUiPreference("campaignRowHeights");
    }
    return;
  }
  if (context.resetWidths) {
    const widths = storedObject("portalTableWidths");
    Object.keys(widths).filter((key) => key.startsWith(`${context.node.id}:`)).forEach((key) => delete widths[key]);
    saveUiPreferenceRaw("portalTableWidths", JSON.stringify(widths));
  }
  if (context.resetHeights) {
    const heights = storedObject("portalTableRowHeights");
    Object.keys(heights).filter((key) => key.startsWith(`${context.node.id}:`)).forEach((key) => delete heights[key]);
    saveUiPreferenceRaw("portalTableRowHeights", JSON.stringify(heights));
  }
}

function saveColumnDrawer() {
  const context = columnDrawerContext;
  if (!context) return;
  if (!context.draft.size) {
    toast("Keep at least one column visible");
    selectColumnDrawerTab("columns");
    return;
  }
  const preference = {
    density: context.preference.density,
    freezeFirst: $("#columnFreezeFirst").checked,
    stripes: $("#columnRowStripes").checked,
  };
  saveUiPreferenceRaw("portalTableDensity", preference.density);
  saveUiPreferenceRaw("portalFreezeFirstColumn", String(preference.freezeFirst));
  saveUiPreferenceRaw("portalTableStripes", String(preference.stripes));
  applyTableAppearance(preference);
  clearStoredTableSizing(context);
  if (context.type === "paid") {
    state.campaignColumns = new Set(context.draft);
    saveUiPreferenceRaw("campaignColumns", JSON.stringify([...state.campaignColumns]));
    if (context.initialPreference.density !== preference.density) {
      state.campaignRowHeight = TABLE_DENSITY_HEIGHTS[preference.density];
      state.campaignRowHeights = {};
      saveUiPreferenceRaw("campaignRowHeight", String(state.campaignRowHeight));
      removeUiPreference("campaignRowHeights");
      $("#campaignRowHeight").value = String(state.campaignRowHeight);
      $("#campaignRowHeightValue").textContent = `${state.campaignRowHeight}px`;
    }
    renderPaid();
  } else {
    const view = state.tableViews[context.node.id];
    view.columns = new Set(context.draft);
    const preferences = storedObject("portalTableColumns");
    preferences[context.node.id] = [...view.columns];
    saveUiPreferenceRaw("portalTableColumns", JSON.stringify(preferences));
    const config = context.node._tableConfig;
    table(context.node, config.cols, config.rows, config.empty);
  }
  closeColumnDrawer();
  toast("Table layout saved");
}

function setupColumnDrawer() {
  applyTableAppearance();
  $("#columnDrawerClose").addEventListener("click", closeColumnDrawer);
  $("#columnDrawerCancel").addEventListener("click", closeColumnDrawer);
  $("#columnDrawerSave").addEventListener("click", saveColumnDrawer);
  $("#columnDrawer").addEventListener("click", (ev) => {
    if (ev.target.id === "columnDrawer") closeColumnDrawer();
  });
  $("#columnDrawerSearch").addEventListener("input", renderColumnDrawerFields);
  $("#columnDrawerFields").addEventListener("change", (ev) => {
    const input = ev.target.closest('input[type="checkbox"]');
    if (!input || !columnDrawerContext) return;
    if (input.checked) columnDrawerContext.draft.add(input.value);
    else columnDrawerContext.draft.delete(input.value);
    renderColumnDrawerFields();
  });
  $$("[data-column-panel]").forEach((button) => button.addEventListener("click", () => selectColumnDrawerTab(button.dataset.columnPanel)));
  $$("[data-density]").forEach((button) => button.addEventListener("click", () => {
    if (!columnDrawerContext) return;
    columnDrawerContext.preference.density = button.dataset.density;
    $$("[data-density]").forEach((item) => {
      const selected = item === button;
      item.classList.toggle("on", selected);
      item.setAttribute("aria-pressed", String(selected));
    });
  }));
  $("#columnDrawerAll").addEventListener("click", () => {
    if (!columnDrawerContext) return;
    columnDrawerContext.draft = new Set(columnDrawerContext.columns.map((column) => column.id));
    renderColumnDrawerFields();
  });
  $("#columnDrawerEssentials").addEventListener("click", () => {
    if (!columnDrawerContext) return;
    const essential = columnDrawerContext.type === "paid"
      ? PAID_ESSENTIAL_COLUMNS : columnDrawerContext.columns.slice(0, Math.min(6, columnDrawerContext.columns.length)).map((column) => column.id);
    columnDrawerContext.draft = new Set(essential.filter((id) => columnDrawerContext.columns.some((column) => column.id === id)));
    renderColumnDrawerFields();
  });
  $("#columnRestoreDefaults").addEventListener("click", (ev) => {
    if (!columnDrawerContext) return;
    columnDrawerContext.draft = new Set(columnDrawerContext.defaults);
    ev.currentTarget.classList.add("queued");
    renderColumnDrawerFields();
  });
  $("#columnResetWidths").addEventListener("click", (ev) => {
    if (!columnDrawerContext) return;
    columnDrawerContext.resetWidths = true;
    ev.currentTarget.classList.add("queued");
  });
  $("#columnResetHeights").addEventListener("click", (ev) => {
    if (!columnDrawerContext) return;
    columnDrawerContext.resetHeights = true;
    ev.currentTarget.classList.add("queued");
  });
  document.addEventListener("keydown", (ev) => {
    const drawer = $("#columnDrawer");
    if (drawer.hidden) return;
    if (ev.key === "Escape") {
      closeColumnDrawer();
      return;
    }
    const tabs = $$('[data-column-panel]');
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(ev.key) && tabs.includes(document.activeElement)) {
      ev.preventDefault();
      const current = tabs.indexOf(document.activeElement);
      const next = ev.key === "Home" ? 0 : ev.key === "End" ? tabs.length - 1
        : (current + (ev.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
      tabs[next].focus();
      selectColumnDrawerTab(tabs[next].dataset.columnPanel);
      return;
    }
    if (ev.key !== "Tab") return;
    const focusable = $$('button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])', drawer)
      .filter((element) => !element.closest('[hidden]'));
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault(); last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault(); first.focus();
    }
  });
}

function setupStandardTableToolbar(node, columns, rows, visibleRows) {
  const wrap = node.closest(".tablewrap");
  if (!wrap) return;
  let toolbar = $(`#${node.id}Tools`);
  if (!toolbar) {
    toolbar = document.createElement("div");
    toolbar.id = `${node.id}Tools`;
    toolbar.className = "table-toolbar";
    toolbar.innerHTML = `
      <label class="table-search"><span class="sr-only">Search this table</span>
        <input type="search" placeholder="Search this table…" autocomplete="off">
      </label>
      <div class="table-column-picker">
        <button type="button" class="table-columns-toggle" aria-haspopup="dialog" aria-expanded="false" aria-controls="columnDrawer">${ICONS.columns}<span>Modify columns</span></button>
      </div>
      <button type="button" class="table-refresh" title="Refresh this tab">${ICONS.refresh}<span>Refresh</span></button>
      <span class="table-result-count" aria-live="polite"></span>`;
    wrap.before(toolbar);
    $(".table-search input", toolbar).addEventListener("input", (ev) => {
      state.tableViews[node.id].query = ev.target.value;
      const config = node._tableConfig;
      table(node, config.cols, config.rows, config.empty);
    });
    $(".table-columns-toggle", toolbar).addEventListener("click", (ev) => {
      openColumnDrawer({ type: "standard", node, columns: node._tableConfig.cols.map((column, index) => ({ ...column, id: tableColumnId(column, index) })), opener: ev.currentTarget });
    });
    $(".table-refresh", toolbar).addEventListener("click", async (ev) => {
      setButtonBusy(ev.currentTarget, true);
      try {
        const refreshed = await refreshPortal(false);
        if (refreshed) toast("View refreshed from saved portal data");
      }
      catch (err) { toast(`Could not refresh: ${err.message}`); }
      finally { setButtonBusy(ev.currentTarget, false); }
    });
  }
  const view = state.tableViews[node.id];
  const input = $(".table-search input", toolbar);
  if (document.activeElement !== input) input.value = view.query;
  $(".table-result-count", toolbar).textContent = view.query
    ? `${nf.format(visibleRows.length)} of ${nf.format(rows.length)} rows`
    : `${nf.format(rows.length)} rows`;
}

function table(node, cols, rows, empty = "No data in this range") {
  const allColumns = cols.map((column, index) => ({ ...column, id: tableColumnId(column, index) }));
  let prepared = allColumns;
  let visibleRows = rows;
  if (node.id !== "tblCampaigns") {
    const preferences = storedObject("portalTableColumns");
    let view = state.tableViews[node.id];
    if (!view) {
      const saved = Array.isArray(preferences[node.id]) ? preferences[node.id] : [];
      const valid = new Set(allColumns.map((column) => column.id));
      const selected = saved.filter((id) => valid.has(id));
      view = state.tableViews[node.id] = {
        query: "", columns: new Set(selected.length ? selected : [...valid]),
      };
    }
    prepared = allColumns.filter((column) => view.columns.has(column.id));
    const query = view.query.trim().toLowerCase();
    if (query) visibleRows = rows.filter((row) =>
      allColumns.some((column) => searchableText(column, row).toLowerCase().includes(query)));
    node._tableConfig = { cols, rows, empty };
    setupStandardTableToolbar(node, allColumns, rows, visibleRows);
  }
  if (!visibleRows.length) {
    node.innerHTML = `<tbody><tr><td class="empty">${rows.length ? "No rows match your search." : empty}</td></tr></tbody>`;
    return;
  }
  const colgroup = prepared.map((c) => `<col data-column="${esc(c.id)}">`).join("");
  const head = prepared.map((c) => `<th class="${c.num ? "num" : ""}" data-column="${esc(c.id)}">${esc(c.label)}</th>`).join("");
  const body = visibleRows.map((r) =>
    `<tr>${prepared.map((c) => `<td class="${c.num ? "num" : c.wide ? "name" : ""}" data-column="${esc(c.id)}">${c.get(r)}</td>`).join("")}</tr>`
  ).join("");
  node.innerHTML = `<colgroup>${colgroup}</colgroup><thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
  $$('tbody td', node).forEach((cell) => { cell.title = cell.textContent.trim(); });
  if (node.id !== "tblCampaigns") setupStandardTableSizing(node, prepared, visibleRows);
}

/* -------------------------------------------------------------- overview */
const TILES = [
  { k: "spend", label: "Ad spend", source: "paid", f: (v) => money(v), better: null },
  { k: "leads", label: "CRM leads", source: "crm", f: num, better: "higher" },
  { k: "cpl", label: "Cost per lead", source: "crm", f: (v) => money(v), better: "lower" },
  { k: "sales", label: "Sales", source: "crm", f: num, better: "higher" },
  { k: "cac", label: "Cost per sale", source: "crm", f: (v) => money(v), better: "lower" },
  { k: "lead_to_sale", label: "Lead → sale", source: "crm", f: pct, better: "higher" },
  { k: "clicks", label: "Paid clicks", source: "paid", f: num, better: "higher" },
  { k: "sessions", label: "GA4 sessions", source: "web", f: num, better: "higher" },
  { k: "organic_clicks", label: "Organic clicks", source: "search", f: num, better: "higher" },
  { k: "revenue", label: "CRM revenue", source: "crm", f: (v) => money(v, true), better: "higher" },
  { k: "roas", label: "CRM ROAS", source: "crm", f: (v) => v == null ? "—" : `${dec(v)}×`, better: "higher" },
];

async function loadOverview() {
  const ov = await api(`/api/overview?${qs()}`);
  state.overview = ov;
  const crmCoverage = ov.coverage?.crm;
  const crmAvailable = ov.availability?.crm !== false;
  const coverageLabel = !crmAvailable
    ? "CRM data unavailable"
    : crmCoverage && !crmCoverage.complete
      ? `CRM records · ${crmCoverage.days} of ${crmCoverage.total_days} days`
      : "Spend and CRM outcomes";
  const coverageNode = $("#overviewCoverage");
  coverageNode.textContent = coverageLabel;
  coverageNode.classList.toggle("is-partial", !crmAvailable || Boolean(crmCoverage && !crmCoverage.complete));
  const sourceNames = { paid: "paid campaign", crm: "CRM", web: "GA4", search: "Search Console" };
  const renderTile = (t) => {
    const v = ov.current[t.k];
    const d = ov.delta[t.k];
    const missing = ov.availability?.[t.source] === false;
    const coverage = ov.coverage?.[t.source];
    const partial = coverage && coverage.days > 0 && !coverage.complete;
    let cls = "", arrow = "";
    if (d != null && Math.abs(d) >= 0.1) {
      arrow = d > 0 ? "▲" : "▼";
      // Spend has no good direction — colouring it would editorialise.
      if (t.better) cls = (t.better === "higher" ? d > 0 : d < 0) ? "up" : "down";
    }
    const currencyBlocked = ov.availability?.paid && ov.current.spend == null
      && ["spend", "cpl", "cac", "roas"].includes(t.k);
    const revenueMappingMissing = crmAvailable && v == null && ["revenue", "roas"].includes(t.k)
      ? t.k === "revenue" ? "No revenue mapped in CRM" : "Needs CRM revenue mapping"
      : "";
    const dtxt = currencyBlocked
      ? `Financial totals unavailable: verify paid-data currency in the Data quality card`
      : missing && t.source !== "crm"
      ? `No ${sourceNames[t.source]} data in this range`
      : revenueMappingMissing
      ? revenueMappingMissing
      : partial && t.source !== "crm" ? `${coverage.days} of ${coverage.total_days} days available`
      : d == null ? "" : `${arrow} ${dec(Math.abs(d))}% vs prev period`;
    return `<div class="tile"><div class="k">${t.label}</div>
      <div class="v">${t.f(v)}</div>${dtxt ? `<div class="d ${cls}">${dtxt}</div>` : ""}</div>`;
  };
  const primaryMetrics = TILES.slice(0, 5);
  const additionalMetrics = TILES.slice(5);
  $("#tilesPrimary").innerHTML = primaryMetrics.map(renderTile).join("");
  $("#tilesSecondary").innerHTML = additionalMetrics.map(renderTile).join("");
  $("#additionalMetricCount").textContent = String(additionalMetrics.length);

  const ts = await api(`/api/timeseries?${qs()}`);
  const S = ts.series;
  const spendSeries = Object.entries(PAID_PLATFORM_META).map(([source, platform]) => ({
    key: `spend_${source}`, label: platform.label, color: platform.color,
    values: S[`spend_${source}`] || ts.days.map(() => null),
  }));
  legend($("#lgSpend"), spendSeries);
  lineChart($("#chSpend"), ts.days, spendSeries, (v, c) => money(v, c || v >= 1000));

  const leadSeries = [
    { key: "crm_leads", label: "Leads", color: cssVar("--series-1") },
    { key: "crm_sales", label: "Sales", color: cssVar("--series-2") },
  ].map((s) => ({ ...s, values: S[s.key] || ts.days.map(() => null) }));
  legend($("#lgLeads"), leadSeries);
  lineChart($("#chLeads"), ts.days, leadSeries, (v) => num(v));

  const chans = await api(`/api/channels?${qs()}`);
  const label = (r) => {
    const nice = {
      google_ads: "Google Ads", meta_ads: "Meta Ads", linkedin_ads: "LinkedIn Ads",
      tiktok_ads: "TikTok Ads", organic_search: "Organic search",
      direct: "Direct", referral: "Referral", social_organic: "Organic social", email: "Email",
    }[r.channel] || r.channel;
    const color = PAID_PLATFORM_META[r.channel]?.color
      || (r.channel === "google_ads" ? cssVar("--series-1")
        : r.channel === "meta_ads" ? cssVar("--series-2") : cssVar("--series-3"));
    return `<span class="chip"><i style="background:${color}"></i>${esc(nice)}</span>`;
  };
  table($("#tblChannels"), [
    { label: "Channel", get: label },
    { label: "Spend", num: true, get: (r) => money(r.spend) },
    { label: "Clicks", num: true, get: (r) => num(r.clicks) },
    { label: "Sessions", num: true, get: (r) => num(r.sessions) },
    { label: "Leads", num: true, get: (r) => r.crm_available === false ? "—" : num(r.leads) },
    { label: "Qualified", num: true, get: (r) => r.crm_available === false ? "—" : num(r.qualified) },
    { label: "Sales", num: true, get: (r) => r.crm_available === false ? "—" : num(r.sales) },
    { label: "CPL", num: true, get: (r) => money(r.cpl) },
    { label: "CAC", num: true, get: (r) => money(r.cac) },
    { label: "Lead→sale", num: true, get: (r) => pct(r.lead_to_sale) },
    { label: "Revenue", num: true, get: (r) => r.revenue_available === false ? "—" : money(r.revenue, true) },
    { label: "ROAS", num: true, get: (r) => r.revenue_available === false ? "—" : r.spend ? `${dec(r.revenue / r.spend)}×` : "—" },
  ], chans);
}

/* ------------------------------------------------------------------ paid */
function campaignFunnel(name = "") {
  const value = name.toLowerCase();
  if (value.includes("supply") || value.includes("procurement")) return "supply";
  if (value.includes("demand") || value.includes("sale")) return "demand";
  return "";
}

function googleAccountKey(row) {
  return String(row.account_id || row.account || "").trim();
}

function renderGoogleAccountOptions() {
  const toggle = $("#googleAdsAccountToggle");
  const labelNode = $("#googleAdsAccountLabel");
  const menu = $("#googleAdsAccountMenu");
  if (!toggle || !labelNode || !menu) return;
  const accounts = new Map();
  state.campaignRows.filter((row) => row.source === "google_ads").forEach((row) => {
    const key = googleAccountKey(row);
    if (!key || accounts.has(key)) return;
    const name = String(row.account || "").trim();
    accounts.set(key, name && name !== key ? `${name} · ${key}` : key);
  });
  const choices = [...accounts.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  if (state.campaignGoogleAccount && !accounts.has(state.campaignGoogleAccount)) state.campaignGoogleAccount = "";
  labelNode.textContent = accounts.get(state.campaignGoogleAccount) || "All accounts";
  menu.innerHTML = [["", "All accounts"], ...choices].map(([key, label]) =>
    `<button type="button" role="option" aria-selected="${key === state.campaignGoogleAccount}" data-google-account="${esc(key)}">${esc(label)}</button>`).join("");
  toggle.disabled = state.campaignSrc === "meta_ads" || !choices.length;
  toggle.title = toggle.disabled
    ? (state.campaignSrc === "meta_ads" ? "Select Google Ads to choose an account" : "No Google Ads accounts in this date range")
    : "Filter Google Ads campaigns by account";
}

function closeGoogleAccountMenu() {
  const toggle = $("#googleAdsAccountToggle");
  const menu = $("#googleAdsAccountMenu");
  if (!toggle || !menu) return;
  menu.hidden = true;
  toggle.setAttribute("aria-expanded", "false");
}

function selectGoogleAccount(value) {
  state.campaignGoogleAccount = value;
  closeGoogleAccountMenu();
  if (state.campaignGoogleAccount && state.campaignSrc !== "google_ads") {
    const google = $("[data-src=\"google_ads\"]");
    $$("[data-src]").forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-pressed", "false"); });
    google.classList.add("on");
    google.setAttribute("aria-pressed", "true");
    state.campaignSrc = "google_ads";
    loadPaid();
    if (refreshMetricCampaigns) refreshMetricCampaigns();
    return;
  }
  renderPaid();
}

const PAID_COLUMNS = [
  {
    id: "campaign", label: "Campaign", width: 360, wide: true, get: (r) => {
      const color = PAID_PLATFORM_META[r.source]?.color || cssVar("--series-3");
      return `<span class="chip campaign-name"><i style="background:${color}"></i><span class="chip-label">${esc(r.campaign || "(unnamed)")}</span></span>`;
    },
  },
  {
    id: "platform", label: "Platform", width: 130, get: (r) =>
      esc(PAID_PLATFORM_META[r.source]?.label || r.source || "—"),
  },
  { id: "account_id", label: "Account ID", width: 145, get: (r) => esc(r.account_id || "—") },
  { id: "account", label: "Account", width: 170, get: (r) => esc(r.account || "—") },
  { id: "campaign_id", label: "Campaign ID", width: 145, get: (r) => esc(r.campaign_id || "—") },
  { id: "campaign_status", label: "Status", width: 105, get: (r) => esc(r.campaign_status || "—") },
  { id: "adset", label: "Ad group / set", width: 180, get: (r) => esc(r.adset || "—") },
  { id: "adset_id", label: "Ad group / set ID", width: 155, get: (r) => esc(r.adset_id || "—") },
  { id: "ad", label: "Ad", width: 180, get: (r) => esc(r.ad || "—") },
  { id: "ad_id", label: "Ad ID", width: 145, get: (r) => esc(r.ad_id || "—") },
  {
    id: "funnel", label: "Funnel", width: 100, get: (r) => {
      const funnel = campaignFunnel(r.campaign);
      return funnel
        ? `<span class="campaign-tag ${funnel}">${funnel === "demand" ? "Demand" : "Supply"}</span>`
        : `<span class="campaign-tag other">Other</span>`;
    },
  },
  { id: "type", label: "Type", width: 140, get: (r) => esc(r.channel || "—") },
  { id: "spend", label: "Spend", width: 130, num: true, get: (r) => moneyInCurrency(r.spend, r.currency) },
  { id: "currency", label: "Currency", width: 90, get: (r) => esc(r.currency || "Unknown") },
  { id: "impressions", label: "Impr.", width: 100, num: true, get: (r) => num(r.impressions) },
  { id: "reach", label: "Reach", width: 100, num: true, get: (r) => num(r.reach) },
  { id: "frequency", label: "Frequency", width: 90, num: true, get: (r) => dec(r.frequency) },
  { id: "clicks", label: "Clicks", width: 90, num: true, get: (r) => num(r.clicks) },
  { id: "ctr", label: "CTR", width: 75, num: true, get: (r) => pct(r.ctr) },
  { id: "cpc", label: "CPC", width: 100, num: true, get: (r) => moneyInCurrency(r.cpc, r.currency) },
  {
    id: "cpm", label: "CPM", width: 85, num: true,
    get: (r) => moneyInCurrency(r.impressions ? (Number(r.spend) / Number(r.impressions)) * 1000 : null, r.currency),
  },
  { id: "conversions", label: "Conv.", width: 90, num: true, get: (r) => dec(r.conversions) },
  { id: "all_conversions", label: "All conv.", width: 95, num: true, get: (r) => dec(r.all_conversions) },
  {
    id: "cvr", label: "Conv. rate", width: 100, num: true,
    get: (r) => pct(r.clicks ? (Number(r.conversions) / Number(r.clicks)) * 100 : null),
  },
  { id: "conversion_value", label: "Conv. value", width: 135, num: true, get: (r) => moneyInCurrency(r.conversion_value, r.currency) },
  {
    id: "platform_roas", label: "Platform ROAS", width: 120, num: true,
    get: (r) => Number(r.spend) > 0 ? `${dec(Number(r.conversion_value) / Number(r.spend))}×` : "—",
  },
  { id: "daily_budget", label: "Daily budget", width: 135, num: true, get: (r) => moneyInCurrency(r.daily_budget, r.currency) },
  { id: "bidding_strategy", label: "Bid strategy", width: 170, get: (r) => esc(r.bidding_strategy || "—") },
  { id: "impression_share", label: "Search impr. share", width: 140, num: true, get: (r) => pct(r.impression_share) },
  { id: "budget_lost_impression_share", label: "Lost IS (budget)", width: 130, num: true, get: (r) => pct(r.budget_lost_impression_share) },
  { id: "rank_lost_impression_share", label: "Lost IS (rank)", width: 125, num: true, get: (r) => pct(r.rank_lost_impression_share) },
  { id: "keyword", label: "Keyword", width: 170, get: (r) => esc(r.keyword || "—") },
  { id: "keyword_match_type", label: "Match type", width: 110, get: (r) => esc(r.keyword_match_type || "—") },
  { id: "search_term", label: "Search term", width: 190, get: (r) => esc(r.search_term || "—") },
  { id: "quality_score", label: "Quality score", width: 105, num: true, get: (r) => dec(r.quality_score) },
  { id: "device", label: "Device", width: 105, get: (r) => esc(r.device || "—") },
  { id: "country", label: "Country", width: 105, get: (r) => esc(r.country || "—") },
  { id: "age", label: "Age", width: 90, get: (r) => esc(r.age || "—") },
  { id: "gender", label: "Gender", width: 90, get: (r) => esc(r.gender || "—") },
  { id: "publisher_platform", label: "Publisher", width: 120, get: (r) => esc(r.publisher_platform || "—") },
  { id: "placement", label: "Placement", width: 140, get: (r) => esc(r.placement || "—") },
  { id: "video_views", label: "Video views", width: 105, num: true, get: (r) => num(r.video_views) },
  { id: "video_completion_rate", label: "Video completion", width: 130, num: true, get: (r) => pct(r.video_completion_rate) },
  { id: "crm_leads", label: "CRM leads", width: 100, num: true,
    get: (r) => r.crm_leads_source ? num(r.crm_leads) : "—" },
  { id: "crm_sales", label: "CRM won", width: 90, num: true,
    get: (r) => r.crm_leads_source ? num(r.crm_sales) : "—" },
  { id: "crm_revenue", label: "CRM revenue", width: 140, num: true, get: (r) => moneyInCurrency(r.crm_revenue, r.crm_revenue_currency) },
  { id: "cpl", label: "CPL", width: 105, num: true, get: (r) => moneyInCurrency(r.cpl, r.currency) },
  { id: "cac", label: "CAC", width: 105, num: true, get: (r) => moneyInCurrency(r.cac, r.currency) },
  { id: "cpa", label: "CPA", width: 105, num: true, get: (r) => moneyInCurrency(r.cpa, r.currency) },
];

const METRIC_FIELDS = [
  ["spend", "Spend"], ["impressions", "Impressions"], ["clicks", "Clicks"],
  ["conversions", "Platform conversions"], ["crm_leads", "CRM leads"],
  ["crm_sales", "CRM won"], ["crm_revenue", "CRM revenue"],
  ["conversion_value", "Conversion value"],
  ["daily_budget", "Daily budget"],
  ["period_budget", "Period budget"],
  ["budgeted_spend", "Spend on budgeted days"],
];

function metricValue(metric, row) {
  const numerator = Number(row[metric.numerator] || 0);
  const denominator = Number(row[metric.denominator] || 0);
  return denominator ? numerator / denominator : null;
}

function metricFormatter(format, value, currency = state.currency) {
  if (format === "currency") return moneyInCurrency(value, currency);
  if (format === "percent") return pct(value == null ? null : value * 100);
  return dec(value);
}

function loadCustomMetrics() {
  try { state.customMetrics = JSON.parse(localStorage.getItem("customMetrics") || "[]"); }
  catch (_) { state.customMetrics = []; }
  const reserved = new Set(PAID_COLUMNS.map((column) => column.label.trim().toLowerCase()));
  const seen = new Set();
  const cleaned = state.customMetrics.filter((metric) => {
    const name = String(metric?.name || "").trim().toLowerCase();
    if (!name || reserved.has(name) || seen.has(name)) return false;
    seen.add(name);
    return true;
  });
  if (cleaned.length !== state.customMetrics.length) {
    state.customMetrics = cleaned;
    saveCustomMetrics();
  }
  state.customMetrics.forEach((metric) => {
    if (!metric.id || PAID_COLUMNS.some((column) => column.id === metric.id)) return;
    PAID_COLUMNS.push({
      id: metric.id, label: metric.name, width: 110, num: true,
      get: (row) => metricFormatter(metric.format, metricValue(metric, row)),
    });
  });
}

function saveCustomMetrics() {
  saveUiPreferenceRaw("customMetrics", JSON.stringify(state.customMetrics));
}

function loadCampaignColumnPreference() {
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem("campaignColumns") || "[]"); } catch (_) {}
  const valid = new Set(PAID_COLUMNS.map((c) => c.id));
  state.campaignColumns = new Set(saved.filter((id) => valid.has(id)));
  if (!state.campaignColumns.size) PAID_COLUMNS.forEach((c) => state.campaignColumns.add(c.id));
  try { state.campaignWidths = JSON.parse(localStorage.getItem("campaignWidths") || "{}"); }
  catch (_) { state.campaignWidths = {}; }
  try { state.campaignRowHeights = JSON.parse(localStorage.getItem("campaignRowHeights") || "{}"); }
  catch (_) { state.campaignRowHeights = {}; }
  const savedHeight = Number(localStorage.getItem("campaignRowHeight"));
  state.campaignRowHeight = savedHeight >= 32 && savedHeight <= 72 ? savedHeight : 38;
}

function campaignRowKey(row) {
  return `${row.source || ""}:${row.campaign_id || row.campaign || ""}`;
}

function setupCampaignTableSizing(columns, rows) {
  const node = $("#tblCampaigns");
  if (!node.tHead) return;
  const widthFor = (column) => Math.max(64, Math.min(720,
    Number(state.campaignWidths[column.id]) || column.width || 100));
  const applyWidths = () => {
    let total = 0;
    columns.forEach((column) => {
      const width = widthFor(column);
      total += width;
      const col = node.querySelector(`col[data-column="${column.id}"]`);
      if (col) col.style.width = `${width}px`;
    });
    node.style.width = `${total}px`;
  };
  node.style.setProperty("--campaign-row-height", `${state.campaignRowHeight}px`);
  applyWidths();

  $$('th[data-column]', node).forEach((th) => {
    const column = columns.find((item) => item.id === th.dataset.column);
    if (!column) return;
    const handle = document.createElement("span");
    handle.className = "column-resizer";
    handle.title = `Resize ${column.label} column`;
    th.appendChild(handle);
    handle.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      handle.setPointerCapture(ev.pointerId);
      document.body.classList.add("resizing-column");
      const startX = ev.clientX;
      const startWidth = widthFor(column);
      const move = (moveEv) => {
        state.campaignWidths[column.id] = Math.max(64, Math.min(720,
          startWidth + moveEv.clientX - startX));
        applyWidths();
      };
      const stop = () => {
        document.body.classList.remove("resizing-column");
        saveUiPreferenceRaw("campaignWidths", JSON.stringify(state.campaignWidths));
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", stop);
        handle.removeEventListener("pointercancel", stop);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", stop);
      handle.addEventListener("pointercancel", stop);
    });
    handle.addEventListener("dblclick", (ev) => {
      ev.preventDefault();
      const cells = $$(`td[data-column="${column.id}"]`, node);
      const contentWidth = Math.max(th.scrollWidth, ...cells.map((cell) => cell.scrollWidth));
      state.campaignWidths[column.id] = Math.max(64, Math.min(720, contentWidth + 18));
      applyWidths();
      saveUiPreferenceRaw("campaignWidths", JSON.stringify(state.campaignWidths));
      toast(`${column.label} column auto-fitted`);
    });
  });

  $$('tbody tr', node).forEach((tr, index) => {
    const key = campaignRowKey(rows[index] || {});
    const saved = Number(state.campaignRowHeights[key]);
    if (saved >= 28 && saved <= 160) tr.style.setProperty("--campaign-row-height", `${saved}px`);
    $$('td', tr).forEach((cell) => {
      const handle = document.createElement("span");
      handle.className = "row-resizer";
      handle.title = "Resize this row";
      cell.appendChild(handle);
      handle.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        handle.setPointerCapture(ev.pointerId);
        document.body.classList.add("resizing-row");
        const startY = ev.clientY;
        const startHeight = tr.getBoundingClientRect().height;
        const move = (moveEv) => {
          const height = Math.max(28, Math.min(160, startHeight + moveEv.clientY - startY));
          state.campaignRowHeights[key] = Math.round(height);
          tr.style.setProperty("--campaign-row-height", `${height}px`);
        };
        const stop = () => {
          document.body.classList.remove("resizing-row");
          saveUiPreferenceRaw("campaignRowHeights", JSON.stringify(state.campaignRowHeights));
          handle.removeEventListener("pointermove", move);
          handle.removeEventListener("pointerup", stop);
          handle.removeEventListener("pointercancel", stop);
        };
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", stop);
        handle.addEventListener("pointercancel", stop);
      });
      handle.addEventListener("dblclick", (ev) => {
        ev.preventDefault();
        delete state.campaignRowHeights[key];
        tr.style.removeProperty("--campaign-row-height");
        saveUiPreferenceRaw("campaignRowHeights", JSON.stringify(state.campaignRowHeights));
      });
    });
  });
}

function renderColumnMenu() {
  if (columnDrawerContext?.type === "paid") {
    columnDrawerContext.columns = PAID_COLUMNS;
    renderColumnDrawerFields();
  }
}

async function fetchAllCampaigns(source = "") {
  const range = qs();
  const selectedSource = source ? `&source=${encodeURIComponent(source)}` : "";
  const rows = [];
  while (true) {
    const page = await api(`/api/campaigns?${range}${selectedSource}&limit=1000&offset=${rows.length}`);
    if (!Array.isArray(page)) throw new Error("Campaign report returned an invalid page.");
    rows.push(...page);
    if (page.length < 1000) return rows;
  }
}

function renderPaid() {
  const query = state.campaignQuery.trim().toLowerCase();
  const rows = state.campaignRows.filter((r) => {
    const matchesQuery = !query || (r.campaign || "").toLowerCase().includes(query);
    const matchesFunnel = !state.campaignFunnel
      || campaignFunnel(r.campaign) === state.campaignFunnel;
    const matchesGoogleAccount = !state.campaignGoogleAccount
      || (r.source === "google_ads" && googleAccountKey(r) === state.campaignGoogleAccount);
    return matchesQuery && matchesFunnel && matchesGoogleAccount;
  });
  renderGoogleAccountOptions();
  $("#campaignCount").textContent = `${nf.format(rows.length)} campaign${rows.length === 1 ? "" : "s"}`;
  const columns = PAID_COLUMNS.filter((column) => state.campaignColumns.has(column.id));
  table($("#tblCampaigns"), columns, rows, query || state.campaignFunnel
    ? "No campaigns match these filters."
    : "No paid data yet — connect Google Ads or Meta Ads.");
  setupCampaignTableSizing(columns, rows);
}

async function loadPaid(notify = false) {
  const requestId = (state.paidRequestId || 0) + 1;
  state.paidRequestId = requestId;
  const source = state.campaignSrc;
  const start = state.start, end = state.end;
  const button = $("#refreshPaid");
  setButtonBusy(button, true);
  try {
    const rows = await fetchAllCampaigns(source);
    if (requestId !== state.paidRequestId || source !== state.campaignSrc
        || start !== state.start || end !== state.end) return;
    state.campaignRows = rows;
    renderPaid();
    if (notify) toast("Campaign data refreshed");
  } catch (err) {
    if (notify) toast(`Could not refresh campaigns: ${err.message}`);
    else throw err;
  } finally {
    setButtonBusy(button, false);
  }
}

/* ------------------------------------------------------------------- ga4 */
async function loadWeb() {
  const rows = await api(`/api/web?${qs()}&dim=${state.webDim}`);
  table($("#tblWeb"), [
    { label: state.webDim, wide: true, get: (r) => esc(r.key || "(not set)") },
    { label: "Sessions", num: true, get: (r) => num(r.sessions) },
    { label: "Users", num: true, get: (r) => num(r.users) },
    { label: "Engaged", num: true, get: (r) => num(r.engaged_sessions) },
    { label: "Engagement", num: true, get: (r) => pct(r.engagement_rate) },
    { label: "Conversions", num: true, get: (r) => dec(r.conversions) },
  ], rows, "No GA4 data yet — connect Google Analytics.");
}

/* ---------------------------------------------------------------- search */
async function loadSearch() {
  const [rows, ahrefs] = await Promise.all([
    api(`/api/search?${qs()}&dim=${state.searchDim}`),
    api(`/api/ahrefs?end=${encodeURIComponent(state.end)}`),
  ]);
  const ahrefsKpis = $("#ahrefsKpis");
  const ahrefsConnection = state.connections.find((connection) => connection.provider === "ahrefs");
  const ahrefsHealthy = Boolean(ahrefsConnection?.enabled && ahrefsConnection.ready && ahrefsConnection.last_status === "ok");
  const ahrefsReconnect = $("#ahrefsReconnect");
  if (ahrefsReconnect) {
    ahrefsReconnect.hidden = currentUser?.role !== "admin" || ahrefsHealthy;
    ahrefsReconnect.textContent = ahrefsConnection ? "Reconnect Ahrefs" : "Connect Ahrefs";
    ahrefsReconnect.dataset.connectionId = ahrefsConnection?.id || "";
  }
  if (ahrefs.available) {
    $("#ahrefsStatus").textContent = ahrefsHealthy ? "Connected" : "Stale snapshot";
    $("#ahrefsStatus").className = `status-pill ${ahrefsHealthy ? "current" : "delayed"}`;
    $("#ahrefsContext").textContent = `${ahrefs.target} · India database · snapshot ${displayDate(ahrefs.date)}`;
    ahrefsKpis.innerHTML = [
      ["Domain rating", dec(ahrefs.domain_rating)], ["Organic keywords", num(ahrefs.organic_keywords)],
      ["Top 3 keywords", num(ahrefs.top3_keywords)], ["Est. organic traffic", num(ahrefs.organic_traffic)],
      ["Live backlinks", num(ahrefs.live_backlinks)], ["Referring domains", num(ahrefs.live_refdomains)],
    ].map(([label, value]) => `<div class="tile"><div class="k">${label}</div><div class="v">${value}</div></div>`).join("");
  } else {
    $("#ahrefsStatus").textContent = "Setup needed";
    $("#ahrefsStatus").className = "status-pill delayed";
    ahrefsKpis.innerHTML = `<div class="empty compact">Connect Ahrefs to load SEO metrics. You will need an Ahrefs API key.</div>`;
  }
  table($("#tblSearch"), [
    { label: state.searchDim, wide: true, get: (r) => esc(r.key || "—") },
    { label: "Clicks", num: true, get: (r) => num(r.clicks) },
    { label: "Impressions", num: true, get: (r) => num(r.impressions) },
    { label: "CTR", num: true, get: (r) => pct(r.ctr) },
    { label: "Avg position", num: true, get: (r) => dec(r.position) },
  ], rows, "No Search Console data yet — connect Search Console.");
}

/* ------------------------------------------------------------------- crm */
async function loadCrm() {
  const d = await api(`/api/crm?${qs()}`);

  const renderFunnels = (funnels, emptyMessage) => (funnels || []).map((f) => {
    const top = Math.max(1, ...f.stages.map((s) => s.count || 0));
    const rows = f.stages.map((s, i) => {
      const w = Math.max(0.5, Math.min(100, (s.count / top) * 100));
      return `<div class="frow">
        <div class="lbl">${esc(s.stage)}</div>
        <div><div class="bar" style="width:${w}%;background:var(--ord-${i + 1})"></div></div>
        <div class="val"><b>${num(s.count)}</b> · ${pct(s.pct_of_leads)}</div>
      </div>`;
    }).join("");
    const note = f.invalid_stage_order
      ? `<p class="sub funnel-warning">${num(f.source_rows_out_of_order || 0)} CRM source group(s) have stages out of sequence. These are the original counts. Do not use adjacent-stage conversion rates until the source qualification and stage rules are corrected.</p>`
      : `<p class="sub">Share of leads created in this range that reached each stage.</p>`;
    return `<div class="card"><h2>${esc(f.label)}</h2>
      ${note}
      <div class="funnel">${rows}</div></div>`;
  }).join("") || `<div class="card"><div class="empty">${emptyMessage}</div></div>`;

  // Buying and selling are different funnels with different stages, so they
  // get one panel each rather than being averaged into a single misleading set.
  $("#funnels").innerHTML = renderFunnels(d.funnels, "No CRM data in this range.");
  $("#channelFunnels").innerHTML = renderFunnels(
    d.channel_funnels,
    "No paid-platform CRM leads in this range.",
  );

  const conv = (r) => pct(r.leads ? (r.sales / r.leads) * 100 : null);
  const campaignFreshness = d.campaign_data_to
    ? `Campaign lead totals in this report are available through ${d.campaign_data_to}.`
    : "Campaign lead data is not available yet.";
  $("#campaignLeadFreshness").textContent = `${campaignFreshness} ${d.campaign_feed_policy || ""}`.trim();
  $("#funnelFreshness").textContent = d.funnel_data_to
    ? `Detailed Superleap stage data through ${d.funnel_data_to}.`
    : "Detailed funnel-stage data is not available yet.";
  table($("#tblCampaignLeads"), [
    { label: "Lead side", get: (r) => esc(({ demand: "Demand", supply: "Supply", buy: "Demand", sell: "Supply" }[r.lead_type] || r.lead_type || "—")) },
    { label: "Channel", get: (r) => esc(r.channel || "—") },
    { label: "Source", get: (r) => esc(r.source || "Unknown source") },
    { label: "Campaigns", num: true, get: (r) => num(r.campaigns) },
    { label: "Leads", num: true, get: (r) => num(r.leads) },
    { label: "Updated", get: (r) => esc(r.data_to || "—") },
  ], d.campaign_leads || [], "No campaign lead data in this range.");
  table($("#tblSource"), [
    { label: "Source", get: (r) => esc(r.key || "—") },
    { label: "Channel", get: (r) => esc(r.channel || "—") },
    { label: "Leads", num: true, get: (r) => num(r.leads) },
    { label: "Won", num: true, get: (r) => num(r.sales) },
    { label: "Conv.", num: true, get: conv },
  ], d.by_source || [], "No CRM data yet.");

  table($("#tblCity"), [
    { label: "City", get: (r) => esc(r.key || "—") },
    { label: "Leads", num: true, get: (r) => num(r.leads) },
    { label: "Won", num: true, get: (r) => num(r.sales) },
    { label: "Conv.", num: true, get: conv },
  ], d.by_city || [], "No CRM data yet.");

  table($("#tblType"), [
    { label: "Lead type", get: (r) => esc(({ buy: "Buy (demand)", sell: "Sell (supply)" }[r.key] || r.key || "—")) },
    { label: "Leads", num: true, get: (r) => num(r.leads) },
    { label: "Won", num: true, get: (r) => num(r.sales) },
    { label: "Conv.", num: true, get: conv },
  ], d.by_type || [], "No CRM data yet.");
}

/* ----------------------------------------------------------- connections */
let PROVIDERS = [];
let REDIRECT_URI = "";
let modalReturnFocus = null;

const CATEGORY_LABEL = {
  ads: "Advertising", analytics: "Analytics", search: "Search",
  crm: "CRM", commerce: "Commerce", other: "Anything else",
};

function modal(title, bodyHtml, footHtml) {
  modalReturnFocus = document.activeElement;
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = bodyHtml;
  $("#modalFoot").innerHTML = footHtml;
  $("#modal").hidden = false;
  requestAnimationFrame(() => {
    const first = $("#modalBody input, #modalBody select, #modalBody textarea, #modalBody button, #modalFoot button, #modalClose");
    first?.focus();
  });
}
function trapDialogFocus(ev) {
  if (ev.key !== "Tab" || $("#modal").hidden) return;
  const focusable = $$('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])', $("#modal"))
    .filter((node) => node.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
}
const closeModal = () => {
  $("#modal").hidden = true;
  if (modalReturnFocus?.isConnected) modalReturnFocus.focus();
  modalReturnFocus = null;
};

function esc(v) {
  return String(v ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

const FIELD_GUIDANCE = {
  client_id: "Open the platform's developer console, create a web application, and copy its Client ID or App ID.",
  client_secret: "Open the same developer application and copy its Client Secret or App Secret. Keep it private.",
  developer_token: "In Google Ads Manager, open Tools → API Center and copy the developer token. Confirm the token has production reporting access.",
  customer_ids: "In Google Ads, copy the 10-digit customer ID shown at the top of the account. You can add several IDs separated by commas.",
  login_customer_id: "Use the 10-digit ID of the Google Ads manager account that owns or manages the selected customer accounts.",
  account_ids: "Open the platform's account settings and copy the advertising or business account ID. Multiple IDs can be comma-separated.",
  property_ids: "In GA4, open Admin → Property settings and copy the numeric Property ID. Do not use the G- measurement ID.",
  site_urls: "In Search Console, copy the property exactly as displayed, such as sc-domain:example.com or https://example.com/.",
  advertiser_ids: "In TikTok Ads Manager, open account settings and copy the numeric Advertiser ID.",
  access_token: "Create or generate the access token in the platform's developer or business settings. The portal stores it only in the private local database.",
  api_key: "Open the platform's developer or API settings and create an API key with read access.",
  url: "Use the HTTPS reporting endpoint in the platform's API documentation. The server administrator must add its public host to PORTAL_CONNECTOR_HOSTS before testing or syncing.",
  field_map: "Map each portal column on the left to the matching field name returned by the source on the right.",
  path: "Choose a CSV file or folder on this computer, or paste a published Google Sheets CSV link.",
};

const PROVIDER_FIELD_GUIDANCE = {
  google_ads: {
    client_id: "Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs → your Web application → Client ID.",
    client_secret: "Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs → your Web application → Client secret.",
    developer_token: "Google Ads → Tools → Setup → API Center → Developer token.",
    customer_ids: "Google Ads → account selector at the top → copy the 10-digit Customer ID.",
    login_customer_id: "Google Ads manager account → account selector at the top → copy the manager Customer ID.",
  },
  ga4: {
    client_id: "Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs → your Web application → Client ID.",
    client_secret: "Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs → your Web application → Client secret.",
    property_ids: "Google Analytics → Admin → Property details → Property ID. Use the number, not the G- measurement ID.",
  },
  gsc: {
    client_id: "Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs → your Web application → Client ID.",
    client_secret: "Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs → your Web application → Client secret.",
    site_urls: "Search Console → property selector in the top-left → copy each exact property name, including sc-domain: or the URL protocol and trailing slash. The owner must grant the Google account used for Connect access to each property.",
  },
  meta_ads: {
    client_id: "Meta for Developers → My Apps → your app → App settings → Basic → App ID.",
    client_secret: "Meta for Developers → My Apps → your app → App settings → Basic → App secret.",
    account_ids: "Meta Ads Manager → account selector → copy the account ID, including the act_ prefix.",
    access_token: "Meta Business Settings → Users → System users → select a user → Generate new token.",
  },
  linkedin_ads: {
    client_id: "LinkedIn Developers → My apps → your app → Auth → Client ID.",
    client_secret: "LinkedIn Developers → My apps → your app → Auth → Primary client secret.",
    account_ids: "Campaign Manager → account selector → copy the account number from the account page URL.",
  },
  tiktok_ads: {
    client_id: "TikTok for Business Developers → My Apps → your app → App ID.",
    client_secret: "TikTok for Business Developers → My Apps → your app → App secret.",
    advertiser_ids: "TikTok Ads Manager → Tools → Account setup → Basic information → Advertiser ID.",
    access_token: "TikTok for Business Developers → My Apps → your app → Authorization → Access token.",
  },
  ahrefs: {
    api_key: "Ahrefs → Account settings → API keys → Create API key. Workspace owner or admin access is required; API usage depends on your Ahrefs plan.",
    target: "Ahrefs Site Explorer → enter the domain you want to report, such as vutto.in.",
    country: "Use the two-letter country code used in Ahrefs, such as in, us, or gb.",
  },
  rest: {
    url: "Platform developer documentation → API reference → HTTPS reporting endpoint. Ask the portal administrator to add this public host to PORTAL_CONNECTOR_HOSTS.",
    token: "Platform account → Developer, Integrations, or API settings → create an API key or access token.",
    field_map: "Open one API response, then match each portal field to its exact JSON field name.",
  },
  csv: {
    path: "Local file: copy its full path. Google Sheets: File → Share → Publish to web → CSV, then paste the published link.",
    field_map: "Use the exact column headings from the first row of the file or Google Sheet.",
  },
  superleap_crm: {
    api_key: "Superleap Workspace → Settings → API Access → create an API key. Paste the complete key, with or without a leading Bearer prefix. This connector only reads CRM data; the portal stores the key as a secret.",
    object_slug: "Keep the default opportunity object for Vutto demand and supply leads.",
    initial_sync_from: "Optional. Choose the oldest opportunity creation date to import. Leave blank to backfill all available opportunity history.",
    revenue_field: "Only enter a field that represents actual recognized CRM revenue. Do not use asking price or an estimated vehicle resale price.",
  },
};

function fieldGuidance(provider, field) {
  return PROVIDER_FIELD_GUIDANCE[provider.id]?.[field.key] || field.help || FIELD_GUIDANCE[field.key] || "Find this value in the platform's account, developer, or API settings.";
}

/** Build the setup form straight from the provider's declared fields. */
function fieldsForm(provider, conn, defaults = {}) {
  const values = Object.fromEntries(provider.fields.filter((f) => f.default !== undefined && f.default !== "")
    .map((f) => [f.key, f.default]));
  Object.assign(values, defaults, conn?.config || {});
  const setSecrets = new Set(conn?.secrets_set || []);
  const rows = provider.fields.map((f) => {
    const fieldId = `field-${provider.id}-${f.key}`;
    const val = f.secret ? "" : (values[f.key] ?? "");
    const filled = f.secret && setSecrets.has(f.key);
    const input = f.kind === "text" && (f.key === "field_map" || f.key === "extra_params")
      ? `<textarea id="${fieldId}" name="${f.key}" placeholder="${esc(f.placeholder)}">${esc(val)}</textarea>`
      : `<input id="${fieldId}" name="${f.key}" type="${f.secret ? "password" : (f.kind === "date" ? "date" : "text")}"
           placeholder="${esc(filled ? "•••••• (saved — leave blank to keep)" : f.placeholder)}"
           value="${esc(val)}" autocomplete="off">`;
    const guidance = fieldGuidance(provider, f);
    return `<div class="field">
      <div class="field-label-row"><label for="${fieldId}">${esc(f.label)}${f.required ? "" : ' <span class="opt">(optional)</span>'}</label>
      <span class="field-help-wrap"><button type="button" class="field-info" aria-label="Where to find ${esc(f.label)}" aria-describedby="help-${fieldId}"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg></button><span class="field-help" role="tooltip" id="help-${fieldId}">${esc(guidance)}</span></span></div>
      ${input}
    </div>`;
  }).join("");

  const oauthNote = provider.auth === "oauth2"
    ? `<div class="callout">This platform uses OAuth. Register this address as the
         redirect / callback URL in the app you create there, then save and press
         <b>Connect</b>.<code>${esc(REDIRECT_URI)}</code></div>`
    : "";

  const gscNote = provider.id === "gsc"
    ? `<div class="callout"><b>Search Console property access</b><p>For each property, its owner opens Search Console → Settings → Users and permissions → Add user, enters the Google account you use for <b>Connect</b>, and grants Full or Restricted access. Enter the exact property shown in Search Console, then press <b>Test connection</b> after authorising. <a href="https://support.google.com/webmasters/answer/7687615" target="_blank" rel="noopener noreferrer">Google's access instructions</a>.</p></div>`
    : "";

  return `${oauthNote}${gscNote}
    <div class="field">
      <label for="connection-name">Name this connection</label>
      <p class="hint">How it appears in the portal — useful when you have several
        accounts on the same platform.</p>
      <input id="connection-name" name="__name" value="${esc(conn?.name || defaults.__name || provider.label)}" autocomplete="off">
    </div>
    ${rows}`;
}

function readForm() {
  const values = {};
  let name = "";
  $$("#modalBody input, #modalBody textarea").forEach((el) => {
    if (el.name === "__name") { name = el.value.trim(); return; }
    if (el.value !== "") values[el.name] = el.value.trim();
  });
  return { name, values };
}

function validateConnectionForm(provider, conn, name, values) {
  $$(".form-error-summary, .field-error", $("#modalBody")).forEach((node) => node.remove());
  $$("[aria-invalid='true']", $("#modalBody")).forEach((node) => {
    node.removeAttribute("aria-invalid");
    node.removeAttribute("aria-describedby");
  });
  const missing = [];
  if (!name) missing.push({ id: "connection-name", label: "Enter a connection name." });
  const savedSecrets = new Set(conn?.secrets_set || []);
  provider.fields.filter((field) => field.required).forEach((field) => {
    if (!values[field.key] && !(field.secret && savedSecrets.has(field.key))) {
      missing.push({ id: `field-${provider.id}-${field.key}`, label: `Enter ${field.label.toLowerCase()}.` });
    }
  });
  if (!missing.length) return true;

  const summary = document.createElement("div");
  summary.className = "form-error-summary";
  summary.setAttribute("role", "alert");
  summary.tabIndex = -1;
  summary.innerHTML = `<b>Complete ${missing.length === 1 ? "this field" : "these fields"}</b><ul>${missing.map((item) => `<li><a href="#${esc(item.id)}">${esc(item.label)}</a></li>`).join("")}</ul>`;
  $("#modalBody").prepend(summary);
  missing.forEach((item) => {
    const input = $(`#${item.id}`);
    if (!input) return;
    const error = document.createElement("p");
    error.className = "field-error";
    error.id = `${item.id}-error`;
    error.textContent = item.label;
    input.setAttribute("aria-invalid", "true");
    input.setAttribute("aria-describedby", error.id);
    input.insertAdjacentElement("afterend", error);
  });
  summary.focus();
  return false;
}

function openPicker() {
  const byCat = {};
  PROVIDERS.filter((p) => p.syncable !== false).forEach((p) => (byCat[p.category] ||= []).push(p));
  const body = Object.entries(byCat).map(([cat, list]) => `
    <p class="hint" style="margin:0 0 8px;font-weight:600;text-transform:uppercase;
       letter-spacing:.04em">${esc(CATEGORY_LABEL[cat] || cat)}</p>
    <div class="pickers" style="margin-bottom:18px">
      ${list.map((p) => `<button class="picker" data-pick="${p.id}">
          <b>${esc(p.label)}</b><span>${esc(p.summary || "")}</span></button>`).join("")}
    </div>`).join("");
  modal("Choose a platform", body, `<button id="cancelPick">Cancel</button>`);
  $("#cancelPick").addEventListener("click", closeModal);
  $$("[data-pick]").forEach((b) =>
    b.addEventListener("click", () => openForm(b.dataset.pick)));
}

function openForm(providerId, conn, defaults = {}) {
  const provider = PROVIDERS.find((p) => p.id === providerId);
  if (!provider) return;
  const canTest = provider.syncable !== false && (provider.auth !== "oauth2" || (conn && !conn.missing?.includes("Authorisation")));
  modal(
    conn ? `Edit ${provider.label}` : (defaults.__formTitle || `Connect ${provider.label}`),
    fieldsForm(provider, conn, defaults),
    `<button id="cancelForm">Cancel</button>${canTest ? '<button id="testForm">Test connection</button>' : ""}<button id="saveForm" class="primary">${provider.auth === "oauth2" && !conn ? "Save & continue" : "Save"}</button>`,
  );
  $("#cancelForm").addEventListener("click", closeModal);
  $("#testForm")?.addEventListener("click", async () => {
    const { name, values } = readForm();
    if (!validateConnectionForm(provider, conn, name, values)) return;
    const button = $("#testForm");
    button.disabled = true; button.textContent = "Testing…";
    try {
      const result = await api("/api/connections/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: providerId, connection_id: conn?.id, name, values }),
      });
      let note = $("#connectionTestResult");
      if (!note) { note = document.createElement("div"); note.id = "connectionTestResult"; $("#modalBody").prepend(note); }
      note.className = "connection-test-result ok"; note.textContent = result.message;
    } catch (err) {
      let note = $("#connectionTestResult");
      if (!note) { note = document.createElement("div"); note.id = "connectionTestResult"; $("#modalBody").prepend(note); }
      note.className = "connection-test-result error"; note.textContent = `Could not verify: ${err.message}`;
    } finally { button.disabled = false; button.textContent = "Test connection"; }
  });
  $("#saveForm").addEventListener("click", async () => {
    const { name, values } = readForm();
    if (!validateConnectionForm(provider, conn, name, values)) return;
    $("#saveForm").disabled = true;
    try {
      if (conn) {
        await api(`/api/connections/${conn.id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, values }),
        });
      } else {
        await api("/api/connections", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider: providerId, name, values }),
        });
      }
      closeModal();
      toast(conn ? "Connection updated" : "Connection added");
      await loadConnections();
      if (providerId === "ahrefs") {
        toast("Ahrefs connection saved. Choose Sync now in Connections to load the latest snapshot.");
        switchTab("connections");
      }
      if (providerId === "gmail" && currentUser?.role === "admin") {
        await loadGmailSettings();
        selectSettingsPanel("apps");
      }
    } catch (err) {
      toast(`Could not save: ${err.message}`, "error");
      $("#saveForm").disabled = false;
    }
  });
}

function sourceRouteId(name = activeTab) {
  const match = String(name || "").match(/^source-(\d+)$/);
  return match ? Number(match[1]) : null;
}

function renderSourceNavigation(list = state.connections) {
  list = list.filter((connection) => connection.syncable !== false);
  state.connections = list;
  const nav = $("#sourceTabs");
  if (!nav) return;
  const connected = list.filter((c) => c.enabled && c.ready);
  const superleap = connected.filter((c) => c.provider === "superleap_crm" || (c.provider === "csv" && c.name.startsWith("Superleap CRM")));
  const standalone = connected.filter((c) => c.provider !== "superleap_crm" && !(c.provider === "csv" && c.name.startsWith("Superleap CRM")));
  const optionCount = standalone.length + (superleap.length ? 1 : 0);
  nav.hidden = !optionCount || currentUser?.role === "viewer";
  const select = $("#sourceSelect"), menu = $("#sourceMenu");
  if (!select || !menu) return;
  const healthFor = (connection) => !connection.enabled ? "off" : connection.last_status === "ok" ? "ok"
    : ["error", "empty"].includes(connection.last_status) ? "err" : "idle";
  const superleapHealth = superleap.some((c) => c.last_status === "error") ? "err"
    : superleap.every((c) => c.last_status === "ok") ? "ok"
      : superleap.some((c) => c.last_status === "empty") ? "err" : "idle";
  const items = [
    ...(superleap.length ? [{ route: "crm", name: "Superleap CRM", id: "superleap", health: superleapHealth, description: `${superleap.length} CRM connection${superleap.length === 1 ? "" : "s"}` }] : []),
    ...standalone.map((connection) => ({ route: `source-${connection.id}`, name: connection.name, id: connection.provider, health: healthFor(connection), description: connectorCategory(connection.provider) })),
  ];
  const active = items.find((item) => item.route === activeTab);
  $(".source-select-label", select).textContent = active ? active.name : `Connected sources (${optionCount})`;
  select.classList.toggle("has-selection", Boolean(active));
  menu.innerHTML = items.map((item) => {
    const status = item.health === "ok" ? "Sync succeeded" : item.health === "err" ? "Sync needs attention" : "Sync pending";
    return `<button type="button" role="menuitem" tabindex="-1" data-source-route="${esc(item.route)}" class="source-menu-item ${item.route === activeTab ? "on" : ""}" ${item.route === activeTab ? 'aria-current="page"' : ""}>
    ${sourceLogo(item.id, item.name)}<span class="source-menu-copy"><b>${esc(item.name)}</b><small>${esc(item.description)} · ${status}</small></span><i class="dot ${item.health}" aria-hidden="true"></i>
  </button>`;
  }).join("");
  renderMetricSourceLogos();
}

function sourceLogo(provider, name) {
  const key = provider === "meta_ads" ? "facebook" : provider === "google_sheets" ? "googlesheets"
    : provider === "google_ads" ? "google_ads" : provider === "ga4" ? "googleanalytics4"
      : provider === "gsc" ? "searchconsole" : provider;
  return connectorIcon({ id: key || "file", name: name || "Source" }).replace("connector-logo", "connector-logo source-logo");
}

function renderMetricSourceLogos() {
  const node = $("#metricSourceLogos");
  if (!node) return;
  const sourceSpecs = [
    { provider: "google_ads", id: "google_ads", label: "Google Ads", use: "Spend & clicks" },
    { provider: "meta_ads", id: "facebook", label: "Meta Ads", use: "Spend & conversions" },
    { provider: "superleap", id: "superleap", label: "Superleap CRM", use: "Leads & sales" },
  ];
  const visible = sourceSpecs.filter((spec) => spec.provider === "superleap"
    ? state.connections.some((connection) => (connection.provider === "superleap_crm" || connection.name.startsWith("Superleap CRM")) && connection.enabled && connection.ready)
    : state.connections.some((connection) => connection.provider === spec.provider && connection.enabled && connection.ready));
  node.innerHTML = visible.map((spec) => `<span class="metric-source-chip">${sourceLogo(spec.id, spec.label)}<span><b>${spec.label}</b><small>${spec.use}</small></span></span>`).join("");
  node.hidden = !visible.length;
}

function sourceColumnLabel(name) {
  const aliases = { crm_sales: "CRM won", campaign_id: "Campaign ID", campaign_name: "Campaign",
    lead_type: "Funnel", created_date: "Created", ingested_at: "Imported" };
  return aliases[name] || String(name).replaceAll("_", " ").replace(/\b\w/g, (x) => x.toUpperCase());
}

function sourceValue(column, value) {
  if (value == null || value === "") return "—";
  if (["spend", "revenue", "conversion_value", "daily_budget"].includes(column)) return money(Number(value));
  if (["frequency", "position", "impression_share", "budget_lost_impression_share",
    "rank_lost_impression_share", "video_completion_rate"].includes(column)) return dec(Number(value));
  return typeof value === "number" ? nf.format(value) : esc(String(value));
}

async function loadSource(offset = 0, search = "") {
  const connectionId = sourceRouteId();
  if (!connectionId) return;
  const pageSize = 50;
  const d = await api(`/api/source?connection_id=${connectionId}&${qs()}&limit=${pageSize}&offset=${offset}&q=${encodeURIComponent(search)}`);
  const c = d.connection;
  state.activeSourceId = connectionId;
  state.connections = state.connections.map((item) => item.id === c.id ? c : item);
  renderSourceNavigation();
  const status = !c.enabled ? "Turned off" : c.last_status === "ok" ? "Healthy"
    : c.last_status === "error" ? "Needs attention" : c.last_status === "empty" ? "No rows returned"
      : c.ready ? "Ready" : "Setup incomplete";
  const statusClass = c.last_status === "ok" ? "current" : ["error", "empty"].includes(c.last_status) ? "delayed" : "empty";
  const metricPriority = ["spend", "leads", "won", "conversions", "sessions", "clicks",
    "impressions", "total_users", "revenue", "organic_traffic", "organic_keywords"];
  const metricNames = metricPriority.filter((key) => Object.hasOwn(d.totals, key)).slice(0, 5);
  const metricCards = [
    `<div><span>Rows in this range</span><b>${num(d.row_count)}</b><small>${d.shown < d.row_count ? `${num(d.shown)} shown below` : "All rows shown"}</small></div>`,
    ...metricNames.map((key) => `<div><span>${esc(sourceColumnLabel(key))}</span><b>${sourceValue(key, d.totals[key])}</b><small>${displayDate(d.range.start)} – ${displayDate(d.range.end)}</small></div>`),
  ].join("");
  $("#sourceView").innerHTML = `
    <div class="source-view-hero">
      <div class="source-view-mark">${esc((c.provider_label || "Source").split(/\s+/).slice(0, 2).map((x) => x[0]).join("").toUpperCase())}</div>
      <div class="source-view-copy"><span>${esc(c.provider_label)}</span><h2>${esc(c.name)}</h2>
        <p>Imported records for the selected reporting period.</p></div>
      <span class="status-pill ${statusClass}">${esc(status)}</span>
      <div class="source-view-actions"><button id="sourceSync" class="primary" ${c.ready && c.enabled ? "" : "disabled"}>Sync now</button><button id="sourceEdit">Edit connection</button><button id="sourceManage">All connections</button></div>
    </div>
    <div class="source-kpis">${metricCards}</div>
    <div class="card source-data-card"><div class="cardhead"><div><h2>Imported data</h2>
      <p class="sub">${num(d.row_count)} matching rows. Search and move through all imported records.</p></div>
      <label class="source-search"><span class="sr-only">Search imported data</span><input id="sourceSearch" type="search" value="${esc(search)}" placeholder="Search this source…"></label></div>
      <div class="tablewrap"><table id="tblConnectedSource"></table></div>
      <div class="source-pagination"><button id="sourcePrevious" ${d.offset <= 0 ? "disabled" : ""}>Previous</button><span>${d.row_count ? `${num(d.offset + 1)}–${num(Math.min(d.offset + d.shown, d.row_count))}` : "0"} of ${num(d.row_count)}</span><button id="sourceNext" ${d.offset + d.shown >= d.row_count ? "disabled" : ""}>Next</button></div></div>`;
  const columns = d.columns.map((column) => ({
    label: sourceColumnLabel(column.name),
    num: /INT|REAL|NUM|DEC|FLOAT|DOUBLE/i.test(column.type || ""),
    get: (row) => sourceValue(column.name, row[column.name]),
  }));
  table($("#tblConnectedSource"), columns, d.rows, "No imported rows in this date range.");
  let searchTimer;
  $("#sourceSearch").addEventListener("input", (ev) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadSource(0, ev.target.value), 300);
  });
  $("#sourcePrevious").addEventListener("click", () => loadSource(Math.max(0, d.offset - pageSize), search));
  $("#sourceNext").addEventListener("click", () => loadSource(d.offset + pageSize, search));
  $("#sourceSync").addEventListener("click", async () => {
    const button = $("#sourceSync"); setButtonBusy(button, true);
    try {
      const response = await api(`/api/connections/${connectionId}/sync`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ wait: true }) });
      const summary = syncSummary(response.results || []); toast(summary.message, summary.tone); await loadSource();
    } catch (err) { toast(`Could not sync this source: ${err.message}`, "error"); }
    finally { setButtonBusy(button, false); }
  });
  $("#sourceEdit").addEventListener("click", async () => {
    const detail = await api(`/api/connections/${connectionId}`); openForm(c.provider, detail);
  });
  $("#sourceManage").addEventListener("click", () => switchTab("connections"));
}

async function loadConnections() {
  const [list, runs] = await Promise.all([
    api("/api/connections"),
    api("/api/syncs?limit=30"),
  ]);
  if (!PROVIDERS.length) PROVIDERS = await api("/api/providers");
  if (!REDIRECT_URI) REDIRECT_URI = (await api("/api/oauth/redirect-uri")).redirect_uri;
  renderSourceNavigation(list);

  const syncableList = list.filter((c) => c.syncable !== false);
  $("#conns").innerHTML = syncableList.length ? syncableList.map((c) => {
    const cls = !c.enabled ? "off"
      : c.last_status === "ok" ? "ok"
        : ["error", "empty"].includes(c.last_status) ? "err"
          : c.ready ? "idle" : "off";
    const state = !c.enabled ? "Turned off"
      : !c.ready ? `Needs: ${c.missing.join(", ")}`
        : c.last_status === "never" ? "Ready — never synced"
          : c.last_status === "ok" ? "Healthy" : c.last_status === "empty" ? "No rows returned" : c.last_status;
    const when = c.last_success_at
      ? `Last success ${new Date(c.last_success_at).toLocaleString("en-GB")}`
      : "No successful sync yet";
    const issue = connectionIssue(c.last_message);
    const err = c.last_status === "error" && c.last_message
      ? `<div class="connection-error" role="alert"><strong>${esc(issue[0])}</strong><p>${esc(issue[1])}</p><details><summary>Technical detail</summary><code>${esc(c.last_message.slice(0, 700))}</code></details></div>`
      : c.last_status === "empty" ? `<div class="connection-error" role="status"><strong>No rows in the latest refresh</strong><p>Check the source and the sync date range. Previously imported data may still be stored.</p></div>` : "";
    const needsAuth = c.needs_oauth && c.missing.includes("Authorisation");
    return `<div class="conn">
      <h3><span class="dot ${cls}"></span>${esc(c.name)}
        <span class="cat">${esc(c.provider_label)}</span></h3>
      <div class="meta">
        <div>${esc(state)}</div><div>${esc(when)}</div>
        <div>${nf.format(c.rows_held)} rows stored</div>${err}
      </div>
      <div class="row">
        ${needsAuth ? `<button class="primary" data-auth="${c.id}">Connect</button>` : ""}
        ${c.syncable === false ? "" : `<button data-sync="${c.id}" ${c.ready && c.enabled ? "" : "disabled"}>Sync now</button>`}
        <button data-edit="${c.id}" data-provider="${c.provider}">Edit</button>
        <button data-toggle="${c.id}" data-on="${c.enabled ? 1 : 0}">${c.enabled ? "Turn off" : "Turn on"}</button>
        <button class="danger" data-del="${c.id}">Remove</button>
      </div>
    </div>`;
  }).join("") : `<div class="empty">No accounts connected yet. Press
      <b>Add a connection</b> to link a platform.</div>`;

  $$("[data-auth]").forEach((b) => b.addEventListener("click", () => {
    window.open(`/oauth/start?connection_id=${b.dataset.auth}`, "_blank");
    toast("Finish authorising in the new tab, then refresh this page");
  }));
  $$("[data-sync]").forEach((b) => b.addEventListener("click", async () => {
    setButtonBusy(b, true);
    try {
      const response = await api(`/api/connections/${b.dataset.sync}/sync`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wait: true }),
      });
      await loadConnections();
      const summary = syncSummary(response.results || []);
      toast(summary.message, summary.tone);
    } catch (err) {
      toast(`Could not sync this connection: ${err.message}`, "error");
      setButtonBusy(b, false);
    }
  }));
  $$("[data-edit]").forEach((b) => b.addEventListener("click", async () => {
    const conn = await api(`/api/connections/${b.dataset.edit}`);
    openForm(b.dataset.provider, conn);
  }));
  $$("[data-toggle]").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/connections/${b.dataset.toggle}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: b.dataset.on !== "1" }),
    });
    loadConnections();
  }));
  $$("[data-del]").forEach((b) => b.addEventListener("click", () => {
    const c = list.find((x) => String(x.id) === b.dataset.del);
    modal("Remove this connection?",
      `<p class="hint" style="font-size:13px">Removing <b>${esc(c.name)}</b> deletes its saved
       credentials. Its ${nf.format(c.rows_held)} stored rows can stay in the warehouse, or go
       with it. This does not revoke the app's access at the source platform; revoke that grant in the platform's account settings if needed.</p>`,
      `<button id="cancelDel">Cancel</button>
       <button id="delKeep">Remove, keep data</button>
       <button id="delAll" class="primary">Remove and delete data</button>`);
    $("#cancelDel").addEventListener("click", closeModal);
    const remove = async (drop) => {
      try {
        await api(`/api/connections/${c.id}/delete${drop ? "?drop_data=1" : ""}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        closeModal(); toast("Connection removed"); await loadConnections();
      } catch (error) { toast(`Could not remove connection: ${error.message}`, "error"); }
    };
    $("#delKeep").addEventListener("click", () => remove(false));
    $("#delAll").addEventListener("click", () => remove(true));
  }));

  table($("#tblSyncs"), [
    { label: "Platform", get: (r) => esc(r.label || "—") },
    {
      label: "Status", get: (r) => {
        const cls = r.status === "ok" ? "ok" : ["error", "empty"].includes(r.status) ? "err" : "idle";
        const label = r.status === "empty" ? "No rows" : r.status;
        return `<span class="chip"><i class="dot ${cls}"></i>${esc(label)}</span>`;
      },
    },
    { label: "Rows", num: true, get: (r) => num(r.rows) },
    { label: "Started", get: (r) => (r.started_at ? new Date(r.started_at).toLocaleString("en-GB") : "—") },
    { label: "Message", wide: true, get: (r) => esc((r.message || "").slice(0, 180)) },
  ], runs, "Nothing has run yet.");
}

/* --------------------------------------------------------------- data hub */
function getSavedViews() {
  try {
    const saved = JSON.parse(localStorage.getItem("savedPortalViews") || "[]");
    if (!Array.isArray(saved)) return [];
    const seen = new Set();
    return saved.filter((view) => {
      const name = String(view?.name || "").trim().toLowerCase();
      if (!name || seen.has(name)) return false;
      seen.add(name);
      return true;
    });
  }
  catch (_) { return []; }
}

function saveCurrentView() {
  const name = window.prompt("Name this view", `${pageInfoFor(activeTab)[0]} · ${displayDate(state.start)}`);
  if (!name?.trim()) return;
  const views = getSavedViews().filter((view) => view.name.toLocaleLowerCase() !== name.trim().toLocaleLowerCase());
  views.unshift({
    id: String(Date.now()), name: name.trim(), tab: activeTab, start: state.start, end: state.end,
    campaignSrc: state.campaignSrc, campaignFunnel: state.campaignFunnel,
    campaignQuery: state.campaignQuery, campaignColumns: [...state.campaignColumns],
    webDim: state.webDim, searchDim: state.searchDim,
  });
  saveUiPreferenceRaw("savedPortalViews", JSON.stringify(views.slice(0, 30)));
  renderSavedViews();
  toast("View saved");
}

function applySavedView(view) {
  const latest = latestReportingDate();
  state.end = view.end && view.end <= latest ? view.end : latest;
  state.start = view.start && view.start <= state.end
    ? view.start : shiftIso(state.end, -29);
  state.campaignSrc = view.campaignSrc || "";
  state.campaignFunnel = view.campaignFunnel || "";
  state.campaignQuery = view.campaignQuery || "";
  state.webDim = view.webDim || "channel";
  state.searchDim = view.searchDim || "query";
  if (view.campaignColumns?.length) state.campaignColumns = new Set(view.campaignColumns);
  $("#start").value = state.start; $("#end").value = state.end;
  $("#campaignSearch").value = state.campaignQuery;
  updateDateRangeLabel(); renderColumnMenu();
  switchTab(view.tab || "overview");
  toast(`Loaded ${view.name}`);
}

function renderSavedViews() {
  const node = $("#savedViews");
  if (!node) return;
  const views = getSavedViews();
  node.innerHTML = views.length ? views.map((view) => `<div class="saved-view">
    <button class="saved-view-open" data-view="${view.id}"><b>${esc(view.name)}</b><small>${esc(PAGE_INFO[view.tab]?.[0] || view.tab)} · ${esc(view.start)} → ${esc(view.end)}</small></button>
    <button class="icon-button saved-view-delete" data-delete-view="${view.id}" aria-label="Delete ${esc(view.name)}">×</button>
  </div>`).join("") : `<div class="empty compact">No saved views yet. Set up any report and press <b>Save view</b>.</div>`;
}

function csvCell(value) {
  let text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (typeof value === "string" && /^[=+\-@＝＋－＠]/.test(text)) text = "'\t" + text;
  return `"${text.replace(/"/g, '""')}"`;
}

function csvBlob(content) {
  // Put the UTF-8 marker first so spreadsheet apps decode ₹ and — correctly.
  const text = `sep=,\n${content}`.replace(/\n/g, "\r\n");
  return new Blob(["\uFEFF", text], { type: "text/csv;charset=utf-8" });
}

function exportActiveView() {
  const section = $(`#tab-${activeTab}`);
  const tables = $$('table', section).filter((tableNode) => tableNode.rows.length > 1);
  if (!tables.length) { toast("This page has no table data to export"); return; }
  const chunks = tables.map((tableNode) => {
    const title = tableNode.closest(".card")?.querySelector("h2")?.textContent || pageInfoFor(activeTab)[0];
    const rows = $$('tr', tableNode).map((row) => $$('th,td', row).map((cell) => csvCell(cell.innerText)).join(","));
    return `${csvCell(title)}\n${rows.join("\n")}`;
  });
  const blob = csvBlob(chunks.join("\n\n"));
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `marketing-${activeTab}-${state.start}-to-${state.end}.csv`;
  link.click(); URL.revokeObjectURL(link.href);
  toast("CSV export downloaded");
}

function buildInsights(overview) {
  const current = overview?.current || {};
  const delta = overview?.delta || {};
  const insights = [];
  const available = (value) => value != null && Number.isFinite(Number(value));
  const comparison = (value, higherIsBetter = true) => {
    if (!available(value)) return { tone: "neutral", text: "Previous-period comparison is unavailable because source coverage is incomplete." };
    const improved = higherIsBetter ? value >= 0 : value <= 0;
    return { tone: improved ? "good" : "warn", text: `${value >= 0 ? "Up" : "Down"} ${nf1.format(Math.abs(value))}% versus the previous comparable period.` };
  };
  if (available(current.leads)) {
    const trend = comparison(delta.leads, true);
    insights.push({ tone: trend.tone, source: "CRM", title: "Lead volume", value: num(current.leads), detail: trend.text,
      action: available(delta.leads) && delta.leads < -10 ? "Review campaigns with falling lead volume before increasing spend." : "Compare lead growth with spend and CPL before changing budgets." });
  }
  if (available(current.cpl)) {
    const trend = comparison(delta.cpl, false);
    insights.push({ tone: trend.tone, source: "CRM", title: "Cost per lead", value: money(current.cpl), detail: trend.text,
      action: available(delta.cpl) && delta.cpl > 10 ? "Check high-spend campaigns with weak lead delivery." : "Prioritise campaigns that maintain lead quality at this cost." });
  }
  if (available(current.cac)) {
    const trend = comparison(delta.cac, false);
    insights.push({ tone: trend.tone, source: "CRM", title: "Customer acquisition", value: money(current.cac), detail: `${num(current.sales || 0)} completed sales · ${trend.text}`,
      action: available(delta.cac) && delta.cac > 10 ? "Review the campaigns spending most without completed sales." : "Track sales volume with CAC to confirm efficient growth." });
  }
  if (available(current.lead_to_sale)) {
    const trend = comparison(delta.lead_to_sale, true);
    insights.push({ tone: trend.tone, source: "CRM", title: "Lead-to-sale rate", value: pct(current.lead_to_sale), detail: trend.text,
      action: available(delta.lead_to_sale) && delta.lead_to_sale < 0 ? "Inspect CRM stages where leads are dropping out." : "Use this rate to assess whether lead quality is holding." });
  }
  if (available(current.spend)) {
    const trend = comparison(delta.spend, true);
    insights.push({ tone: "neutral", source: "Paid media", title: "Advertising spend", value: money(current.spend), detail: trend.text,
      action: "Read spend together with leads, sales, CPL, and CAC; spend alone is not a performance result." });
  }
  if (!available(current.leads) && available(current.platform_conversions)) {
    const trend = comparison(delta.platform_conversions, true);
    insights.push({ tone: trend.tone, source: "Paid media", title: "Platform conversions", value: num(current.platform_conversions), detail: trend.text,
      action: "Validate platform conversions against CRM outcomes before treating them as completed sales." });
  }
  if (!available(current.leads) && available(current.clicks)) {
    const trend = comparison(delta.clicks, true);
    insights.push({ tone: trend.tone, source: "Paid media", title: "Ad clicks", value: num(current.clicks), detail: trend.text,
      action: "Compare click movement with CTR and CPC to separate demand changes from efficiency changes." });
  }
  if (!available(current.cpl) && available(current.cpc)) {
    const trend = comparison(delta.cpc, false);
    insights.push({ tone: trend.tone, source: "Paid media", title: "Cost per click", value: money(current.cpc), detail: trend.text,
      action: available(delta.cpc) && delta.cpc > 10 ? "Review expensive campaigns and search terms before raising budgets." : "Monitor whether efficient clicks are also producing conversions." });
  }
  if (available(current.ctr)) {
    const trend = comparison(delta.ctr, true);
    insights.push({ tone: trend.tone, source: "Paid media", title: "Click-through rate", value: pct(current.ctr), detail: trend.text,
      action: available(delta.ctr) && delta.ctr < 0 ? "Review targeting and creative relevance in campaigns losing engagement." : "Keep testing creative while watching conversion quality." });
  }
  if (!available(current.leads) && available(current.impressions)) {
    const trend = comparison(delta.impressions, true);
    insights.push({ tone: trend.tone, source: "Paid media", title: "Ad impressions", value: num(current.impressions), detail: trend.text,
      action: "Use reach and engagement together; more impressions only help when clicks and conversions follow." });
  }
  if (available(current.roas) && current.revenue) {
    const trend = comparison(delta.roas, true);
    insights.push({ tone: trend.tone, title: "Return on ad spend", value: `${nf1.format(current.roas)}×`, detail: trend.text,
      action: current.roas < 1 ? "Verify revenue mapping and review campaigns returning less value than spend." : "Confirm revenue coverage before using ROAS for budget decisions." });
  }
  return insights.slice(0, 6);
}

function renderPerformanceInsights(overview) {
  const insights = buildInsights(overview);
  const range = overview?.range;
  const compare = overview?.compare_range;
  $("#insightRange").textContent = range
    ? `Showing ${displayDate(range.start)} – ${displayDate(range.end)}${compare ? ` · compared with ${displayDate(compare.start)} – ${displayDate(compare.end)} when coverage is complete` : ""}`
    : "Current reporting range";
  $("#insightList").innerHTML = insights.length ? insights.map((item) => `<article class="insight ${item.tone}"><div class="insight-meta"><span class="insight-source">${esc(item.source || "Portal")}</span></div><div class="insight-head"><span>${esc(item.title)}</span><b>${esc(item.value)}</b></div><p>${esc(item.detail)}</p><small><strong>Next:</strong> ${esc(item.action)}</small></article>`).join("") : `<div class="empty compact">No performance metrics are available for this date range. Sync data or choose another range.</div>`;
}

async function refreshPerformanceInsights() {
  const button = $("#refreshInsights");
  button.disabled = true;
  button.classList.add("is-busy");
  $("#insightRange").textContent = "Refreshing insights from current data…";
  try {
    const overview = await api(`/api/overview?${qs()}`);
    state.overview = overview;
    renderPerformanceInsights(overview);
    toast("Performance insights refreshed");
  } catch (error) {
    $("#insightRange").textContent = "Insights could not be refreshed. Check the connection and try again.";
    toast(`Could not refresh insights: ${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.classList.remove("is-busy");
  }
}

function connectorCategory(id) {
  if (/(ads|adform|adroll|awin|criteo|dsp|dv360|facebook|linkedin|outbrain|pinterest|snapchat|taboola|tiktok|tradedesk|twitter)/.test(id)) return "Advertising";
  if (/(ahrefs|analytics|ga4|gsc|google_sheets|googlesheets|amplitude|clarity|fullstory|matomo|mixpanel|plausible|posthog|searchconsole|semrush|serpstat|similarweb)/.test(id)) return "Analytics & SEO";
  if (/(salesforce|hubspot|pipedrive|zoho|freshsales|close_com|crm|intercom|gong|outreach|salesloft)/.test(id)) return "CRM & sales";
  if (/(shopify|commerce|magento|amazon_sp|amazon_vendor|woocommerce|walmart|stripe|paypal|chargebee|recurly|square)/.test(id)) return "Commerce & payments";
  if (/(big_query|snowflake|redshift|mysql|postgresql|mongodb|s3|sftp|supabase|azure|databricks)/.test(id)) return "Warehouses & storage";
  if (/(mail|klaviyo|sendgrid|braze|iterable|customer_io|convertkit|omnisend|onesignal)/.test(id)) return "Email & engagement";
  return "Business tools";
}

const NATIVE_CONNECTORS = new Set(["ahrefs", "google_ads", "facebook", "googleanalytics4", "googlesheets", "searchconsole", "linkedin", "tiktok", "file", "superleap_crm"]);
const CONNECTOR_DISPLAY_NAMES = {
  facebook: "Meta Ads", googleanalytics4: "Google Analytics 4", googlesheets: "Google Sheets",
  searchconsole: "Google Search Console", linkedin: "LinkedIn Ads", tiktok: "TikTok Ads",
  file: "CSV file or export", superleap_crm: "Superleap CRM",
};
let connectorPageSize = 60;
const PORTAL_PROVIDER = {
  ahrefs: "ahrefs",
  google_ads: "google_ads", facebook: "meta_ads", googleanalytics4: "ga4",
  googlesheets: "google_sheets", searchconsole: "gsc", linkedin: "linkedin_ads", tiktok: "tiktok_ads", file: "csv",
  superleap_crm: "superleap_crm",
};
function connectorIcon(item) {
  const iconId = ({ facebook: "meta_ads", googleanalytics4: "ga4", googlesheets: "google_sheets",
    searchconsole: "gsc", linkedin: "linkedin_ads", tiktok: "tiktok_ads", file: "csv",
    superleap_crm: "superleap" })[item.id] || item.id;
  const marks = {
    superleap: ['superleap', '<path d="M17.8 5.2A8 8 0 1 0 18.6 17h-4.2a4.5 4.5 0 1 1 .2-9.8L12 9.8h8V2l-2.2 3.2Z"/>'],
    ahrefs: ['ahrefs', '<path d="M4 18.5 9.2 5h4.1l5.2 13.5h-3.8l-.9-2.5H8.5l-.9 2.5H4Zm5.5-5.4h3.4l-1.7-4.8-1.7 4.8Z"/>'],
    google_ads: ['google-ads', '<path fill="#4285F4" d="M5.8 17.8 13.4 4.7a2.3 2.3 0 0 1 4 2.3L9.8 20.1a2.3 2.3 0 0 1-4-2.3Z"/><path fill="#FBBC04" d="M13.4 4.7a2.3 2.3 0 0 1 4 0l4.3 7.4a2.3 2.3 0 0 1-4 2.3L13.4 7a2.3 2.3 0 0 1 0-2.3Z"/><circle cx="5.9" cy="18.9" r="2.3" fill="#34A853"/>'],
    meta_ads: ['meta', '<path d="M3.4 14.4c1.2-4.5 2.8-6.8 5-6.8 2.2 0 3.3 2.3 4.4 4.4 1-2.1 2.2-4.4 4.2-4.4 2.4 0 3.8 2.5 4.4 6.7.3 2.2-.7 3.9-2.6 3.9-2 0-3.7-2.5-6-5.8-2.3 3.3-4 5.8-6 5.8-1.8 0-2.8-1.5-2.4-3.8Z" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'],
    ga4: ['ga4', '<circle cx="17.8" cy="17.8" r="2.7"/><rect x="3" y="12" width="5.4" height="8.5" rx="2.7"/><rect x="10.4" y="3" width="5.4" height="17.5" rx="2.7"/>'],
    gsc: ['gsc', '<rect x="3" y="5" width="18" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M7 11h10M7 15h6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'],
    google_sheets: ['sheets', '<path d="M6 2h9l5 5v15H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z"/><path d="M8 11h8M8 15h8M11 9v9" fill="none" stroke="#fff" stroke-width="1.5"/>'],
    linkedin_ads: ['linkedin', '<rect x="2" y="2" width="20" height="20" rx="3"/><path d="M7 10v8M7 7v.1M11 18v-8h3v1.1c.6-.8 1.4-1.3 2.5-1.3 2.1 0 2.5 1.4 2.5 3.4V18" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>'],
    tiktok_ads: ['tiktok', '<path d="M14 3v10.4a4.2 4.2 0 1 1-3.8-4.2v3.2a1.1 1.1 0 1 0 .7 1V3h3.1c.4 2.1 1.7 3.4 4 3.6v3.1c-1.5 0-2.9-.4-4-1.3V3Z"/>'],
    csv: ['csv', '<path d="M6 2h9l5 5v15H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z"/><path d="M15 2v5h5M8 12h8M8 16h8" fill="none" stroke="currentColor" stroke-width="1.5"/>'],
  };
  const mark = marks[iconId];
  if (mark) return `<span class="connector-logo connector-logo-${mark[0]}" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false">${mark[1]}</svg></span>`;
  const hue = [...item.id].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 360;
  const initials = String(item.name || "Source").split(/\s+/).filter(Boolean).slice(0, 2).map((word) => word[0]).join("").toUpperCase();
  return `<span class="connector-logo" style="--logo-hue:${hue}" aria-hidden="true"><span>${esc(initials)}</span></span>`;
}

async function openConnectorSetup(item) {
  item = { ...item, name: CONNECTOR_DISPLAY_NAMES[item.id] || item.name };
  if (!PROVIDERS.length) PROVIDERS = await api("/api/providers");
  if (!REDIRECT_URI) REDIRECT_URI = (await api("/api/oauth/redirect-uri")).redirect_uri;
  const nativeProvider = PORTAL_PROVIDER[item.id];
  if (nativeProvider && PROVIDERS.some((provider) => provider.id === nativeProvider)) {
    const defaults = item.id === "ahrefs"
      ? { __name: "Ahrefs · vutto.in", target: "vutto.in", country: "in" }
      : { __name: item.name };
    openForm(nativeProvider, null, defaults);
    return;
  }
  const category = connectorCategory(item.id);
  const presets = category === "Advertising" ? {
    target_table: "paid_metrics",
    field_map: JSON.stringify({ date: "date", source: "source", account_id: "account_id", account_name: "account_name", campaign_id: "campaign_id", campaign_name: "campaign", impressions: "impressions", clicks: "clicks", spend: "spend", conversions: "conversions", conversion_value: "conversion_value", currency: "currency" }, null, 2),
  } : category === "Analytics & SEO" ? {
    target_table: "search_metrics",
    field_map: JSON.stringify({ date: "date", site_url: "site", query: "query", page: "page", country: "country", device: "device", clicks: "clicks", impressions: "impressions", position: "position" }, null, 2),
  } : {
    target_table: "crm_leads",
    field_map: JSON.stringify({ external_id: "id", created_date: "created_at", utm_source: "source", utm_medium: "medium", utm_campaign: "campaign", city: "city", stage: "status", revenue: "revenue" }, null, 2),
  };
  modal(`Connect ${item.name}`, `<div class="connection-choice-intro"><b>Choose how ${esc(item.name)} provides its data</b><p>This is a generic template, not a ready-made connector. You need the platform's API documentation and an approved HTTPS host, or a CSV export.</p></div><div class="connection-choices">
    <button id="chooseDirectApi" class="connection-choice"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M8 12h8M7 8v8M17 8v8M5 10H3v4h2M19 10h2v4h-2"/></svg><span><b>Generic HTTPS API</b><small>Enter the platform endpoint, authentication, and field mapping. An administrator must allow its host.</small></span></button>
    <button id="chooseFileImport" class="connection-choice"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 3h10l4 4v14H5V3Z"/><path d="M15 3v5h5M8 13h8M8 17h8"/></svg><span><b>CSV or Google Sheets</b><small>Use an exported file or a published Sheet when API access is unavailable.</small></span></button>
  </div>`, `<button id="cancelConnectorChoice">Cancel</button>`);
  $("#cancelConnectorChoice").addEventListener("click", closeModal);
  $("#chooseDirectApi").addEventListener("click", () => openForm("rest", null, {
    __name: item.name, __formTitle: `Connect ${item.name} directly`, source_name: item.id, ...presets,
  }));
  $("#chooseFileImport").addEventListener("click", () => openForm("csv", null, {
    __name: item.name, __formTitle: `Import ${item.name}`, source_name: item.id, ...presets,
  }));
}

function renderConnectorDirectory() {
  const seen = new Set();
  const connectors = (window.SOURCE_CONNECTORS || []).map((item) => ({ ...item, name: CONNECTOR_DISPLAY_NAMES[item.id] || item.name })).filter((item) => {
    const key = String(item.name || "").trim().toLocaleLowerCase();
    if (!key || ["all", "adobe2"].includes(item.id) || seen.has(key)) return false;
    seen.add(key); return true;
  }).sort((left, right) => Number(NATIVE_CONNECTORS.has(right.id)) - Number(NATIVE_CONNECTORS.has(left.id)) || left.name.localeCompare(right.name));
  const query = ($("#connectorSearch")?.value || "").trim().toLowerCase();
  const filtered = connectors.filter((item) => `${item.name} ${item.id}`.toLowerCase().includes(query));
  const visible = filtered.slice(0, connectorPageSize);
  const nativeCount = connectors.filter((item) => NATIVE_CONNECTORS.has(item.id)).length;
  $("#connectorTotal").textContent = nf.format(connectors.length);
  $("#connectorSummary").textContent = `${nf.format(nativeCount)} native connectors · ${nf.format(connectors.length - nativeCount)} API or file templates · ${nf.format(filtered.length)} matching`;
  $("#connectorGrid").innerHTML = visible.map((item) => {
    const native = NATIVE_CONNECTORS.has(item.id);
    return `<article class="connector-item">${connectorIcon(item)}<div><b>${esc(item.name)}</b><small>${connectorCategory(item.id)}</small></div><div class="connector-actions"><span class="connector-state ${native ? "native" : "universal"}">${native ? "Native connector" : "API or file template"}</span><button data-connect-id="${esc(item.id)}">${native ? "Connect" : "Set up"}</button></div></article>`;
  }).join("") + (filtered.length > visible.length ? `<button class="connector-more" data-more-connectors type="button">Show ${nf.format(Math.min(60, filtered.length - visible.length))} more</button>` : "") || `<div class="empty">No connectors match “${esc(query)}”.</div>`;
}

function renderCustomMetrics() {
  const node = $("#customMetrics");
  if (!node) return;
  node.innerHTML = state.customMetrics.length ? state.customMetrics.map((metric) => `<div class="metric-item"><span><b>${esc(metric.name)}</b><small>${esc(metric.numerator)} ÷ ${esc(metric.denominator)} · ${esc(metric.format)}</small></span><button class="metric-view" data-view-metric="${esc(metric.id)}">View column</button><button class="icon-button" data-delete-metric="${esc(metric.id)}" aria-label="Delete ${esc(metric.name)}">×</button></div>`).join("") : `<div class="empty compact">Your added columns will appear here.</div>`;
}

const AUTOMATION_TYPES = {
  data_sync: {
    label: "Scheduled data sync", icon: "↻",
    description: "Refresh connected marketing and CRM sources automatically.",
    actions: [["refresh", "Refresh all connected sources"], ["topup", "Refresh recent conversions only"]],
  },
  reports: {
    label: "Automatic reports", icon: "▤",
    description: "Generate and deliver scheduled CSV or Excel performance reports.",
    actions: [["email_csv", "Email a CSV report"], ["email_excel", "Email an Excel report"], ["save_local", "Prepare a local download"]], email: true, planned: true,
  },
  performance_alerts: {
    label: "Performance alerts", icon: "!",
    description: "Alert when CPL, CAC, CPC, spend, or conversions cross a limit.",
    actions: [["notify", "Show a portal notification"], ["email", "Send an email alert"]], threshold: true, email: true, planned: true,
  },
  budget_monitoring: {
    label: "Budget monitoring", icon: "₹",
    description: "Track pacing, forecast month-end spend, and flag budget risk.",
    actions: [["pacing", "Check planned versus actual spend"], ["forecast", "Forecast month-end spend"], ["email", "Email a budget warning"]], threshold: true, email: true, planned: true,
  },
  recommendations: {
    label: "Campaign recommendations", icon: "✦",
    description: "Find campaigns to scale, investigate, pause, or improve.",
    actions: [["efficiency", "Review efficiency opportunities"], ["scale", "Find campaigns ready to scale"], ["waste", "Find spend without outcomes"]], planned: true,
  },
  crm_followup: {
    label: "CRM follow-up automation", icon: "◎",
    description: "Find untouched leads and leads stuck in a funnel stage.",
    actions: [["uncontacted", "Find leads not contacted"], ["stalled", "Find leads stuck in a stage"], ["summary", "Send a funnel summary"]], crm: true, planned: true,
  },
  data_quality: {
    label: "Data-quality checks", icon: "✓",
    description: "Detect missing fields, duplicates, connector gaps, and total mismatches.",
    actions: [["missing", "Check missing campaign and source fields"], ["duplicates", "Check duplicate CRM leads"], ["connectors", "Check delayed or failed connectors"], ["totals", "Compare portal and platform totals"]], planned: true,
  },
  offline_conversions: {
    label: "Offline conversion preparation", icon: "⇧",
    description: "Prepare validated qualified-lead and sale files for ad platforms.",
    actions: [["google", "Prepare Google Ads conversion file"], ["meta", "Prepare Meta conversion file"], ["both", "Prepare both platform files"]], planned: true,
  },
  snapshots: {
    label: "Snapshots and backups", icon: "◫",
    description: "Save historical campaign snapshots and reporting backups.",
    actions: [["campaigns", "Save campaign performance snapshot"], ["full", "Back up the complete report"], ["sheet", "Prepare a Google Sheets export"]], retention: true, planned: true,
  },
  custom_rules: {
    label: "Custom automation rules", icon: "⌘",
    description: "Choose a metric, condition, threshold, schedule, and action.",
    actions: [["notify", "Show a portal notification"], ["email", "Send an email"], ["flag", "Flag matching campaigns"]], threshold: true, planned: true,
  },
};

function getAutomationRules() {
  if (currentUser && currentUser.role !== "admin") return [];
  try { return JSON.parse(localStorage.getItem("portalAutomationRules") || "[]"); }
  catch (_) { return []; }
}

async function persistAutomationRules(rules) {
  if (currentUser?.role !== "admin") throw new Error("Only an administrator can manage automation rules.");
  localStorage.setItem("portalAutomationRules", JSON.stringify(rules));
  await api("/api/automation/rules", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rules }) });
}

function renderAutomationTypeSummary() {
  const typeId = $("#automationType")?.value;
  const type = AUTOMATION_TYPES[typeId] || AUTOMATION_TYPES.data_sync;
  const schedulerPaused = typeId === "data_sync" && state.meta && !state.meta.scheduler_enabled;
  $("#automationChoiceIcon").textContent = type.icon;
  $("#automationChoiceTitle").textContent = type.label;
  const availability = type.planned ? " Planned feature. Execution service required."
    : schedulerPaused ? " Background schedule paused. Manual sync is available below."
      : " Available now.";
  $("#automationChoiceDescription").textContent = `${type.description}${availability}`;
  $("#createAutomation").textContent = type.planned ? "Review planned workflow" : "Configure automation";
}

function renderAutomationRules() {
  const node = $("#automationRuleList");
  if (!node) return;
  const rules = getAutomationRules();
  node.innerHTML = rules.length ? `
    <span class="compact-label">Saved workflows</span>
    ${rules.map((rule) => {
      const type = AUTOMATION_TYPES[rule.type] || AUTOMATION_TYPES.custom_rules;
      return `<div class="automation-rule">
        <span class="automation-rule-icon" aria-hidden="true">${type.icon}</span>
        <span><b>${esc(rule.name)}</b><small>${esc(rule.scheduleLabel)} · ${esc(rule.actionLabel)}</small></span>
        <button type="button" data-delete-automation="${esc(rule.id)}" aria-label="Delete ${esc(rule.name)}">×</button>
      </div>`;
    }).join("")}` : "";
}

function automationThresholdFields(type) {
  const config = AUTOMATION_TYPES[type];
  const metricOptions = ["Spend", "CPL", "CAC", "CPC", "CTR", "Conversions", "CRM leads", "CRM won"];
  if (config.threshold) return `
    <div class="automation-condition-grid">
      <label>Metric<select id="automationMetric">${metricOptions.map((metric) => `<option>${metric}</option>`).join("")}</select></label>
      <label>Condition<select id="automationCondition"><option value="above">Goes above</option><option value="below">Goes below</option><option value="changes">Changes by more than</option></select></label>
      <label>Threshold<input id="automationThreshold" type="number" min="0" step="0.01" value="5000"></label>
    </div>`;
  if (config.crm) return `<label>Follow up after<select id="automationLeadAge"><option value="2">2 hours</option><option value="6">6 hours</option><option value="24" selected>24 hours</option><option value="48">48 hours</option><option value="72">3 days</option></select></label>`;
  if (config.retention) return `<label>Keep snapshots for<select id="automationRetention"><option value="30">30 days</option><option value="90" selected>90 days</option><option value="180">6 months</option><option value="365">1 year</option></select></label>`;
  return "";
}

function openAutomationBuilder() {
  const typeId = $("#automationType").value;
  if (typeId === "data_sync") {
    openAutomationSettings().catch((err) => toast(`Could not open schedule: ${err.message}`));
    return;
  }
  const type = AUTOMATION_TYPES[typeId];
  if (type.planned) {
    modal(type.label, `<div class="automation-form"><p class="callout"><b>Planned feature.</b> This workflow is not running yet. It needs a background job and, where applicable, email or platform credentials. No inactive rule will be presented as live.</p></div>`,
      `<button id="cancelAutomationRule" class="primary">Close</button>`);
    $("#cancelAutomationRule").addEventListener("click", closeModal);
    return;
  }
  modal(type.label, `
    <div class="automation-form automation-builder-form">
      <div class="automation-modal-intro"><span aria-hidden="true">${type.icon}</span><p>${esc(type.description)}</p></div>
      <label>Automation name<input id="automationName" maxlength="60" value="${esc(type.label)}"></label>
      <label>What should it do?<select id="automationAction">${type.actions.map(([value, label]) => `<option value="${value}">${esc(label)}</option>`).join("")}</select></label>
      ${type.email ? `<label>Delivery email <span class="optional-word">optional</span><input id="automationEmail" type="email" placeholder="team@company.com" autocomplete="email"></label>` : ""}
      ${automationThresholdFields(typeId)}
      <div class="automation-condition-grid">
        <label>Run<select id="automationCadence"><option value="hourly">Every hour</option><option value="daily" selected>Every day</option><option value="weekdays">Weekdays</option><option value="weekly">Every week</option><option value="monthly">Every month</option></select></label>
        <label>At<input id="automationTime" type="time" value="09:00"></label>
      </div>
      <p class="callout">This workflow is saved on this computer. Email delivery and platform uploads require their connection details before they can run.</p>
    </div>`,
  `<button id="cancelAutomationRule">Cancel</button><button id="saveAutomationRule" class="primary">Save workflow</button>`);
  $("#cancelAutomationRule").addEventListener("click", closeModal);
  $("#saveAutomationRule").addEventListener("click", async () => {
    const name = $("#automationName").value.trim();
    if (!name) { toast("Choose an automation name"); $("#automationName").focus(); return; }
    const action = $("#automationAction");
    const cadence = $("#automationCadence");
    const rules = getAutomationRules();
    rules.unshift({
      id: String(Date.now()), type: typeId, name,
      action: action.value, actionLabel: action.selectedOptions[0].textContent,
      cadence: cadence.value, scheduleLabel: `${cadence.selectedOptions[0].textContent} at ${$("#automationTime").value}`,
      metric: $("#automationMetric")?.value || null,
      condition: $("#automationCondition")?.value || null,
      threshold: $("#automationThreshold")?.value || null,
      leadAge: $("#automationLeadAge")?.value || null,
      retention: $("#automationRetention")?.value || null,
      email: $("#automationEmail")?.value.trim() || null,
    });
    await persistAutomationRules(rules.slice(0, 30));
    closeModal(); renderAutomationRules(); toast("Automation workflow saved");
  });
}

async function loadHub() {
  renderSavedViews(); renderCustomMetrics(); renderConnectorDirectory(); renderAutomationRules();
  const [overview, automation, mcpInfo, quality] = await Promise.all([
    api(`/api/overview?${qs()}`), api("/api/automation"),
    currentUser?.role === "viewer" ? Promise.resolve({ http_enabled: false, tools: [] }) : api("/api/mcp"),
    api(`/api/data-quality?${qs()}`),
  ]);
  state.overview = overview;
  renderPerformanceInsights(overview);
  const meta = state.meta || await api("/api/meta");
  state.meta = meta;
  renderAutomationTypeSummary();
  const schedulerEnabled = Boolean(meta.scheduler_enabled);
  $("#automationScheduleLabel").textContent = schedulerEnabled ? "Active schedules" : "Saved schedule settings";
  $("#fullSchedule").textContent = schedulerEnabled
    ? `${automation.full_sync_time} · previous ${meta.sync_lookback_days || 30} days`
    : "Paused · background scheduler is off";
  $("#topupSchedule").textContent = schedulerEnabled
    ? `Every ${automation.topup_hours} hours · previous 3 days`
    : "Paused · background scheduler is off";
  $("#fullScheduleDot").classList.toggle("good", schedulerEnabled);
  $("#topupScheduleDot").classList.toggle("good", schedulerEnabled);
  $("#mcpStatus").textContent = mcpInfo.http_enabled ? "Local + HTTP ready" : "Local ready";
  $("#mcpStatus").classList.toggle("online", true);
  $("#mcpToolCount").textContent = `${mcpInfo.tools.length} read-only tools`;
  const qualityList = $("#qualityList");
  qualityList.innerHTML = quality.checks.map((check) => `<div class="quality-item ${check.status}"><span aria-hidden="true">${check.status === "pass" ? "✓" : check.status === "warn" ? "!" : "×"}</span><div><b>${esc(check.title)}</b><small>${esc(check.detail)}</small>${check.status === "pass" ? "" : `<small class="quality-next"><strong>Next:</strong> ${esc(check.next_step || "Review the source data before using this metric.")}</small>${check.action_route ? `<button type="button" class="quality-action" data-quality-route="${esc(check.action_route)}">${esc(check.action_label || "Review")}</button>` : ""}`}</div></div>`).join("");
  qualityList.querySelectorAll("[data-quality-route]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.qualityRoute));
  });
}

function workspaceItems(node, items, render, empty) {
  node.innerHTML = items.length ? items.map(render).join("") : `<div class="empty compact">${esc(empty)}</div>`;
}

async function runExplorer() {
  const requestId = (state.explorerRequestId || 0) + 1;
  state.explorerRequestId = requestId;
  const dimensions = selectedExplorerDimensions();
  const metrics = selectedExplorerMetrics();
  const sortSelect = $("#explorerSort");
  const previousSort = state.pendingExplorerSort || sortSelect.value;
  state.pendingExplorerSort = "";
  const sortOptions = [...dimensions, ...metrics];
  sortSelect.innerHTML = sortOptions.map((key) => `<option value="${key}">${key.replaceAll("_", " ").replace(/\b\w/g, (x) => x.toUpperCase())}</option>`).join("");
  sortSelect.value = sortOptions.includes(previousSort) ? previousSort : (metrics[0] || dimensions[0]);
  const filter = $("#explorerFilter").value.trim();
  const body = {
    start: state.start, end: state.end, dimensions, metrics,
    sort: sortSelect.value, direction: $("#explorerDirection").value, limit: 100,
    filters: filter ? [{ field: "campaign", operator: "contains", value: filter }] : [],
  };
  const result = await api("/api/explorer/query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (requestId !== state.explorerRequestId || body.start !== state.start || body.end !== state.end) return;
  state.explorerDefinition = body;
  const columns = result.columns.map((key) => ({
    id: key,
    label: key.replaceAll("_", " ").replace(/\b\w/g, (x) => x.toUpperCase()),
    num: result.rows.some((row) => typeof row[key] === "number"),
    get: (row) => {
      const value = row[key];
      if (value == null) return "—";
      if (["spend", "cpc", "cpa", "conversion_value"].includes(key)) return moneyInCurrency(value, row.currency);
      if (key === "currency") return esc(value || "Unknown");
      if (["ctr"].includes(key)) return `${nf1.format(value)}%`;
      return typeof value === "number" ? nf1.format(value) : esc(String(value));
    },
  }));
  const node = $("#tblExplorer");
  prepareExplorerColumns(node, columns);
  table(node, columns, result.rows, "No rows match this explorer query.");
  const notice = $("#explorerNotice");
  notice.textContent = (result.warnings || []).join(" ");
  notice.hidden = !notice.textContent;
}

function selectedExplorerDimensions() {
  const selected = $$('[data-explorer-dimension]:checked').map((input) => input.value).slice(0, 4);
  return selected.length ? selected : ["campaign"];
}

function selectedExplorerMetrics() {
  const selected = $$('[data-explorer-metric]:checked').map((input) => input.value).slice(0, 8);
  return selected.length ? selected : ["spend"];
}

function prepareExplorerColumns(node, columns) {
  const valid = new Set(columns.map((column) => column.id));
  const normalize = (id) => String(id).toLowerCase().replace(/-\d+$/, "")
    .replace(/[^a-z0-9]+/g, "_");
  const previousView = state.tableViews[node.id];
  const saved = previousView ? [...previousView.columns] : storedObject("portalTableColumns")[node.id];
  const selected = Array.isArray(saved) ? saved.map(normalize).filter((id) => valid.has(id)) : [];
  const visible = new Set(selected.length ? selected : [...valid]);
  const previousColumns = new Set((node._tableConfig?.cols || []).map((column) =>
    normalize(column.id || column.label)));
  if (previousColumns.size) {
    valid.forEach((id) => { if (!previousColumns.has(id)) visible.add(id); });
  }
  if (valid.has("currency")) visible.add("currency");
  state.tableViews[node.id] = { query: previousView?.query || "", columns: visible };
}

function selectedDashboardMetrics() {
  return $$('[data-dashboard-metric]:checked').map((input) => input.value);
}

const DASHBOARD_METRICS = {
  spend: ["Spend", "spend", "money"], impressions: ["Impressions", "impressions", "number"],
  clicks: ["Clicks", "clicks", "number"], platform_conversions: ["Platform conversions", "platform_conversions", "number"],
  leads: ["CRM leads", "leads", "number"], qualified: ["Qualified leads", "qualified", "number"],
  sales: ["Sales", "sales", "number"], revenue: ["CRM revenue", "revenue", "money"],
  cpl: ["CPL", "cpl", "money"], cac: ["CAC", "cac", "money"],
  roas: ["CRM ROAS", "roas", "ratio"], ctr: ["CTR", "ctr", "percent"], sessions: ["Sessions", "sessions", "number"],
};
const LEGACY_DASHBOARD_METRICS = {
  "Spend": "spend", "CRM leads": "leads", "CAC": "cac", "Sales": "sales",
  "Clicks": "clicks", "Conversions": "platform_conversions", "CPL": "cpl", "Qualified": "qualified",
};

async function loadWorkspace() {
  const requestId = (state.workspaceRequestId || 0) + 1;
  state.workspaceRequestId = requestId;
  state.explorerRequestId = (state.explorerRequestId || 0) + 1;
  state.workspaceOverview = null;
  previewDashboard($("#savedDashboards").value);
  if (state.explorerDefinition && (state.explorerDefinition.start !== state.start
      || state.explorerDefinition.end !== state.end)) {
    const node = $("#tblExplorer");
    table(node, node._tableConfig?.cols || [], [], "Loading report for the selected dates…");
    $("#planningCards").textContent = "Loading figures for the selected dates…";
    $("#explorerNotice").hidden = true;
  }
  const [workspace, planning, overview, meta] = await Promise.all([api("/api/workspace"), api(`/api/planning?${qs()}`), api(`/api/overview?${qs()}`), api("/api/meta")]);
  if (requestId !== state.workspaceRequestId) return;
  state.workspace = workspace;
  $("#planningCards").innerHTML = [
    ["Daily spend", money(planning.daily_spend)], ["30-day spend", money(planning.forecast_30_day_spend)],
    ["30-day leads", num(planning.forecast_30_day_leads)], ["30-day outcomes", num(planning.forecast_30_day_sales)],
    ["Current CPL", money(planning.cpl)], ["Current CAC", money(planning.cac)],
  ].map(([label, value]) => `<div><span>${esc(label)}</span><b>${value}</b></div>`).join("") + `<p>${esc(planning.warning)}</p>`;
  $("#attributionStatus").textContent = planning.attribution.note;
  $("#workspaceSchedulerStatus").textContent = meta.scheduler_enabled
    ? "Automatic schedules run on this local server. Keep the portal server running for scheduled jobs."
    : "Automatic schedules are paused. Manual runs are still available.";
  workspaceItems($("#dashboardList"), workspace.dashboards, (x) => `<div class="workspace-dashboard-item"><div><b>${esc(x.name)}</b><small>${esc(x.description || "Team dashboard")} · ${x.shared ? "Shared" : "Private"}</small></div>${currentUser?.role === "admin" ? `<button type="button" class="dashboard-remove" data-delete-dashboard="${x.id}" aria-label="Remove ${esc(x.name)} dashboard">Remove</button>` : ""}</div>`, "No dashboards created yet.");
  const dashboardSelect = $("#savedDashboards");
  const selectedDashboard = dashboardSelect.value;
  dashboardSelect.innerHTML = `<option value="">Preview a dashboard…</option>${workspace.dashboards.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}`;
  dashboardSelect.value = workspace.dashboards.some((x) => String(x.id) === selectedDashboard) ? selectedDashboard : "";
  const viewSelect = $("#savedExplorerViews");
  const selectedView = viewSelect.value;
  viewSelect.innerHTML = `<option value="">Open saved view…</option>${workspace.views.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}`;
  viewSelect.value = workspace.views.some((x) => String(x.id) === selectedView) ? selectedView : "";
  $("#removeExplorerView").disabled = !viewSelect.value;
  state.workspaceOverview = overview.current;
  previewDashboard(dashboardSelect.value);
  workspaceItems($("#ruleList"), workspace.governance.rules, (x) => `<div class="workspace-removable"><span><b>${esc(x.name)}</b><small>${esc(x.field)} ${esc(x.match_type)} “${esc(x.pattern)}” → ${esc(x.output)}</small></span>${currentUser?.role === "admin" ? `<button type="button" class="workspace-remove" data-delete-workspace="transformations" data-workspace-id="${x.id}">Remove</button>` : ""}</div>`, "No transformation rules yet.");
  workspaceItems($("#destinationList"), workspace.destinations, (x) => `<div class="workspace-removable"><span><b>${esc(x.name)}</b><small>${esc(x.kind.replaceAll("_", " "))} · ${x.enabled ? "Enabled" : "Paused"} · ${esc(x.status === "not_available" ? "Not available yet" : x.status)}${x.status !== "ready" && x.status !== "not_available" ? " · Setup required" : ""}</small></span>${currentUser?.role === "admin" ? `<button type="button" class="workspace-remove" data-delete-workspace="destinations" data-workspace-id="${x.id}">Remove</button>` : ""}</div>`, "No destinations configured yet.");
  const destinationSelect = $("#jobDestination");
  const selectedDestination = destinationSelect.value;
  const destinations = workspace.destinations.filter((x) => x.enabled && (["download", "folder"].includes(x.kind) || (x.kind === "email" && x.status === "ready")));
  destinationSelect.innerHTML = `<option value="">Default local reports folder</option>${destinations.map((x) => `<option value="${x.id}">${esc(x.name)} (${esc(x.kind)})</option>`).join("")}`;
  destinationSelect.value = destinations.some((x) => String(x.id) === selectedDestination) ? selectedDestination : "";
  workspaceItems($("#jobList"), workspace.jobs, (x) => `<div class="workspace-removable"><span><b>${esc(x.name)}</b><small>${esc(x.job_type)} · ${esc(x.schedule)} at ${esc(x.run_at)} · ${x.last_run_at ? `Last run ${esc(x.last_run_at)}` : "Not run yet"}</small></span><button type="button" data-run-job="${x.id}">Run now</button>${currentUser?.role === "admin" ? `<button type="button" class="workspace-remove" data-delete-workspace="jobs" data-workspace-id="${x.id}">Remove</button>` : ""}</div>`, "No scheduled jobs yet.");
  workspaceItems($("#jobRunList"), workspace.job_runs.slice(0, 10), (x) => `<div><b>${esc(x.status.replaceAll("_", " ").toUpperCase())} · ${esc(x.message)}</b><small>${esc(x.finished_at || x.started_at)}</small>${x.artifact ? `<a href="/api/job-runs/${x.id}/download">Download file</a>` : ""}</div>`, "No job runs recorded yet.");
  workspaceItems($("#userList"), workspace.governance.users, (x) => `<div><b>${esc(x.display_name || x.email)}</b><small>${esc(x.email)} · ${esc(x.role)} · ${x.enabled ? "Active" : "Disabled"}</small><button type="button" data-toggle-user data-email="${esc(x.email)}" data-name="${esc(x.display_name || "")}" data-role="${esc(x.role)}" data-enabled="${x.enabled ? "true" : "false"}" ${x.email === currentUser?.email ? "disabled title=\"Use another administrator account to disable yourself\"" : ""}>${x.enabled ? "Disable" : "Enable"}</button></div>`, "No portal accounts configured.");
  workspaceItems($("#metricDictionary"), workspace.governance.metrics, (x) => `<div><b>${esc(x.label)}</b><small>${esc(x.formula)} · ${esc(x.description)}</small></div>`, "No metric definitions.");
  workspaceItems($("#auditList"), workspace.audit.slice(0, 12), (x) => `<div><b>${esc(x.action.replaceAll("_", " "))} ${esc(x.object_type.replaceAll("_", " "))}</b><small>${esc(x.actor)} · ${new Date(`${x.occurred_at}Z`).toLocaleString("en-GB")}</small></div>`, "No workspace changes recorded.");
  await runExplorer();
}

function confirmWorkspaceRemoval(kind, id) {
  if (currentUser?.role !== "admin") return;
  const specs = {
    views: ["views", "explorer/views", "saved view"],
    transformations: ["governance.rules", "transformations", "transformation rule"],
    destinations: ["destinations", "destinations", "destination"],
    jobs: ["jobs", "jobs", "scheduled job"],
  };
  const spec = specs[kind];
  if (!spec) return;
  const collection = kind === "transformations" ? state.workspace?.governance?.rules : state.workspace?.[spec[0]];
  const item = collection?.find((entry) => String(entry.id) === String(id));
  if (!item) return;
  const note = kind === "jobs" ? " Its execution history will also be removed. Generated files will stay in the reports folder."
    : kind === "destinations" ? " Scheduled jobs using it must be removed first."
      : " Source data will stay unchanged.";
  modal(`Remove ${spec[2]}?`, `<p class="hint">Remove <b>${esc(item.name)}</b>?${note}</p>`,
    `<button id="cancelWorkspaceRemoval">Cancel</button><button id="confirmWorkspaceRemoval" class="workspace-remove">Remove ${spec[2]}</button>`);
  $("#cancelWorkspaceRemoval").addEventListener("click", closeModal);
  $("#confirmWorkspaceRemoval").addEventListener("click", async () => {
    const button = $("#confirmWorkspaceRemoval");
    setButtonBusy(button, true);
    try {
      await api(`/api/${spec[1]}/${item.id}/delete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      closeModal(); toast(`${spec[2][0].toUpperCase()}${spec[2].slice(1)} removed`); await loadWorkspace();
    } catch (error) {
      toast(`Could not remove ${spec[2]}: ${error.message}`, "error"); setButtonBusy(button, false);
    }
  });
}

function previewDashboard(id) {
  const dashboard = state.workspace?.dashboards.find((x) => String(x.id) === String(id));
  const target = $("#dashboardPreview");
  if (!dashboard) { target.hidden = true; target.innerHTML = ""; return; }
  target.hidden = false;
  if (!state.workspaceOverview) {
    target.replaceChildren();
    target.textContent = "Loading dashboard for the selected dates…";
    return;
  }
  const values = state.workspaceOverview;
  target.innerHTML = (dashboard.layout || []).map((savedMetric) => {
    const key = LEGACY_DASHBOARD_METRICS[savedMetric] || savedMetric;
    const metric = DASHBOARD_METRICS[key];
    if (!metric) return "";
    const [label, valueKey, format] = metric;
    const value = values[valueKey];
    const formatted = value == null ? "Not available" : format === "money" ? money(value)
      : format === "percent" ? `${nf1.format(value)}%` : format === "ratio" ? `${nf1.format(value)}×` : num(value);
    return `<div><span>${esc(label)}</span><b>${formatted}</b></div>`;
  }).join("");
}

async function saveWorkspaceObject(path, body, success) {
  await api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  toast(success); await loadWorkspace();
}

async function copyMcpText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${label} copied`);
  } catch (_) {
    const input = document.createElement("textarea");
    input.value = text; document.body.appendChild(input); input.select(); document.execCommand("copy"); input.remove();
    toast(`${label} copied`);
  }
}

async function openMcpSetup() {
  const info = await api("/api/mcp");
  const claudeConfig = JSON.stringify({
    mcpServers: { marketingPortal: { command: info.stdio_command, args: info.stdio_args } },
  }, null, 2);
  const remoteUrl = location.protocol === "https:"
    ? `${location.origin}/mcp`
    : "https://YOUR-PRIVATE-PORTAL-DOMAIN/mcp";
  const openAiConfig = JSON.stringify({
    type: "mcp", server_label: "marketing_portal", server_url: remoteUrl,
    headers: { Authorization: "Bearer YOUR_PORTAL_MCP_TOKEN" }, require_approval: "never",
  }, null, 2);
  modal("Connect an AI assistant", `
    <div class="mcp-setup">
      <section><div class="mcp-setup-head"><div><b>Claude Desktop</b><small>Private local connection · ready now</small></div><span class="mcp-status online">Ready</span></div>
        <p>Add this server to Claude Desktop’s MCP configuration, then restart Claude.</p>
        <pre id="claudeMcpConfig"><code>${esc(claudeConfig)}</code></pre>
        <button type="button" data-copy-mcp="claude">Copy Claude configuration</button>
      </section>
      <section><div class="mcp-setup-head"><div><b>ChatGPT / OpenAI API</b><small>Secure Streamable HTTP connection</small></div><span class="mcp-status ${info.http_enabled ? "online" : ""}">${info.http_enabled ? "Enabled" : "Protected"}</span></div>
        <p>${info.http_enabled ? "Use the configuration below with the portal’s private HTTPS address." : "HTTP access is safely disabled. Set PORTAL_MCP_TOKEN in .env, use a private HTTPS address, and restart the portal before connecting a hosted AI client."}</p>
        <pre id="openAiMcpConfig"><code>${esc(openAiConfig)}</code></pre>
        <button type="button" data-copy-mcp="openai">Copy OpenAI configuration</button>
      </section>
      <div class="callout">MCP access is read-only. AI assistants can analyse portal data but cannot change connections, trigger syncs, or delete records.</div>
    </div>`, `<button id="closeMcpSetup" class="primary">Done</button>`);
  $("#closeMcpSetup").addEventListener("click", closeModal);
  $$('[data-copy-mcp]').forEach((button) => button.addEventListener("click", () => {
    copyMcpText(button.dataset.copyMcp === "claude" ? claudeConfig : openAiConfig, button.dataset.copyMcp === "claude" ? "Claude configuration" : "OpenAI configuration");
  }));
}

async function openAutomationSettings() {
  const [settings, meta] = await Promise.all([api("/api/automation"), api("/api/meta")]);
  modal("Data automation schedule", `
    <div class="automation-form">
      <label>Daily full refresh time<input id="automationFullTime" type="time" value="${esc(settings.full_sync_time)}"></label>
      <label>Conversion top-up interval<select id="automationTopupHours">
        ${[1, 2, 4, 6, 12, 24].map((hours) => `<option value="${hours}" ${hours === settings.topup_hours ? "selected" : ""}>Every ${hours} hour${hours === 1 ? "" : "s"}</option>`).join("")}
      </select></label>
      <p class="callout">The scheduler uses ${esc(meta.timezone || "the portal timezone")}. Keep this portal server running for automatic refreshes.</p>
    </div>`,
  `<button id="cancelAutomation">Cancel</button><button id="saveAutomation" class="primary">Save schedule</button>`);
  $("#cancelAutomation").addEventListener("click", closeModal);
  $("#saveAutomation").addEventListener("click", async () => {
    const button = $("#saveAutomation"); setButtonBusy(button, true);
    try {
      await api("/api/automation", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        full_sync_time: $("#automationFullTime").value,
        topup_hours: Number($("#automationTopupHours").value),
      }) });
      closeModal(); await loadHub(); toast("Automation schedule saved");
    } catch (err) { toast(`Could not save schedule: ${err.message}`); }
    finally { setButtonBusy(button, false); }
  });
}

async function downloadAutomationReport() {
  const button = $("#downloadAutomation"); setButtonBusy(button, true);
  try {
    const [overview, channels, campaigns] = await Promise.all([
      api(`/api/overview?${qs()}`), api(`/api/channels?${qs()}`), fetchAllCampaigns(),
    ]);
    const chunks = [];
    chunks.push([csvCell("Overview metric"), csvCell("Value")].join(",") + "\n" + Object.entries(overview.current || {}).map(([key, value]) => `${csvCell(key)},${csvCell(value)}`).join("\n"));
    const addRows = (title, rows) => {
      if (!rows.length) return;
      const columns = Object.keys(rows[0]);
      chunks.push(`${csvCell(title)}\n${columns.map(csvCell).join(",")}\n${rows.map((row) => columns.map((key) => csvCell(row[key])).join(",")).join("\n")}`);
    };
    addRows("Channel performance", channels); addRows("Campaign performance", campaigns);
    const blob = csvBlob(chunks.join("\n\n"));
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob);
    link.download = `marketing-report-${state.start}-to-${state.end}.csv`;
    link.click(); URL.revokeObjectURL(link.href); toast("Marketing report downloaded");
  } catch (err) { toast(`Could not download report: ${err.message}`); }
  finally { setButtonBusy(button, false); }
}

/* ------------------------------------------------------------------ wiring */
const LOADERS = {
  overview: loadOverview, paid: loadPaid, web: loadWeb,
  search: loadSearch, crm: loadCrm, connections: loadConnections, workspace: loadWorkspace, hub: loadHub,
  settings: loadSettings,
};
let activeTab = "overview";
let refreshMetricCampaigns = null;

function loaderFor(name) {
  const aliases = { website: "web", paid_campaigns: "paid", crm_funnel: "crm", data_hub: "hub" };
  name = aliases[name] || name;
  return sourceRouteId(name) ? loadSource : LOADERS[name];
}

function pageInfoFor(name) {
  const id = sourceRouteId(name);
  if (!id) return PAGE_INFO[name] || PAGE_INFO.overview;
  const connection = state.connections.find((item) => item.id === id);
  return connection
    ? [connection.name, `${connection.provider_label} data, sync status, and imported records.`]
    : ["Connected source", "Review this connector's sync status and imported records."];
}

async function refresh() {
  const section = sourceRouteId() ? $("#tab-source") : $(`#tab-${activeTab}`);
  section?.setAttribute("aria-busy", "true");
  section?.classList.add("is-loading");
  try {
    const loader = loaderFor(activeTab);
    await Promise.all([loader ? loader() : Promise.resolve(),
      refreshMetricCampaigns ? refreshMetricCampaigns() : Promise.resolve()]);
  } catch (err) {
    toast(`Could not load: ${err.message}`);
  } finally {
    section?.removeAttribute("aria-busy");
    section?.classList.remove("is-loading");
  }
}

async function refreshPortal(notify = false) {
  const button = $("#refreshPortal");
  setButtonBusy(button, true);
  try {
    const [meta, connections] = await Promise.all([api("/api/meta"), api("/api/connections")]);
    state.meta = meta;
    state.currency = meta.currency;
    state.dataTo = meta.data_to;
    const total = Object.values(meta.row_counts).reduce((a, b) => a + b, 0);
    $("#freshness").textContent = meta.data_to
      ? `· data through ${meta.data_to} · ${nf.format(total)} rows`
      : "· no data yet — open Connectors";
    renderSourceStatus(meta.sources || sourceStatusFromConnections(connections));
    renderSourceNavigation(connections);
    const loader = loaderFor(activeTab);
    await Promise.all([loader ? loader() : Promise.resolve(),
      refreshMetricCampaigns ? refreshMetricCampaigns() : Promise.resolve()]);
    if (notify) toast("Portal refreshed from saved data");
    return true;
  } catch (err) {
    toast(`Could not refresh portal: ${err.message}`, "error");
    return false;
  } finally {
    setButtonBusy(button, false);
  }
}

function switchTab(name, push = true) {
  const aliases = { website: "web", paid_campaigns: "paid", crm_funnel: "crm", data_hub: "hub" };
  name = aliases[name] || name;
  if (currentUser?.role === "viewer" && ["workspace", "connections"].includes(name)) name = "overview";
  if (currentUser?.role === "analyst" && name === "connections") name = "overview";
  if (name === "settings" && !$("[data-settings-content]:not([hidden])")) selectSettingsPanel("profile");
  const requestedSource = sourceRouteId(name);
  if (requestedSource && state.connections.find((c) => c.id === requestedSource
    && (c.provider === "superleap_crm" || (c.provider === "csv" && c.name.startsWith("Superleap CRM"))))) {
    name = "crm";
  }
  if (!loaderFor(name)) name = "overview";
  activeTab = name;
  document.body.classList.toggle("settings-page", name === "settings");
  state.activeSourceId = sourceRouteId(name);
  $("#accountMenuToggle")?.classList.toggle("is-active", name === "settings");
  const homeButton = $("#homeIconButton");
  if (homeButton) {
    if (name === "overview") homeButton.setAttribute("aria-current", "page");
    else homeButton.removeAttribute("aria-current");
  }
  if (push && location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
  $$("nav.tabs button, #sourceTabs button[data-tab]").forEach((b) => {
    const selected = b.dataset.tab === name;
    b.classList.toggle("on", selected);
    b.setAttribute("aria-selected", String(selected));
    b.tabIndex = selected ? 0 : -1;
  });
  if ($("#sourceSelect")) renderSourceNavigation(state.connections);
  const [title, description] = pageInfoFor(name);
  $("#pageTitle").textContent = title;
  $("#pageDescription").textContent = description;
  document.title = `${title} · Vortex Veri`;
  const sectionId = sourceRouteId(name) ? "tab-source" : `tab-${name}`;
  $$("main > section").forEach((s) => { s.hidden = s.id !== sectionId; });
  $("#sourceStatus").hidden = name === "settings";
  refresh();
}

function setupAssistant() {
  const launcher = $("#assistantLauncher"), panel = $("#assistantPanel");
  const question = $("#assistantQuestion"), send = $("#assistantSend");
  const messages = $("#assistantMessages"), status = $("#assistantStatus");
  const addMessage = (body, kind = "assistant") => {
    const item = document.createElement("p");
    item.className = `assistant-message ${kind}`;
    item.textContent = body;
    messages.append(item);
    messages.scrollTop = messages.scrollHeight;
  };
  const setOpen = (open) => {
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", String(open));
    if (open) question.focus(); else launcher.focus();
  };
  launcher.addEventListener("click", async () => {
    const open = panel.hidden;
    setOpen(open);
    if (!open) return;
    try {
      const availability = await api("/api/assistant/status");
      status.textContent = availability.configured ?
        (availability.mode === "openai" ? `ChatGPT · ${availability.model}` :
          availability.mode === "local_model" ? `Local AI · ${availability.model}` :
          "Basic answers · model offline") : "Unavailable";
      send.disabled = !availability.configured;
      if (!availability.configured && !messages.querySelector(".assistant-message")) addMessage("Portal reports are unavailable right now.", "error");
    } catch (error) { status.textContent = "Unavailable"; send.disabled = true; addMessage(error.message, "error"); }
  });
  $("#assistantClose").addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !panel.hidden) setOpen(false); });
  messages.addEventListener("click", (event) => {
    const prompt = event.target.closest("[data-assistant-prompt]");
    if (!prompt) return;
    question.value = prompt.dataset.assistantPrompt;
    $("#assistantForm").requestSubmit();
  });
  $("#assistantForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = question.value.trim();
    if (!value || send.disabled) return;
    addMessage(value, "user");
    question.value = "";
    send.disabled = true;
    status.textContent = "Checking portal reports…";
    try {
      const result = await api("/api/assistant/ask", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: value, start: $("#start").value, end: $("#end").value }) });
      addMessage(result.answer);
      const evidence = document.createElement("p");
      evidence.className = "assistant-evidence";
      evidence.textContent = `${result.range.start} to ${result.range.end}${result.date_basis === "question" ? " · date from your question" : " · dashboard dates"} · ${result.model === "portal-basic" ? "Report: " : "Reports supplied: "}${result.reports_used.join(", ")}`;
      messages.append(evidence);
      messages.scrollTop = messages.scrollHeight;
      status.textContent = result.model === "portal-basic" ? "Portal report answer" :
        result.provider === "openai" ? `ChatGPT · ${result.model}` : `Local AI · ${result.model}`;
    } catch (error) { addMessage(error.message, "error"); status.textContent = "Could not answer"; }
    finally { send.disabled = false; question.focus(); }
  });
}

async function boot() {
  updateBrandMark();
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (!document.documentElement.dataset.theme) updateBrandMark();
  });

  // Password visibility toggle
  document.querySelectorAll(".password-toggle").forEach(button => {
    button.addEventListener("click", (e) => {
      e.preventDefault();
      const inputId = button.getAttribute("aria-controls");
      const input = document.getElementById(inputId);
      const isPassword = input.type === "password";
      input.type = isPassword ? "text" : "password";
      button.setAttribute("aria-label", isPassword ? "Hide password" : "Show password");
    });
  });

  const authInfo = await api("/api/auth/me");
  csrfToken = authInfo.csrf_token;
  applyRoleUI(authInfo.user);
  setupAssistant();
  const [meta, connections, automationRules, profile] = await Promise.all([
    api("/api/meta"), api("/api/connections"),
    currentUser.role !== "admin" ? Promise.resolve({ rules: [] }) : api("/api/automation/rules"),
    api("/api/auth/profile"),
  ]);
  renderProfileSettings(profile);
  try { await hydrateUiPreferences(authInfo.user); }
  catch (error) { toast(`Account layout could not load: ${error.message}`, "error"); }
  localStorage.setItem("portalAutomationRules", JSON.stringify(automationRules.rules || []));
  state.meta = meta;
  state.currency = meta.currency;
  state.dataTo = meta.data_to;
  state.connections = connections;
  renderSourceNavigation(connections);
  setRange(30);
  loadCustomMetrics();
  loadCampaignColumnPreference();
  setupColumnDrawer();
  renderColumnMenu();
  $("#demoBanner").hidden = !meta.demo_mode;
  const total = Object.values(meta.row_counts).reduce((a, b) => a + b, 0);
  $("#freshness").textContent = meta.data_to
    ? `· data through ${meta.data_to} · ${nf.format(total)} rows`
    : "· no data yet — open Connectors";
  renderSourceStatus(meta.sources || sourceStatusFromConnections(connections));

  $("nav.tabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-tab]");
    if (button) switchTab(button.dataset.tab);
  });
  const accountMenuToggle = $("#accountMenuToggle"), accountMenu = $("#accountMenu");
  const closeAccountMenu = (restoreFocus = false) => {
    accountMenu.hidden = true;
    accountMenuToggle.setAttribute("aria-expanded", "false");
    if (restoreFocus) accountMenuToggle.focus();
  };
  accountMenuToggle.addEventListener("click", () => {
    const open = accountMenu.hidden;
    accountMenu.hidden = !open;
    accountMenuToggle.setAttribute("aria-expanded", String(open));
    if (open) accountMenu.querySelector("[role='menuitem']")?.focus();
  });
  accountMenuToggle.addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    accountMenu.hidden = false;
    accountMenuToggle.setAttribute("aria-expanded", "true");
    const items = [...accountMenu.querySelectorAll("[role='menuitem']")];
    (event.key === "ArrowUp" ? items.at(-1) : items[0])?.focus();
  });
  accountMenu.addEventListener("click", (event) => {
    const item = event.target.closest("[role='menuitem']");
    if (!item) return;
    closeAccountMenu();
    if (item.id === "accountSettingsButton") switchTab("settings");
  });
  accountMenu.addEventListener("keydown", (event) => {
    const items = [...accountMenu.querySelectorAll("[role='menuitem']")];
    const index = items.indexOf(document.activeElement);
    if (event.key === "Escape") { event.preventDefault(); closeAccountMenu(true); }
    else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(index + step + items.length) % items.length]?.focus();
    } else if (event.key === "Home") { event.preventDefault(); items[0]?.focus(); }
    else if (event.key === "End") { event.preventDefault(); items.at(-1)?.focus(); }
  });
  document.addEventListener("click", (event) => {
    if (!accountMenu.hidden && !event.target.closest("#portalAccount")) closeAccountMenu();
  });
  const sourceButton = $("#sourceSelect"), sourceMenu = $("#sourceMenu");
  const positionSourceMenu = () => {
    if (sourceMenu.hidden) return;
    const rect = sourceButton.getBoundingClientRect();
    const width = Math.min(340, window.innerWidth - 24);
    sourceMenu.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
    sourceMenu.style.top = `${Math.min(rect.bottom + 7, window.innerHeight - 120)}px`;
    sourceMenu.style.width = `${width}px`;
    sourceMenu.style.maxHeight = `${Math.max(112, window.innerHeight - rect.bottom - 20)}px`;
  };
  const closeSourceMenu = (restoreFocus = false) => {
    sourceMenu.hidden = true;
    sourceButton.setAttribute("aria-expanded", "false");
    if (restoreFocus) sourceButton.focus();
  };
  const openSourceMenu = (focusItem = false) => {
    sourceMenu.hidden = false;
    sourceButton.setAttribute("aria-expanded", "true");
    positionSourceMenu();
    if (focusItem) (sourceMenu.querySelector("[aria-current='page']") || sourceMenu.querySelector("[role='menuitem']"))?.focus();
  };
  sourceButton.addEventListener("click", () => sourceMenu.hidden ? openSourceMenu() : closeSourceMenu());
  sourceButton.addEventListener("keydown", (event) => {
    if (["ArrowDown", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      openSourceMenu(true);
    } else if (event.key === "Escape") closeSourceMenu();
  });
  $("#sourceMenu").addEventListener("click", (event) => {
    const item = event.target.closest("[data-source-route]");
    if (!item) return;
    closeSourceMenu();
    switchTab(item.dataset.sourceRoute);
  });
  sourceMenu.addEventListener("keydown", (event) => {
    const items = [...sourceMenu.querySelectorAll("[role='menuitem']")];
    const current = items.indexOf(document.activeElement);
    if (event.key === "Escape") {
      event.preventDefault();
      closeSourceMenu(true);
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#sourceTabs")) {
      closeSourceMenu();
    }
  });
  window.addEventListener("resize", positionSourceMenu);
  window.addEventListener("scroll", positionSourceMenu, true);
  $("nav.tabs").addEventListener("keydown", (ev) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(ev.key)) return;
    const tabs = $$("nav.tabs button[data-tab]");
    const current = tabs.indexOf(document.activeElement);
    if (current < 0) return;
    ev.preventDefault();
    const next = ev.key === "Home" ? 0 : ev.key === "End" ? tabs.length - 1
      : (current + (ev.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus();
    switchTab(tabs[next].dataset.tab);
  });

  // The URL is the source of truth for which tab is open, so that a bookmark
  // or a pasted #crm link lands where it says. Re-checked on pageshow too:
  // a back/forward-cache restore brings back the old DOM without re-running
  // boot(), which would otherwise leave the page showing the wrong tab.
  const syncFromHash = (force = false) => {
    const aliases = { website: "web", paid_campaigns: "paid", crm_funnel: "crm", data_hub: "hub" };
    const raw = location.hash.slice(1);
    const name = aliases[raw] || raw;
    if (loaderFor(name) && (force || name !== activeTab)) switchTab(name, false);
    else if (raw && !loaderFor(name)) switchTab("overview");
  };
  window.addEventListener("hashchange", () => syncFromHash());
  window.addEventListener("pageshow", (ev) => { if (ev.persisted) syncFromHash(true); });

  $$("[data-preset]").forEach((b) => b.addEventListener("click", () => {
    $$("[data-preset]").forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-pressed", "false"); });
    b.classList.add("on");
    b.setAttribute("aria-pressed", "true");
    setRange(+b.dataset.preset);
    closeDateRange();
    refresh();
  }));
  $("#dateRangeToggle").addEventListener("click", () => {
    const menu = $("#dateRangeMenu");
    if (menu.hidden) openDateRange(); else closeDateRange();
  });
  $("#dateRangeMenu").addEventListener("click", (ev) => ev.stopPropagation());
  $$('[data-calendar-mode]').forEach((button) => button.addEventListener("click", () => {
    setCalendarMode(button.dataset.calendarMode);
  }));
  $("#dateRangeMenu").addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    if (!$("#calendarPeriodPicker").hidden) {
      setCalendarPeriodPicker(false);
      return;
    }
    closeDateRange();
    $("#dateRangeToggle").focus();
  });
  $("#calendarPrev").addEventListener("click", () => moveCalendarMonth(-1));
  $("#calendarNext").addEventListener("click", () => moveCalendarMonth(1));
  $("#calendarPeriodToggle").addEventListener("click", (event) => {
    setCalendarPeriodPicker($("#calendarPeriodPicker").hidden, false);
    if (!$("#calendarPeriodPicker").hidden) event.currentTarget.setAttribute("aria-expanded", "true");
  });
  [["month", $("#calendarMonthWheel")], ["year", $("#calendarYearWheel")]].forEach(([type, wheel]) => {
    wheel.addEventListener("click", (event) => {
      const option = event.target.closest("[data-calendar-wheel-value]");
      if (option) selectCalendarWheelOption(wheel, option.dataset.calendarWheelValue);
    });
    wheel.addEventListener("scroll", () => {
      clearTimeout(calendarWheelScrollTimers[type]);
      calendarWheelScrollTimers[type] = setTimeout(updateCalendarPeriodFromWheels, 120);
    }, { passive: true });
    wheel.addEventListener("keydown", (event) => {
      if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const options = [...wheel.querySelectorAll("[data-calendar-wheel-value]")];
      if (!options.length) return;
      const index = Math.max(0, Math.min(options.length - 1,
        Math.round(wheel.scrollTop / CALENDAR_WHEEL_ROW_HEIGHT)));
      const target = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
        : Math.max(0, Math.min(options.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)));
      selectCalendarWheelOption(wheel, options[target].dataset.calendarWheelValue);
    });
  });
  $("#calendarGrid").addEventListener("click", (ev) => {
    const button = ev.target.closest("[data-calendar-day]");
    if (button) chooseCalendarDay(button.dataset.calendarDay);
  });
  $("#calendarGrid").addEventListener("keydown", (ev) => {
    const button = ev.target.closest("[data-calendar-day]");
    if (!button || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(ev.key)) return;
    ev.preventDefault();
    const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    const weekday = (new Date(`${button.dataset.calendarDay}T00:00:00Z`).getUTCDay() + 6) % 7;
    const amount = ev.key === "Home" ? -weekday : ev.key === "End" ? 6 - weekday : offsets[ev.key];
    const target = shiftIso(button.dataset.calendarDay, amount);
    if (target > portalTodayIso()) return;
    calendarMonth = monthIso(target); renderCalendar(target);
  });
  $("#calendarPresets").addEventListener("click", (ev) => {
    const remove = ev.target.closest("[data-delete-calendar-preset]");
    if (remove) {
      saveUiPreferenceRaw("calendarDatePresets", JSON.stringify(getCustomDatePresets().filter((item) => item.id !== remove.dataset.deleteCalendarPreset)));
      renderCalendarPresets(); toast("Preset removed"); return;
    }
    const preset = ev.target.closest("[data-calendar-preset]");
    if (preset) selectCalendarPreset(Number(preset.dataset.calendarPreset));
  });
  $("#addDatePreset").addEventListener("click", openDatePresetCreator);
  $("#cancelDateRange").addEventListener("click", () => {
    $("#start").value = state.start;
    $("#end").value = state.end;
    closeDateRange();
  });
  $("#applyDateRange").addEventListener("click", () => {
    const start = calendarDraftStart;
    const end = calendarDraftEnd;
    if (!start || !end || start > end || end > portalTodayIso()) {
      toast("Choose a valid start and end date");
      return;
    }
    state.start = start;
    state.end = end;
    $("#start").value = start;
    $("#end").value = end;
    updateDateRangeLabel();
    $$("[data-preset]").forEach((x) => {
      x.classList.remove("on");
      x.setAttribute("aria-pressed", "false");
    });
    closeDateRange();
    refresh();
  });

  $$("[data-src]").forEach((b) => b.addEventListener("click", () => {
    $$("[data-src]").forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-pressed", "false"); });
    b.classList.add("on");
    b.setAttribute("aria-pressed", "true");
    state.campaignSrc = b.dataset.src;
    if (state.campaignSrc !== "google_ads") state.campaignGoogleAccount = "";
    loadPaid();
    if (refreshMetricCampaigns) refreshMetricCampaigns();
  }));
  $("#googleAdsAccountToggle").addEventListener("click", () => {
    const toggle = $("#googleAdsAccountToggle");
    const menu = $("#googleAdsAccountMenu");
    const opening = menu.hidden;
    menu.hidden = !opening;
    toggle.setAttribute("aria-expanded", String(opening));
  });
  $("#googleAdsAccountMenu").addEventListener("click", (ev) => {
    const option = ev.target.closest("[data-google-account]");
    if (option) selectGoogleAccount(option.dataset.googleAccount);
  });
  $$("[data-funnel]").forEach((b) => b.addEventListener("click", () => {
    $$("[data-funnel]").forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-pressed", "false"); });
    b.classList.add("on");
    b.setAttribute("aria-pressed", "true");
    state.campaignFunnel = b.dataset.funnel;
    renderPaid();
  }));
  $("#campaignSearch").addEventListener("input", (ev) => {
    state.campaignQuery = ev.target.value;
    renderPaid();
  });
  $("#refreshPaid").addEventListener("click", () => loadPaid(true));
  const rowHeight = $("#campaignRowHeight");
  rowHeight.value = String(state.campaignRowHeight);
  $("#campaignRowHeightValue").textContent = `${state.campaignRowHeight}px`;
  rowHeight.setAttribute("aria-valuetext", `${state.campaignRowHeight} pixels`);
  rowHeight.addEventListener("input", (ev) => {
    state.campaignRowHeight = Number(ev.target.value);
    state.campaignRowHeights = {};
    $("#campaignRowHeightValue").textContent = `${state.campaignRowHeight}px`;
    rowHeight.setAttribute("aria-valuetext", `${state.campaignRowHeight} pixels`);
    $("#tblCampaigns").style.setProperty("--campaign-row-height", `${state.campaignRowHeight}px`);
    $$('tbody tr', $("#tblCampaigns")).forEach((tr) => tr.style.removeProperty("--campaign-row-height"));
    saveUiPreferenceRaw("campaignRowHeight", String(state.campaignRowHeight));
    removeUiPreference("campaignRowHeights");
  });
  $("#columnsToggle").addEventListener("click", (ev) => {
    openColumnDrawer({ type: "paid", node: $("#tblCampaigns"), columns: PAID_COLUMNS, opener: ev.currentTarget });
  });
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest(".date-range-wrap")) closeDateRange();
    if (!ev.target.closest(".account-filter")) closeGoogleAccountMenu();
  });
  $("#refreshPortal").addEventListener("click", () => refreshPortal(true));
  $("#ahrefsReconnect").addEventListener("click", async () => {
    const button = $("#ahrefsReconnect");
    setButtonBusy(button, true);
    try {
      const connectionId = Number(button.dataset.connectionId);
      if (connectionId) {
        const connection = await api(`/api/connections/${connectionId}`);
        openForm("ahrefs", connection);
      } else {
        await openConnectorSetup({ id: "ahrefs", name: "Ahrefs" });
      }
    } catch (error) {
      toast(`Could not open Ahrefs setup: ${error.message}`, "error");
    } finally { setButtonBusy(button, false); }
  });
  $("#saveView").addEventListener("click", saveCurrentView);
  $("#exportView").addEventListener("click", exportActiveView);
  $$("[data-dim]").forEach((b) => b.addEventListener("click", () => {
    $$("[data-dim]").forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-pressed", "false"); });
    b.classList.add("on");
    b.setAttribute("aria-pressed", "true");
    state.webDim = b.dataset.dim;
    loadWeb();
  }));
  $$("[data-sdim]").forEach((b) => b.addEventListener("click", () => {
    $$("[data-sdim]").forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-pressed", "false"); });
    b.classList.add("on");
    b.setAttribute("aria-pressed", "true");
    state.searchDim = b.dataset.sdim;
    loadSearch();
  }));

  $("#addConnection").addEventListener("click", openPicker);
  $("#connectorSearch").addEventListener("input", () => { connectorPageSize = 60; renderConnectorDirectory(); });
  $("#connectorGrid").addEventListener("click", (ev) => {
    if (ev.target.closest("[data-more-connectors]")) {
      connectorPageSize += 60; renderConnectorDirectory(); return;
    }
    const button = ev.target.closest("[data-connect-id]");
    if (!button) return;
    const item = (window.SOURCE_CONNECTORS || []).find((connector) => connector.id === button.dataset.connectId);
    if (item) openConnectorSetup(item).catch((err) => toast(`Could not open connector: ${err.message}`));
  });
  $("#runExplorer").addEventListener("click", () => runExplorer().catch((err) => toast(`Could not run report: ${err.message}`, "error")));
  $$('[data-explorer-dimension], [data-explorer-metric]').forEach((input) => input.addEventListener("change", () => {
    const selector = input.matches("[data-explorer-dimension]") ? '[data-explorer-dimension]:checked' : '[data-explorer-metric]:checked';
    const maximum = input.matches("[data-explorer-dimension]") ? 4 : 8;
    if ($$(selector).length > maximum) { input.checked = false; toast(`Choose up to ${maximum} ${maximum === 4 ? "grouping fields" : "metrics"}.`); }
  }));
  $("#dashboardTemplate").addEventListener("change", () => {
    const template = $("#dashboardTemplate").value;
    if (template === "custom") return;
    const presets = {
      executive: ["spend", "leads", "sales", "cac"],
      acquisition: ["spend", "clicks", "platform_conversions", "cpl", "roas"],
      crm: ["leads", "qualified", "sales", "revenue", "cac"],
    };
    const selected = new Set(presets[template] || []);
    $$('[data-dashboard-metric]').forEach((input) => { input.checked = selected.has(input.value); });
  });
  $("#destinationKind").addEventListener("change", () => {
    const kind = $("#destinationKind").value;
    const input = $("#destinationSetting");
    const label = $("#destinationSettingLabel");
    const help = $("#destinationHelp");
    const options = {
      download: ["Folder name (optional)", "Optional folder name", "Download destinations create a local CSV report."],
      folder: ["Report subfolder", "For example: weekly-reports", "Folders are restricted to the portal's reports directory."],
      email: ["Recipient email", "name@example.com", "Email reports use the send-only Gmail account connected in Profile & Settings."],
    }[kind];
    if (!options) { help.textContent = "This destination type is not available yet."; return; }
    label.firstChild.textContent = options[0];
    input.placeholder = options[1]; input.type = kind === "email" ? "email" : "text";
    help.textContent = options[2];
  });
  $("#saveExplorerView").addEventListener("click", () => {
    const name = $("#explorerViewName").value.trim();
    if (!name) { toast("Give this view a name"); $("#explorerViewName").focus(); return; }
    saveWorkspaceObject("/api/explorer/views", { name, definition: state.explorerDefinition || {} }, "Explorer view saved")
      .then(() => { $("#explorerViewName").value = ""; })
      .catch((err) => toast(`Could not save view: ${err.message}`, "error"));
  });
  $("#savedExplorerViews").addEventListener("change", (event) => {
    $("#removeExplorerView").disabled = !event.target.value;
    const view = state.workspace?.views.find((x) => String(x.id) === event.target.value);
    if (!view) return;
    const definition = view.definition || {};
    if (definition.start && definition.end && definition.start <= definition.end && definition.end <= portalTodayIso()) {
      state.start = definition.start; state.end = definition.end;
      $("#start").value = state.start; $("#end").value = state.end; updateDateRangeLabel();
      $$('[data-preset]').forEach((button) => { button.classList.remove("on"); button.setAttribute("aria-pressed", "false"); });
    }
    const dimensions = new Set(definition.dimensions || ["campaign"]);
    const metrics = new Set(definition.metrics || ["spend"]);
    $$('[data-explorer-dimension]').forEach((input) => { input.checked = dimensions.has(input.value); });
    $$('[data-explorer-metric]').forEach((input) => { input.checked = metrics.has(input.value); });
    $("#explorerDirection").value = definition.direction || "desc";
    state.pendingExplorerSort = definition.sort || [...metrics][0] || "spend";
    $("#explorerFilter").value = definition.filters?.[0]?.value || "";
    runExplorer().then(() => toast(`Opened ${view.name}`)).catch((err) => toast(`Could not open view: ${err.message}`, "error"));
  });
  $("#removeExplorerView").addEventListener("click", () => {
    const id = $("#savedExplorerViews").value;
    if (id) confirmWorkspaceRemoval("views", id);
  });
  $("#tab-workspace").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-workspace]");
    if (button) confirmWorkspaceRemoval(button.dataset.deleteWorkspace, button.dataset.workspaceId);
  });
  $("#savedDashboards").addEventListener("change", (event) => previewDashboard(event.target.value));
  $("#saveDashboard").addEventListener("click", () => {
    const name = $("#dashboardName").value.trim();
    if (!name) { toast("Give the dashboard a name"); $("#dashboardName").focus(); return; }
    const template = $("#dashboardTemplate").value;
    const cards = selectedDashboardMetrics();
    if (!cards.length) { toast("Select at least one KPI card.", "error"); return; }
    const label = template === "custom" ? "Custom" : template.replace(/\b\w/g, (x) => x.toUpperCase());
    saveWorkspaceObject("/api/dashboards", { name, description: `${label} dashboard`, layout: cards, shared: true }, "Dashboard created")
      .then(() => { $("#dashboardName").value = ""; })
      .catch((err) => toast(`Could not create dashboard: ${err.message}`, "error"));
  });
  $("#dashboardList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-dashboard]");
    if (!button) return;
    const dashboard = state.workspace?.dashboards.find((item) => String(item.id) === button.dataset.deleteDashboard);
    if (!dashboard) return;
    modal("Remove dashboard?",
      `<p class="hint">Remove <b>${esc(dashboard.name)}</b>? This deletes the saved dashboard layout. Campaign and CRM data will stay unchanged.</p>`,
      `<button id="cancelDashboardDelete">Cancel</button><button id="confirmDashboardDelete" class="dashboard-remove">Remove dashboard</button>`);
    $("#cancelDashboardDelete").addEventListener("click", closeModal);
    $("#confirmDashboardDelete").addEventListener("click", async () => {
      const confirmButton = $("#confirmDashboardDelete");
      setButtonBusy(confirmButton, true);
      try {
        await api(`/api/dashboards/${dashboard.id}/delete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        closeModal(); toast("Dashboard removed"); await loadWorkspace();
      } catch (err) {
        toast(`Could not remove dashboard: ${err.message}`, "error"); setButtonBusy(confirmButton, false);
      }
    });
  });
  $("#saveRule").addEventListener("click", () => {
    const name = $("#ruleName").value.trim(); const pattern = $("#rulePattern").value.trim(); const output = $("#ruleOutput").value.trim();
    if (!name || !pattern || !output) { toast("Complete the rule name, match value, and standard value"); return; }
    saveWorkspaceObject("/api/transformations", { name, field: $("#ruleField").value, match_type: $("#ruleMatch").value, pattern, output }, "Transformation rule added")
      .then(() => { $("#ruleName").value = ""; $("#rulePattern").value = ""; $("#ruleOutput").value = ""; })
      .catch((err) => toast(`Could not add rule: ${err.message}`, "error"));
  });
  $("#saveDestination").addEventListener("click", () => {
    const name = $("#destinationName").value.trim();
    if (!name) { toast("Give the destination a name"); $("#destinationName").focus(); return; }
    const kind = $("#destinationKind").value;
    const setting = $("#destinationSetting").value.trim();
    if (kind === "email" && !setting) { toast("Enter the email recipient."); $("#destinationSetting").focus(); return; }
    const config = kind === "folder" && setting ? { folder: setting } : kind === "email" ? { recipient: setting } : {};
    const message = kind === "folder" || kind === "download" ? "Destination added"
      : kind === "email" ? "Email destination saved. Connect the send-only Gmail account if needed."
        : "Destination saved. Delivery is not implemented for this destination type yet.";
    saveWorkspaceObject("/api/destinations", { name, kind, config, enabled: true }, message)
      .then(() => { $("#destinationName").value = ""; $("#destinationSetting").value = ""; })
      .catch((err) => toast(`Could not add destination: ${err.message}`, "error"));
  });
  $("#saveJob").addEventListener("click", () => {
    const name = $("#jobName").value.trim();
    if (!name) { toast("Give the job a name"); $("#jobName").focus(); return; }
    const destination = $("#jobDestination").value;
    saveWorkspaceObject("/api/jobs", { name, job_type: $("#jobType").value, schedule: $("#jobSchedule").value, run_at: $("#jobRunAt").value, definition: destination ? { destination_id: Number(destination) } : {}, enabled: true }, "Scheduled job added")
      .then(() => { $("#jobName").value = ""; })
      .catch((err) => toast(`Could not schedule job: ${err.message}`, "error"));
  });
  $("#saveUser").addEventListener("click", () => {
    const email = $("#userEmail").value.trim();
    if (!email) { toast("Enter the user’s work email"); $("#userEmail").focus(); return; }
    const password = $("#userPassword").value;
    saveWorkspaceObject("/api/users", { email, display_name: $("#userName").value.trim(), role: $("#userRole").value, enabled: true, password }, "Portal account saved")
      .then(() => { $("#userName").value = ""; $("#userEmail").value = ""; $("#userPassword").value = ""; })
      .catch((err) => toast(`Could not save user: ${err.message}`, "error"));
  });
  $("#userList").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-toggle-user]");
    if (!button) return;
    try {
      await api("/api/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        email: button.dataset.email, display_name: button.dataset.name, role: button.dataset.role,
        enabled: button.dataset.enabled !== "true", password: "",
      }) });
      await loadWorkspace();
      toast(button.dataset.enabled === "true" ? "Account disabled and sessions revoked" : "Account enabled");
    } catch (err) { toast(`Could not update account: ${err.message}`, "error"); }
  });
  $("#logoutButton").addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); }
    finally { csrfToken = ""; location.replace("/login"); }
  });
  $("#settingsLogout").addEventListener("click", () => $("#logoutButton").click());
  $(".settings-nav").addEventListener("click", (event) => {
    const button = event.target.closest("[data-settings-panel]");
    if (button) selectSettingsPanel(button.dataset.settingsPanel);
  });
  $("#settingsInviteForm").addEventListener("submit", createSettingsInvitation);
  $("#copySettingsInvite").addEventListener("click", copySettingsInvitation);
  $("#refreshSettingsInvites").addEventListener("click", () => loadSettingsInvitations().catch((err) => setSettingsMessage("settingsInviteMessage", err.message, true)));
  $("#settingsInvitationList").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-settings-revoke-invite]");
    if (!button) return;
    if (!window.confirm("Revoke this unused invitation? Its link will stop working.")) return;
    setButtonBusy(button, true);
    try {
      await api(`/api/invitations/${button.dataset.settingsRevokeInvite}/revoke`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      setSettingsMessage("settingsInviteMessage", "Invitation revoked.");
      await loadSettingsInvitations();
    } catch (err) { setSettingsMessage("settingsInviteMessage", err.message, true); }
    finally { setButtonBusy(button, false); }
  });
  $("#profileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("#profileForm button[type='submit']");
    setButtonBusy(button, true); setSettingsMessage("profileSaveState", "Saving…");
    try {
      const saved = await api("/api/auth/profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ display_name: $("#profileName").value.trim() }) });
      renderProfileSettings({ ...saved, preferences: profilePreferences });
      setSettingsMessage("profileSaveState", "Saved");
    } catch (err) { setSettingsMessage("profileSaveState", err.message, true); }
    finally { setButtonBusy(button, false); }
  });
  $("#settingsPasswordForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const oldPassword = $("#settingsCurrentPassword").value;
    const newPassword = $("#settingsNewPassword").value;
    if (newPassword !== $("#settingsConfirmPassword").value) {
      setSettingsMessage("passwordSaveState", "The new passwords do not match.", true); $("#settingsConfirmPassword").focus(); return;
    }
    const button = $("#settingsPasswordForm button[type='submit']");
    setButtonBusy(button, true); setSettingsMessage("passwordSaveState", "Updating…");
    try {
      const result = await api("/api/auth/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password: oldPassword, new_password: newPassword }) });
      csrfToken = result.csrf_token;
      $("#settingsPasswordForm").reset();
      setSettingsMessage("passwordSaveState", "Password updated. Other sessions were signed out.");
      toast("Password changed. Other sessions were signed out.");
    } catch (err) { setSettingsMessage("passwordSaveState", err.message, true); }
    finally { setButtonBusy(button, false); }
  });
  $("#revokeOtherSessions").addEventListener("click", async () => {
    const button = $("#revokeOtherSessions"); setButtonBusy(button, true); setSettingsMessage("sessionsState", "Signing out other sessions…");
    try {
      const result = await api("/api/auth/sessions/revoke-others", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      setSettingsMessage("sessionsState", `${result.revoked} other session${result.revoked === 1 ? "" : "s"} signed out.`);
    } catch (err) { setSettingsMessage("sessionsState", err.message, true); }
    finally { setButtonBusy(button, false); }
  });
  $("#saveNotificationSettings").addEventListener("click", async () => {
    const button = $("#saveNotificationSettings");
    const notifications = {
      browser: $("#browserNotifications").checked,
      sync_failures: $("#notifySyncFailures").checked,
      sync_complete: $("#notifySyncComplete").checked,
    };
    if (notifications.browser) {
      if (!("Notification" in window)) { setSettingsMessage("notificationsSaveState", "This browser does not support notifications.", true); return; }
      if (!window.isSecureContext) { setSettingsMessage("notificationsSaveState", "Browser notifications need HTTPS or localhost.", true); return; }
      if (Notification.permission === "default") {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") { $("#browserNotifications").checked = false; setSettingsMessage("notificationsSaveState", "Notification permission was not granted.", true); return; }
      } else if (Notification.permission !== "granted") {
        $("#browserNotifications").checked = false; setSettingsMessage("notificationsSaveState", "Allow notifications in your browser settings first.", true); return;
      }
    }
    setButtonBusy(button, true); setSettingsMessage("notificationsSaveState", "Saving…");
    try {
      const saved = await api("/api/auth/profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notifications }) });
      profilePreferences = saved.preferences;
      $("#browserNotifications").checked = profilePreferences.notifications.browser;
      setSettingsMessage("notificationsSaveState", "Notification preferences saved.");
    } catch (err) { setSettingsMessage("notificationsSaveState", err.message, true); }
    finally { setButtonBusy(button, false); }
  });
  $("#saveAppearance").addEventListener("click", async () => {
    const button = $("#saveAppearance"); setButtonBusy(button, true); setSettingsMessage("appearanceSaveState", "Saving…");
    try {
      const theme = $('input[name="themePreference"]:checked')?.value || "system";
      const saved = await api("/api/auth/profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ theme, accent: draftAccent }) });
      profilePreferences = saved.preferences; renderAppearanceSettings(saved.preferences);
      setSettingsMessage("appearanceSaveState", "Appearance saved for your account.");
    } catch (err) { setSettingsMessage("appearanceSaveState", err.message, true); }
    finally { setButtonBusy(button, false); }
  });
  $$('input[name="themePreference"]').forEach((input) => input.addEventListener("change", previewAppearance));
  $$('input[name="accentPreference"]').forEach((input) => input.addEventListener("change", () => {
    draftAccent = input.value;
    $("#accentHex").value = draftAccent || "#0F5262";
    $("#accentColorPicker").value = draftAccent || "#0f5262";
    $(".accent-custom")?.classList.remove("selected");
    $$('input[name="accentPreference"]').forEach((option) => option.closest(".accent-option")?.classList.toggle("selected", option.checked));
    $("#accentSaveState").textContent = draftAccent ? "Custom accent colour selected." : "Portal default accent is selected.";
    $("#accentSaveState").classList.remove("error");
    previewAppearance();
  }));
  $("#accentColorPicker").addEventListener("input", (event) => {
    draftAccent = event.target.value.toUpperCase();
    $("#accentHex").value = draftAccent;
    $$('input[name="accentPreference"]').forEach((option) => { option.checked = false; option.closest(".accent-option")?.classList.remove("selected"); });
    $(".accent-custom")?.classList.add("selected");
    $("#accentSaveState").textContent = "Custom accent colour selected.";
    $("#accentSaveState").classList.remove("error");
    previewAppearance();
  });
  $("#accentHex").addEventListener("input", (event) => {
    const value = event.target.value.trim();
    if (!/^#[0-9a-f]{6}$/i.test(value)) {
      $("#accentSaveState").textContent = "Enter a hex colour in #RRGGBB format.";
      $("#accentSaveState").classList.add("error");
      return;
    }
    $("#accentSaveState").classList.remove("error");
    draftAccent = value.toUpperCase();
    $("#accentColorPicker").value = draftAccent;
    $$('input[name="accentPreference"]').forEach((option) => { option.checked = false; option.closest(".accent-option")?.classList.remove("selected"); });
    $(".accent-custom")?.classList.add("selected");
    $("#accentSaveState").textContent = "Custom accent colour selected.";
    previewAppearance();
  });
  $("#resetAppearance").addEventListener("click", () => {
    draftAccent = "";
    $$('input[name="themePreference"]').forEach((input) => { input.checked = input.value === "system"; });
    $$('input[name="accentPreference"]').forEach((input) => {
      input.checked = input.value === "";
      input.closest(".accent-option")?.classList.toggle("selected", input.checked);
    });
    $(".accent-custom")?.classList.remove("selected");
    $("#accentHex").value = "#0F5262"; $("#accentColorPicker").value = "#0f5262";
    $("#accentSaveState").textContent = "Default appearance selected. Save changes to apply it.";
    previewAppearance();
  });
  $("#cancelAppearance").addEventListener("click", () => {
    renderAppearanceSettings(profilePreferences);
    setSettingsMessage("appearanceSaveState", "Changes discarded.");
    $("#accentSaveState").textContent = profilePreferences.accent ? "Saved custom accent colour." : "Portal default accent is selected.";
    $("#accentSaveState").classList.remove("error");
  });
  $("#gmailConfigure").addEventListener("click", async () => {
    try {
      if (!PROVIDERS.length) PROVIDERS = await api("/api/providers");
      const existing = gmailStatus?.connection_id ? await api(`/api/connections/${gmailStatus.connection_id}`) : null;
      openForm("gmail", existing);
    } catch (err) { toast(`Could not open Gmail setup: ${err.message}`, "error"); }
  });
  $("#gmailConnect").addEventListener("click", async () => {
    if (!gmailStatus?.connection_id) return;
    const popup = window.open("about:blank", "portal-gmail-connect");
    if (!popup) { toast("Allow pop-ups for this portal, then try Connect Google account again.", "error"); return; }
    try {
      popup.location = `/oauth/start?connection_id=${gmailStatus.connection_id}`;
      setButtonBusy($("#gmailConnect"), true);
      for (let attempt = 0; attempt < 100; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1800));
        const fresh = await api("/api/auth/gmail");
        if (fresh.connected) { await loadGmailSettings(); toast("Gmail connected with send-only access."); return; }
        if (popup.closed) break;
      }
      await loadGmailSettings();
    } catch (err) { toast(`Could not connect Gmail: ${err.message}`, "error"); }
    finally { setButtonBusy($("#gmailConnect"), false); }
  });
  $("#gmailTest").addEventListener("click", async () => {
    const button = $("#gmailTest"); setButtonBusy(button, true);
    try {
      const result = await api("/api/auth/gmail/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      toast(`Test email sent to ${result.recipient}.`);
    } catch (err) { toast(`Test email failed: ${err.message}`, "error"); }
    finally { setButtonBusy(button, false); }
  });
  $("#gmailDisconnect").addEventListener("click", () => {
    modal("Disconnect Gmail?", `<p class="hint">This removes the sender and saved tokens from this portal. It does not revoke the Google grant by itself. To revoke Google's access too, use <a href="https://myaccount.google.com/connections" target="_blank" rel="noopener noreferrer">Google Account connections</a>.</p>`, `<button id="cancelGmailDisconnect">Cancel</button><button id="confirmGmailDisconnect" class="danger">Disconnect</button>`);
    $("#cancelGmailDisconnect").addEventListener("click", closeModal);
    $("#confirmGmailDisconnect").addEventListener("click", async () => {
      const button = $("#confirmGmailDisconnect"); setButtonBusy(button, true);
      try {
        await api("/api/auth/gmail/disconnect", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        closeModal(); await loadGmailSettings(); toast("Gmail removed from this portal.");
      } catch (err) { toast(`Could not disconnect Gmail: ${err.message}`, "error"); }
      finally { setButtonBusy(button, false); }
    });
  });
  $("#changePasswordButton").addEventListener("click", () => {
    modal("Change password", `<div class="preset-form"><label>Current password<input id="currentPassword" type="password" autocomplete="current-password"></label><label>New password<input id="newPassword" type="password" autocomplete="new-password" minlength="14"><small>Use at least 14 characters.</small></label></div>`, `<button id="cancelPassword">Cancel</button><button id="savePassword" class="primary">Change password</button>`);
    $("#cancelPassword").addEventListener("click", closeModal);
    $("#savePassword").addEventListener("click", async () => {
      try {
        const result = await api("/api/auth/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password: $("#currentPassword").value, new_password: $("#newPassword").value }) });
        csrfToken = result.csrf_token;
        closeModal(); toast("Password changed. Other sessions were signed out.");
      } catch (err) { toast(`Could not change password: ${err.message}`, "error"); }
    });
  });
  $("#jobList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-run-job]");
    if (!button) return;
    setButtonBusy(button, true);
    api(`/api/jobs/${button.dataset.runJob}/run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then((result) => { toast(result.message); return loadWorkspace(); })
      .catch((err) => toast(`Could not run job: ${err.message}`, "error"))
      .finally(() => setButtonBusy(button, false));
  });
  $("#runAutomation").addEventListener("click", async () => {
    await syncLatest($("#runAutomation"));
  });
  $("#automationType").addEventListener("change", renderAutomationTypeSummary);
  $("#createAutomation").addEventListener("click", openAutomationBuilder);
  $("#automationRuleList").addEventListener("click", (ev) => {
    const button = ev.target.closest("[data-delete-automation]");
    if (!button) return;
    const rules = getAutomationRules().filter((rule) => rule.id !== button.dataset.deleteAutomation);
    persistAutomationRules(rules).then(() => { renderAutomationRules(); toast("Automation workflow removed"); })
      .catch((err) => toast(`Could not remove workflow: ${err.message}`, "error"));
  });
  renderAutomationTypeSummary();
  $("#refreshInsights").addEventListener("click", refreshPerformanceInsights);
  $("#configureAutomation").addEventListener("click", () => openAutomationSettings().catch((err) => toast(`Could not open schedule: ${err.message}`)));
  $("#downloadAutomation").addEventListener("click", downloadAutomationReport);
  $("#openMcpSetup").addEventListener("click", () => openMcpSetup().catch((err) => toast(`Could not open MCP setup: ${err.message}`)));
  const metricPresets = {
    cac: { name: "CAC", a: "Amount spent", b: "Total sales or procurements", numerator: "spend", denominator: "crm_sales", format: "currency", help: "CAC = total amount spent ÷ total sales or procurements." },
    cpl: { name: "CPL", a: "Amount spent", b: "Total leads", numerator: "spend", denominator: "crm_leads", format: "currency", help: "CPL = total amount spent ÷ total leads." },
    cpc: { name: "CPC", a: "Amount spent", b: "Total clicks", numerator: "spend", denominator: "clicks", format: "currency", help: "CPC = total amount spent ÷ total clicks." },
    roas: { name: "CRM ROAS", a: "CRM revenue", b: "Amount spent", numerator: "crm_revenue", denominator: "spend", format: "number", suffix: "×", needsCrmRevenue: true, help: "CRM ROAS = campaign-attributed CRM revenue ÷ ad spend. Revenue must be mapped to campaign-tagged CRM leads." },
    platform_roas: { name: "Platform ROAS", a: "Platform conversion value", b: "Amount spent", numerator: "conversion_value", denominator: "spend", format: "number", suffix: "×", help: "Platform ROAS = the ad platform's conversion value ÷ ad spend." },
    revenue_per_lead: { name: "CRM revenue per lead", a: "CRM revenue", b: "Total leads", numerator: "crm_revenue", denominator: "crm_leads", format: "currency", needsCrmRevenue: true, help: "CRM revenue per lead = campaign-attributed CRM revenue ÷ CRM leads." },
    budget_pacing: { name: "Budget pacing", a: "Spend on budgeted days", b: "Budget for those days", numerator: "budgeted_spend", denominator: "period_budget", format: "percent", help: "Budget pacing = spend on days with a recorded budget ÷ the sum of those daily budgets. Check coverage before interpreting it." },
    ltv_cac: { name: "LTV : CAC", a: "Lifetime value per customer", b: "CAC", numerator: "ltv", denominator: "cac", format: "number", suffix: "×", help: "LTV : CAC = lifetime value per customer ÷ customer acquisition cost." },
  };
  let selectedMetricPreset = "cac";
  let metricCampaignRows = [];
  let selectedMetricCampaignKey = "";
  let metricCampaignRequestId = 0;
  let metricResultRequestId = 0;
  const metricCampaignKey = (row) => JSON.stringify([
    row.source || "", row.account_id || "", row.campaign_id || row.campaign || "",
  ]);
  const totalsForMetricPreview = async () => {
    const rows = await fetchAllCampaigns(state.campaignSrc);
    const currencies = [...new Set(rows.map((row) => row.currency).filter(Boolean))];
    if (rows.some((row) => !row.currency) || currencies.length > 1) {
      throw new Error("Paid accounts use mixed or missing currencies. Select one account or fix currency settings before calculating a blended total.");
    }
    const totals = {};
    let revenueTotal = 0;
    let revenueRows = 0;
    totals.budgeted_days = 0;
    totals.reported_days = 0;
    totals.selected_days = 0;
    for (const row of rows) {
      totals.budgeted_days += Number(row.budgeted_days || 0);
      totals.reported_days += Number(row.reported_days || 0);
      totals.selected_days += Number(row.selected_days || 0);
      METRIC_FIELDS.forEach(([id]) => {
        if (id === "crm_revenue") {
          if (row.crm_revenue != null && Number(row.crm_revenue) !== 0) {
            revenueTotal += Number(row.crm_revenue); revenueRows += 1;
          }
          return;
        }
        totals[id] = (totals[id] || 0) + Number(row[id] || 0);
      });
    }
    totals.crm_revenue_complete = rows.length > 0 && revenueRows === rows.length;
    totals.crm_revenue = totals.crm_revenue_complete ? revenueTotal : null;
    totals.crm_match_complete = rows.length > 0 && rows.every((row) => Boolean(row.crm_leads_source));
    totals.budget_coverage_percent = totals.spend > 0
      ? totals.budgeted_spend / totals.spend * 100 : null;
    totals.currency = currencies[0] || state.currency;
    return totals;
  };
  const portalValuesForPreset = (preset, totals) => {
    if (selectedMetricPreset === "budget_pacing") {
      return [totals.budgeted_spend ?? null, totals.period_budget ?? null];
    }
    if (selectedMetricPreset === "ltv_cac") {
      const cac = totals.crm_sales ? totals.spend / totals.crm_sales : 0;
      return [null, cac];
    }
    if (preset.needsCrmRevenue && (totals.crm_revenue == null || totals.crm_revenue_complete === false)) {
      return [null, totals[preset.denominator] ?? null];
    }
    if (["crm_leads", "crm_sales"].includes(preset.denominator)
        && (totals.crm_match_complete === false
          || (Object.hasOwn(totals, "crm_leads_source") && !totals.crm_leads_source))) {
      return [totals[preset.numerator] ?? null, null];
    }
    return [totals[preset.numerator] ?? null, totals[preset.denominator] ?? null];
  };
  const fillMetricFromCampaign = () => {
    const selected = $("#metricCampaignSelect").value;
    if (selected === "") return false;
    const index = Number(selected);
    if (!Number.isInteger(index) || !metricCampaignRows[index]) return false;
    const row = metricCampaignRows[index];
    selectedMetricCampaignKey = metricCampaignKey(row);
    const preset = metricPresets[selectedMetricPreset];
    const values = portalValuesForPreset(preset, row);
    const metricCurrency = preset.needsCrmRevenue ? row.crm_revenue_currency : row.currency;
    if (preset.needsCrmRevenue && row.crm_revenue != null && metricCurrency
        && row.currency && metricCurrency !== row.currency) {
      $("#metricForm").dataset.metricCurrencyMismatch = `${metricCurrency} and ${row.currency}`;
    } else {
      delete $("#metricForm").dataset.metricCurrencyMismatch;
    }
    $("#metricForm").dataset.metricCurrency = metricCurrency || row.currency || state.currency;
    $("#metricInputMode").value = "manual";
    $("#metricForm").dataset.inputMode = "manual";
    $("#metricValueA").value = values[0] ?? "";
    $("#metricValueB").value = values[1] ?? "";
    $("#metricForm").dataset.autofilledCampaign = "true";
    return true;
  };
  const metricSourceName = (source) => (PAID_PLATFORM_META[source]?.label
    || ({ google_ads: "Google Ads", meta_ads: "Meta Ads" }[source])
    || String(source || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()));
  const renderMetricCampaignOptions = (query = "") => {
    const normalized = query.trim().toLowerCase();
    const selected = $("#metricCampaignSelect").value;
    const matches = metricCampaignRows.map((row, index) => ({ row, index })).filter(({ row }) =>
      `${row.campaign || ""} ${metricSourceName(row.source)}`.toLowerCase().includes(normalized));
    $("#metricCampaignOptions").innerHTML = matches.length ? matches.map(({ row, index }) => {
      const sourceName = metricSourceName(row.source);
      const badge = PAID_PLATFORM_META[row.source]?.badge || "";
      return `<button type="button" role="option" aria-selected="${selected === String(index)}" class="metric-campaign-option ${selected === String(index) ? "on" : ""}" data-metric-campaign="${index}"><span class="metric-campaign-name" title="${esc(row.campaign || "Unnamed campaign")}">${esc(row.campaign || "Unnamed campaign")}</span><span class="metric-platform-badge ${badge}">${esc(sourceName)}</span></button>`;
    }).join("") : `<div class="empty compact">No campaigns match your search.</div>`;
  };
  const loadMetricCampaigns = async () => {
    const select = $("#metricCampaignSelect");
    const requestId = ++metricCampaignRequestId;
    const start = state.start, end = state.end, source = state.campaignSrc;
    const previous = metricCampaignRows[Number(select.value)];
    const previousKey = selectedMetricCampaignKey || (select.value !== "" && previous ? metricCampaignKey(previous) : "");
    metricCampaignRows = [];
    select.innerHTML = `<option value="">Loading campaigns…</option>`;
    select.disabled = true;
    $("#metricCampaignToggle").disabled = true;
    $("#metricCampaignLabel").textContent = "Loading campaigns…";
    if (previousKey || $("#metricInputMode").value === "portal") {
      if (previousKey) {
        $("#metricValueA").value = "";
        $("#metricValueB").value = "";
      }
      $("#metricCalculatorResult").textContent = "Updating campaign for the selected dates…";
    }
    try {
      const rows = await fetchAllCampaigns(source);
      if (requestId !== metricCampaignRequestId || start !== state.start
          || end !== state.end || source !== state.campaignSrc) return;
      metricCampaignRows = rows;
      select.innerHTML = `<option value="">Choose a campaign…</option>${metricCampaignRows.map((row, index) => {
        const sourceName = metricSourceName(row.source);
        const source = sourceName ? ` · ${sourceName}` : "";
        return `<option value="${index}">${esc(row.campaign || "Unnamed campaign")}${esc(source)}</option>`;
      }).join("")}`;
      select.disabled = !metricCampaignRows.length;
      $("#metricCampaignToggle").disabled = !metricCampaignRows.length;
      const selectedIndex = previousKey
        ? metricCampaignRows.findIndex((row) => metricCampaignKey(row) === previousKey) : -1;
      if (selectedIndex >= 0) {
        select.value = String(selectedIndex);
        $("#metricCampaignLabel").textContent = metricCampaignRows[selectedIndex].campaign || "Unnamed campaign";
        fillMetricFromCampaign();
        updateMetricResult();
      } else {
        selectedMetricCampaignKey = "";
        delete $("#metricForm").dataset.autofilledCampaign;
        $("#metricCampaignLabel").textContent = metricCampaignRows.length
          ? "Choose a campaign…" : "No campaigns in these dates";
        if (previousKey) $("#metricCalculatorResult").textContent = "That campaign has no rows in the selected dates.";
        else if ($("#metricInputMode").value === "portal") await updateMetricResult();
      }
      renderMetricCampaignOptions();
    } catch (_) {
      if (requestId !== metricCampaignRequestId || start !== state.start
          || end !== state.end || source !== state.campaignSrc) return;
      metricCampaignRows = [];
      selectedMetricCampaignKey = "";
      delete $("#metricForm").dataset.autofilledCampaign;
      select.innerHTML = `<option value="">Campaigns could not be loaded</option>`;
      select.disabled = true;
      $("#metricCampaignToggle").disabled = true;
      $("#metricCampaignLabel").textContent = "Campaigns could not be loaded";
    }
  };
  const updateMetricResult = async () => {
    const requestId = ++metricResultRequestId;
    const start = state.start, end = state.end, source = state.campaignSrc;
    const node = $("#metricCalculatorResult");
    const preset = metricPresets[selectedMetricPreset];
    const portalMode = $("#metricInputMode").value === "portal";
    node.classList.remove("is-ready");
    if (preset.needsCrmRevenue && $("#metricForm").dataset.metricCurrencyMismatch) {
      node.innerHTML = `<span>${esc(preset.name)}</span><small>CRM revenue and ad spend use different currencies: ${esc($("#metricForm").dataset.metricCurrencyMismatch)}. Enter values in one currency manually.</small>`;
      return;
    }
    let rawA = $("#metricValueA").value.trim(), rawB = $("#metricValueB").value.trim();
    let a = rawA === "" ? null : Number(rawA), b = rawB === "" ? null : Number(rawB);
    let metricCurrency = $("#metricForm").dataset.metricCurrency || state.currency;
    let budgetBasis = $("#metricForm").dataset.autofilledCampaign === "true"
      ? metricCampaignRows[Number($("#metricCampaignSelect").value)] : null;
    if (portalMode) {
      node.textContent = "Calculating from the current portal date range…";
      try {
        const totals = await totalsForMetricPreview();
        if (requestId !== metricResultRequestId || start !== state.start
            || end !== state.end || source !== state.campaignSrc) return;
        if (preset.needsCrmRevenue && totals.currency !== state.currency) {
          throw new Error(`CRM revenue uses ${state.currency}, but ad spend uses ${totals.currency}. Configure a currency conversion rule before using CRM revenue metrics.`);
        }
        [a, b] = portalValuesForPreset(preset, totals);
        budgetBasis = totals;
        metricCurrency = preset.needsCrmRevenue ? state.currency : totals.currency;
      } catch (error) {
        node.textContent = error.message || "Portal totals could not be loaded. Enter values manually instead.";
        return;
      }
    }
    if (a == null || b == null || !Number.isFinite(a) || !Number.isFinite(b) || b === 0) {
      const missing = preset.needsCrmRevenue && (a == null || !Number.isFinite(a))
        ? "Campaign-attributed CRM revenue is unavailable. Map revenue to CRM leads and include their campaign tags."
        : ["crm_leads", "crm_sales"].includes(preset.denominator) && b == null
          ? "Campaign-matched CRM outcomes are unavailable. Check attribution or enter a verified value manually."
        : selectedMetricPreset === "ltv_cac" && portalMode
          ? "LTV is not stored in the portal. Enter LTV and CAC manually."
          : b === 0 ? `${preset.b} is zero, so this formula has no valid denominator.`
            : `Enter ${preset.a.toLowerCase()} and a non-zero ${preset.b.toLowerCase()}.`;
      node.innerHTML = `<span>${esc(preset.name)}</span><b>Not calculated</b><small>${esc(missing)}</small>`;
      return;
    }
    const value = a / b;
    const formatted = preset.format === "number"
      ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value)
      : metricFormatter(preset.format, value, metricCurrency);
    const display = `${formatted}${preset.suffix || ""}`;
    const budgetCoverage = selectedMetricPreset === "budget_pacing" && budgetBasis
      && (Number(budgetBasis.budgeted_days || 0) < Number(budgetBasis.selected_days || 0)
        || (budgetBasis.budget_coverage_percent != null
          && Number(budgetBasis.budget_coverage_percent) < 100))
      ? ` Provisional: budgets recorded for ${nf.format(Number(budgetBasis.budgeted_days || 0))} of ${nf.format(Number(budgetBasis.selected_days || 0))} campaign-days; ${nf1.format(Number(budgetBasis.budget_coverage_percent || 0))}% of spend is covered.`
      : "";
    node.classList.add("is-ready");
    node.innerHTML = `<span>${esc(preset.name)} result</span><b>${esc(display)}</b><small>${esc(preset.a)} ${nf.format(a)} ÷ ${esc(preset.b)} ${nf.format(b)}.${esc(budgetCoverage)}</small>`;
  };
  const applyMetricPreset = (presetId) => {
    selectedMetricPreset = presetId;
    const preset = metricPresets[presetId];
    $("#metricValueALabel").textContent = preset.a;
    $("#metricValueBLabel").textContent = preset.b;
    $("#metricValueA").placeholder = `Enter ${preset.a.toLowerCase()}`;
    $("#metricValueB").placeholder = `Enter ${preset.b.toLowerCase()}`;
    $("#metricFormulaHelp").textContent = preset.help;
    $("#metricValueA").value = "";
    $("#metricValueB").value = "";
    fillMetricFromCampaign();
    updateMetricResult();
  };
  $("#metricPresetSelect").addEventListener("change", (event) => applyMetricPreset(event.target.value));
  const closeMetricCampaignMenu = () => {
    $("#metricCampaignMenu").hidden = true;
    $("#metricCampaignToggle").setAttribute("aria-expanded", "false");
  };
  $("#metricCampaignToggle").addEventListener("click", () => {
    const menu = $("#metricCampaignMenu"), open = menu.hidden;
    menu.hidden = !open;
    $("#metricCampaignToggle").setAttribute("aria-expanded", String(open));
    if (open) { $("#metricCampaignSearch").value = ""; renderMetricCampaignOptions(); $("#metricCampaignSearch").focus(); }
  });
  $("#metricCampaignSearch").addEventListener("input", (event) => renderMetricCampaignOptions(event.target.value));
  $("#metricCampaignOptions").addEventListener("click", (event) => {
    const option = event.target.closest("[data-metric-campaign]");
    if (!option) return;
    $("#metricCampaignSelect").value = option.dataset.metricCampaign;
    const row = metricCampaignRows[Number(option.dataset.metricCampaign)];
    $("#metricCampaignLabel").textContent = row?.campaign || "Choose a campaign…";
    closeMetricCampaignMenu();
    if (fillMetricFromCampaign()) updateMetricResult();
  });
  document.addEventListener("click", (event) => { if (!event.target.closest(".metric-campaign-picker")) closeMetricCampaignMenu(); });
  $("#metricInputMode").addEventListener("change", () => {
    $("#metricForm").dataset.inputMode = $("#metricInputMode").value;
    updateMetricResult();
  });
  ["#metricValueA", "#metricValueB"].forEach((selector) => $(selector).addEventListener("input", () => {
    delete $("#metricForm").dataset.autofilledCampaign;
    updateMetricResult();
  }));
  $("#metricForm").addEventListener("submit", (event) => {
    event.preventDefault();
    updateMetricResult();
  });
  $("#metricForm").dataset.inputMode = "manual";
  applyMetricPreset($("#metricPresetSelect").value);
  refreshMetricCampaigns = loadMetricCampaigns;
  $("#customMetrics").addEventListener("click", (ev) => {
    const view = ev.target.closest("[data-view-metric]");
    if (view) { switchTab("paid"); return; }
    const button = ev.target.closest("[data-delete-metric]"); if (!button) return;
    const id = button.dataset.deleteMetric;
    state.customMetrics = state.customMetrics.filter((metric) => metric.id !== id);
    const index = PAID_COLUMNS.findIndex((column) => column.id === id); if (index >= 0) PAID_COLUMNS.splice(index, 1);
    state.campaignColumns.delete(id); saveCustomMetrics(); renderCustomMetrics(); renderColumnMenu();
    saveUiPreferenceRaw("campaignColumns", JSON.stringify([...state.campaignColumns]));
  });
  $("#savedViews").addEventListener("click", (ev) => {
    const open = ev.target.closest("[data-view]");
    if (open) { const view = getSavedViews().find((item) => item.id === open.dataset.view); if (view) applySavedView(view); return; }
    const remove = ev.target.closest("[data-delete-view]");
    if (remove) { saveUiPreferenceRaw("savedPortalViews", JSON.stringify(getSavedViews().filter((item) => item.id !== remove.dataset.deleteView))); renderSavedViews(); }
  });
  $("#modalClose").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (ev) => {
    if (ev.target.id === "modal") closeModal();   // click the backdrop to dismiss
  });
  document.addEventListener("keydown", (ev) => {
    trapDialogFocus(ev);
    if (ev.key === "Escape" && !$("#modal").hidden) closeModal();
  });

  $("#syncAll").addEventListener("click", async () => {
    await syncLatest($("#syncAll"));
  });

  let rt;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => { if (activeTab === "overview") loadOverview(); }, 200);
  });

  if (loaderFor(location.hash.slice(1))) syncFromHash(true);
  else refresh();
}

boot();
