const nav = document.querySelector(".main-nav");
const menuButton = document.querySelector(".menu-button");

if (menuButton && nav) {
  menuButton.addEventListener("click", () => {
    const isOpen = nav.classList.toggle("open");
    menuButton.setAttribute("aria-expanded", String(isOpen));
  });
}

const current = document.body.dataset.page;
if (current) {
  document.querySelectorAll("[data-nav]").forEach((link) => {
    if (link.dataset.nav === current) {
      link.classList.add("active");
    }
  });
}

document.querySelectorAll("pre").forEach((pre) => {
  const isEnglish = document.documentElement.lang && document.documentElement.lang.startsWith("en");
  const idleText = isEnglish ? "Copy" : "复制";
  const doneText = isEnglish ? "Copied" : "已复制";
  const failText = isEnglish ? "Failed" : "失败";
  const button = document.createElement("button");
  button.className = "copy-code";
  button.type = "button";
  button.textContent = idleText;
  button.addEventListener("click", async () => {
    const code = pre.querySelector("code")?.innerText || pre.innerText;
    try {
      await navigator.clipboard.writeText(code.replace(idleText, "").trim());
      button.textContent = doneText;
      window.setTimeout(() => { button.textContent = idleText; }, 1400);
    } catch {
      button.textContent = failText;
      window.setTimeout(() => { button.textContent = idleText; }, 1400);
    }
  });
  pre.appendChild(button);
});
