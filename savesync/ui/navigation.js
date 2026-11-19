"use strict";

(() => {
  const directions = {
    ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0],
  };
  const controls = "button, a[href], input:not([type=hidden]), select, textarea, summary, [tabindex]:not([tabindex='-1'])";
  let selectEdit;
  let lastKeyboard = -Infinity;

  function scope() {
    return document.activeElement?.closest("dialog[open]")
      || Array.from(document.querySelectorAll("dialog[open]")).at(-1) || document.body;
  }

  function candidates(root) {
    return Array.from(root.querySelectorAll(controls)).filter((node) =>
      !node.matches(":disabled") && !node.closest("[hidden], [inert]") &&
      node.getClientRects().length && getComputedStyle(node).visibility === "visible");
  }

  function focus(node) {
    if (!node) return;
    document.documentElement.classList.add("controller-nav");
    node.focus({ preventScroll: true });
    node.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function move(direction) {
    const root = scope();
    const options = candidates(root);
    const current = document.activeElement;
    if (!options.includes(current)) {
      focus(root === document.body && options.find((node) => node.matches(".game[aria-current=true]")) || options[0]);
      return;
    }

    const [x, y] = direction;
    const from = current.getBoundingClientRect();
    let best;
    let bestScore = Infinity;
    for (const candidate of options) {
      if (candidate === current) continue;
      const to = candidate.getBoundingClientRect();
      const dx = (to.left + to.right - from.left - from.right) / 2;
      const dy = (to.top + to.bottom - from.top - from.bottom) / 2;
      const forward = dx * x + dy * y;
      if (forward < 2) continue;
      const side = Math.abs(dx * y - dy * x);
      const score = forward + side * 2.5;
      if (score < bestScore) {
        best = candidate;
        bestScore = score;
      }
    }
    focus(best);
  }

  function selectStep(direction) {
    let index = selectEdit.node.selectedIndex + direction;
    const options = selectEdit.node.options;
    while (options[index]?.disabled || options[index]?.parentElement?.disabled) index += direction;
    if (options[index]) selectEdit.node.selectedIndex = index;
  }

  function input(action) {
    const node = document.activeElement;
    if (selectEdit && node !== selectEdit.node) selectEdit = undefined;
    if (selectEdit) {
      if (action === "A" || action === "B") {
        if (action === "B") selectEdit.node.selectedIndex = selectEdit.original;
        else if (selectEdit.original !== selectEdit.node.selectedIndex) {
          node.dispatchEvent(new Event("input", { bubbles: true }));
          node.dispatchEvent(new Event("change", { bubbles: true }));
        }
        selectEdit = undefined;
      } else if (action === "ArrowUp" || action === "ArrowDown") {
        selectStep(directions[action][1]);
      }
      return;
    }
    if (action in directions) {
      move(directions[action]);
    } else if (action === "A" && candidates(scope()).includes(node)) {
      if (node.matches("select")) selectEdit = { node, original: node.selectedIndex };
      else node.click();
    } else if (action === "B") {
      const dialog = scope();
      if (dialog.matches("dialog[open]")) {
        if (dialog.requestClose) dialog.requestClose();
        else if (dialog.dispatchEvent(new Event("cancel", { cancelable: true }))) dialog.close();
      }
    }
  }

  document.addEventListener("pointerdown", () => document.documentElement.classList.remove("controller-nav"));
  document.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.defaultPrevented) return;
    const arrow = event.key in directions;
    if (!arrow && event.key !== "Enter" && event.key !== "Escape") return;
    lastKeyboard = performance.now();
    const node = document.activeElement;
    if (selectEdit && selectEdit.node !== node) selectEdit = undefined;
    if (selectEdit || (event.key === "Enter" && node.matches("select"))) {
      event.preventDefault();
      input(arrow ? event.key : event.key === "Enter" ? "A" : "B");
      return;
    }
    if (!arrow) return;
    if (node.matches("textarea, [contenteditable=true]") ||
        ((event.key === "ArrowLeft" || event.key === "ArrowRight") &&
         node.matches("input:not([type=checkbox]):not([type=radio])"))) return;
    event.preventDefault();
    move(directions[event.key]);
  });

  if (!navigator.getGamepads) return;
  const buttons = [[12, "ArrowUp"], [13, "ArrowDown"], [14, "ArrowLeft"], [15, "ArrowRight"], [0, "A"], [1, "B"]];
  const nextPress = new Map();
  function frame(now) {
    let pads;
    try {
      pads = navigator.getGamepads();
    } catch {
      return;
    }
    const pad = Array.from(pads).find((entry) => entry?.connected && entry.mapping === "standard");
    for (const [index, action] of buttons) {
      if (!pad?.buttons[index]?.pressed || !document.hasFocus()) {
        nextPress.delete(index);
        continue;
      }
      if (now < (nextPress.get(index) ?? 0) || now - lastKeyboard < 120) continue;
      const repeat = nextPress.has(index);
      nextPress.set(index, action in directions ? now + (repeat ? 110 : 350) : Infinity);
      if (action === "A" && !candidates(scope()).includes(document.activeElement)) move([0, 1]);
      else input(action);
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
