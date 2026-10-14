// Small progressive enhancements. Every page works without JavaScript.
(function () {
  "use strict";
  document.documentElement.classList.add("js");

  // --- Sticky header shadow -------------------------------------------------
  var header = document.querySelector(".site-header");
  if (header) {
    var onScroll = function () { header.classList.toggle("is-scrolled", window.scrollY > 8); };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  // --- Light / dark theme toggle ----------------------------------------------
  // No saved choice = follow the device setting. A click saves the opposite of what's showing.
  var root = document.documentElement;
  var themeBtn = document.querySelector(".theme-toggle");
  var darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var isDark = function () {
    var t = root.getAttribute("data-theme");
    return t ? t === "dark" : !!(darkQuery && darkQuery.matches);
  };
  var syncThemeButton = function () {
    if (themeBtn) themeBtn.setAttribute("aria-label", isDark() ? "Switch to light mode" : "Switch to dark mode");
  };
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var next = isDark() ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("datos-theme", next); } catch (e) {}
      syncThemeButton();
    });
    if (darkQuery && darkQuery.addEventListener) darkQuery.addEventListener("change", syncThemeButton);
    syncThemeButton();
  }

  // --- Mobile navigation drawer ---------------------------------------------
  var toggle = document.querySelector(".menu-toggle");
  var drawer = document.getElementById("nav-drawer");
  if (toggle && drawer) {
    var setOpen = function (open) {
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      drawer.classList.toggle("is-open", open);
      drawer.toggleAttribute("inert", !open);
      document.body.classList.toggle("nav-open", open);
      toggle.querySelector(".icon-open").toggleAttribute("hidden", open);
      toggle.querySelector(".icon-close").toggleAttribute("hidden", !open);
    };
    drawer.setAttribute("inert", "");
    toggle.addEventListener("click", function () { setOpen(toggle.getAttribute("aria-expanded") !== "true"); });
    drawer.addEventListener("click", function (e) { if (e.target.closest("a")) setOpen(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && drawer.classList.contains("is-open")) { setOpen(false); toggle.focus(); }
    });
    window.matchMedia("(min-width: 861px)").addEventListener("change", function (m) { if (m.matches) setOpen(false); });
  }

  // --- Countdown --------------------------------------------------------------
  document.querySelectorAll("[data-countdown]").forEach(function (el) {
    var target = new Date(el.getAttribute("data-countdown")).getTime();
    var parts = {
      d: el.querySelector("[data-unit=d]"), h: el.querySelector("[data-unit=h]"),
      m: el.querySelector("[data-unit=m]"), s: el.querySelector("[data-unit=s]")
    };
    var tick = function () {
      var diff = target - Date.now();
      if (diff <= 0) { el.hidden = true; return false; }
      var sec = Math.floor(diff / 1000);
      parts.d.textContent = Math.floor(sec / 86400);
      parts.h.textContent = String(Math.floor((sec % 86400) / 3600)).padStart(2, "0");
      parts.m.textContent = String(Math.floor((sec % 3600) / 60)).padStart(2, "0");
      parts.s.textContent = String(sec % 60).padStart(2, "0");
      return true;
    };
    if (tick()) { el.hidden = false; var id = setInterval(function () { if (!tick()) clearInterval(id); }, 1000); }
  });

  // --- "Happening now" marker on the schedule --------------------------------
  var items = document.querySelectorAll("[data-start][data-end]");
  if (items.length) {
    var markNow = function () {
      var now = Date.now();
      items.forEach(function (it) {
        var live = now >= new Date(it.dataset.start).getTime() && now < new Date(it.dataset.end).getTime();
        it.classList.toggle("is-now", live);
      });
    };
    markNow();
    setInterval(markNow, 30000);
  }

  // --- Speaker detail dialogs -------------------------------------------------
  document.querySelectorAll("[data-dialog]").forEach(function (btn) {
    var dlg = document.getElementById(btn.getAttribute("data-dialog"));
    if (!dlg || typeof dlg.showModal !== "function") return;
    btn.addEventListener("click", function () { dlg.showModal(); });
    dlg.addEventListener("click", function (e) {
      if (e.target === dlg || e.target.closest("[data-close]")) dlg.close();
    });
  });
  // Open a speaker directly from a link like /speakers/#renzi-vidal
  if (location.hash) {
    var target = document.getElementById("dlg-" + location.hash.slice(1));
    if (target && typeof target.showModal === "function") target.showModal();
  }

  // --- Reveal on scroll -------------------------------------------------------
  var reveals = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && reveals.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("is-visible"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    reveals.forEach(function (r) { io.observe(r); });
  } else {
    reveals.forEach(function (r) { r.classList.add("is-visible"); });
  }
})();
