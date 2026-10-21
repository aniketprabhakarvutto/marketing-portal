const $ = (selector) => document.querySelector(selector);
let csrfToken = "";

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: { ...(options.headers || {}), ...(options.method ? { "X-CSRF-Token": csrfToken } : {}) },
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
  return result;
}

function showMessage(message, error = false) {
  const node = $("#inviteMessage");
  node.textContent = message;
  node.classList.toggle("is-error", error);
}

function formatDate(value) {
  const parsed = new Date(`${value.replace(" ", "T")}Z`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function renderInvites(invites) {
  const list = $("#inviteList");
  list.replaceChildren();
  if (!invites.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No pending invitations.";
    list.append(empty);
    return;
  }
  for (const invite of invites) {
    const row = document.createElement("article");
    row.className = "invite-item";
    const details = document.createElement("div");
    details.className = "invite-details";
    const identity = document.createElement("b");
    identity.textContent = invite.display_name ? `${invite.display_name} · ${invite.email}` : invite.email;
    const metadata = document.createElement("small");
    metadata.textContent = `Viewer · ${invite.status === "expired" ? "Expired" : "Expires"} ${formatDate(invite.expires_at)}`;
    if (invite.status === "expired") metadata.classList.add("invite-status", "expired");
    else metadata.classList.add("invite-status");
    details.append(identity, metadata);
    const revoke = document.createElement("button");
    revoke.type = "button";
    revoke.className = "revoke-button";
    revoke.textContent = "Revoke";
    revoke.setAttribute("aria-label", `Revoke invitation for ${invite.email}`);
    revoke.addEventListener("click", async () => {
      if (!window.confirm(`Revoke the invitation for ${invite.email}?`)) return;
      revoke.disabled = true;
      try {
        await request(`/api/invitations/${invite.id}/revoke`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
        });
        await loadInvites();
      } catch (error) {
        revoke.disabled = false;
        showMessage(error.message, true);
      }
    });
    row.append(details, revoke);
    list.append(row);
  }
}

async function loadInvites() {
  const invites = await request("/api/invitations");
  renderInvites(invites);
}

async function initialize() {
  try {
    const auth = await request("/api/auth/me");
    csrfToken = auth.csrf_token;
    if (auth.user?.role !== "admin") throw new Error("Administrator access is required.");
    await loadInvites();
  } catch (error) {
    $("#inviteList").textContent = error.message;
    $("#inviteList").classList.add("empty");
    if (error.message.toLowerCase().includes("sign in")) {
      $("#inviteList").textContent = "Sign in as a portal administrator to manage invitations.";
    }
  }
}

$("#inviteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#createInvite");
  const form = event.currentTarget;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Creating invitation...";
  form.setAttribute("aria-busy", "true");
  showMessage("");
  $("#inviteCreated").hidden = true;
  try {
    const result = await request("/api/invitations", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("#inviteEmail").value, display_name: $("#inviteName").value }),
    });
    const link = `${location.origin}/invite#token=${encodeURIComponent(result.token)}`;
    $("#inviteLink").value = link;
    $("#inviteExpiry").textContent = `Expires ${formatDate(result.expires_at)}`;
    $("#inviteCreated").hidden = false;
    showMessage("Viewer invitation created. Copy the link and share it through your approved channel.");
    await loadInvites();
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = label;
    form.removeAttribute("aria-busy");
  }
});

$("#copyInvite").addEventListener("click", async () => {
  const input = $("#inviteLink");
  try {
    await navigator.clipboard.writeText(input.value);
    $("#copyInvite").textContent = "Copied";
  } catch (_) {
    input.focus();
    input.select();
    showMessage("Clipboard access is blocked. Copy the selected link manually.", true);
  }
});

$("#refreshInvites").addEventListener("click", () => loadInvites().catch((error) => showMessage(error.message, true)));
initialize();
