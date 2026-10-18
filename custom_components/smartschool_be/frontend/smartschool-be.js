// Smartschool BE: open a message from a dashboard card in a pop-up.
//
// Any element with data-ssbe-msg="<message id>" becomes clickable: the click
// asks the integration to load that message into the "Geselecteerd bericht"
// sensor and opens the pop-up at data-ssbe-popup (default
// "#smartschool-bericht", e.g. a Bubble Card pop-up with that hash).
// Optional: data-ssbe-entry="<config entry id>" when several accounts exist.
(() => {
  if (window.__ssbeLoaded) return;
  window.__ssbeLoaded = true;

  const findTarget = (event) =>
    event
      .composedPath()
      .find((node) => node && node.hasAttribute && node.hasAttribute("data-ssbe-msg"));

  const openHash = (hash) => {
    if (window.location.hash === hash) return;
    window.history.pushState(null, "", hash);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  };

  document.addEventListener(
    "click",
    (event) => {
      const target = findTarget(event);
      if (!target) return;
      event.preventDefault();
      event.stopPropagation();

      const id = parseInt(target.getAttribute("data-ssbe-msg"), 10);
      if (!id) return;
      const entry = target.getAttribute("data-ssbe-entry");
      const hash = target.getAttribute("data-ssbe-popup") || "#smartschool-bericht";

      // Lets the pop-up show "loading" until this message has arrived.
      window.__ssbeWant = id;

      const root = document.querySelector("home-assistant");
      const hass = root && root.hass;
      if (hass) {
        const data = { message_id: id };
        if (entry) data.config_entry_id = entry;
        hass
          .callService("smartschool_be", "select_message", data)
          .catch((err) => console.error("smartschool_be: select_message failed", err));
      }
      openHash(hash);
    },
    true
  );
})();
