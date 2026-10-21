const form = document.querySelector("#acceptForm");
const message = document.querySelector("#acceptMessage");
const tokenParams = new URLSearchParams(location.hash.slice(1));
const invitationToken = tokenParams.get("token") || "";

// The fragment is not sent in the HTTP request. Remove it from browser history
// immediately after reading it so it is not left in the address bar/history.
history.replaceState(null, "", location.pathname);

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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.textContent = "";
  if (!invitationToken) {
    message.textContent = "This invite link is missing its token. Ask the portal administrator for a new link.";
    return;
  }
  const password = document.querySelector("#newPassword").value;
  const confirmPassword = document.querySelector("#confirmPassword").value;
  if (password !== confirmPassword) {
    message.textContent = "The passwords do not match.";
    document.querySelector("#confirmPassword").focus();
    return;
  }
  const button = document.querySelector("#acceptInvite");
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Creating account...";
  form.setAttribute("aria-busy", "true");
  let navigating = false;
  try {
    const response = await fetch("/api/invitations/accept", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: invitationToken,
        email: document.querySelector("#acceptEmail").value,
        display_name: document.querySelector("#acceptName").value,
        password,
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "Could not accept this invitation.");
    navigating = true;
    button.textContent = "Opening portal...";
    location.replace("/");
  } catch (error) {
    message.textContent = error.message || "Could not accept this invitation.";
  } finally {
    if (!navigating) { button.disabled = false; button.textContent = label; form.removeAttribute("aria-busy"); }
  }
});
