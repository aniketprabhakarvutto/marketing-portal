const form = document.querySelector("#authForm");
const message = document.querySelector("#message");
const password = document.querySelector("#password");
const togglePassword = document.querySelector("#togglePassword");
let setup = false;

togglePassword.addEventListener("click", () => {
  const isVisible = password.type === "text";
  password.type = isVisible ? "password" : "text";
  togglePassword.setAttribute("aria-label", isVisible ? "Show password" : "Hide password");
  togglePassword.setAttribute("aria-pressed", String(!isVisible));
  password.focus();
});

async function init() {
  try {
    const response = await fetch("/api/auth/status", { cache: "no-store", credentials: "same-origin" });
    const status = await response.json();
    if (!response.ok) throw new Error(status.error || "Could not check sign-in status.");
    if (!status.setup_required) return;
    if (!status.can_setup) {
      document.querySelector("#title").textContent = "Administrator setup required";
      document.querySelector("#hint").textContent = "Open this portal on the computer where it runs and visit localhost to create the first administrator.";
      form.hidden = true;
      document.querySelector("#note").hidden = true;
      return;
    }
    setup = true;
    document.querySelector("#eyebrow").textContent = "One-time setup";
    document.querySelector("#title").textContent = "Create the administrator account";
    document.querySelector("#hint").textContent = "This setup is available only on the portal computer and closes after the first account is created.";
    document.querySelector("#nameField").hidden = false;
    document.querySelector("#displayName").required = true;
    password.minLength = 14;
    password.autocomplete = "new-password";
    document.querySelector("#submit").textContent = "Create administrator";
    document.querySelector("#note").textContent = "Use at least 14 characters. Save the password securely.";
  } catch (_) {
    message.textContent = "The portal could not check sign-in status. Reload this page.";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.textContent = "";
  const button = document.querySelector("#submit");
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = setup ? "Creating account..." : "Signing in...";
  button.classList.add("is-busy");
  form.setAttribute("aria-busy", "true");
  let navigating = false;
  try {
    const body = {
      email: document.querySelector("#email").value,
      password: password.value,
    };
    if (setup) body.display_name = document.querySelector("#displayName").value;
    const response = await fetch(setup ? "/api/auth/setup" : "/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Sign-in failed.");
    navigating = true;
    button.textContent = "Opening portal...";
    location.replace(`/${location.hash}`);
  } catch (error) {
    message.textContent = error.message || "Sign-in failed.";
  } finally {
    if (!navigating) {
      button.disabled = false;
      button.textContent = originalLabel;
      button.classList.remove("is-busy");
      form.removeAttribute("aria-busy");
    }
  }
});

init();
