// GAIA Visual Topology & Scenario Editor Client Logic (Phase 5)
document.addEventListener("DOMContentLoaded", () => {
  const state = {
    baseName: "sc4_web",
    routers: [
      { name: "r0", subnet: "10.0.1.0/24", ip: "10.0.1.1", x: 250, y: 150 },
      { name: "r1", subnet: "10.0.2.0/24", ip: "10.0.2.1", x: 450, y: 150 }
    ],
    hosts: [
      { name: "c0", role: "client", router: "r0", ip: "10.0.1.2", x: 100, y: 300, workload: { kind: "iperf3_client", command: "" } },
      { name: "s0", role: "server", router: "r1", ip: "10.0.2.2", x: 600, y: 300, workload: { kind: "iperf3_server", command: "" } }
    ],
    links: [
      { a: "c0", b: "r0", delay: 15, queue_size: 1000, bandwidth: 100, loss: 0, backup: 0 },
      { a: "r0", b: "r1", delay: 5, queue_size: 1000, bandwidth: 1000, loss: 0, backup: 0 },
      { a: "s0", b: "r1", delay: 15, queue_size: 1000, bandwidth: 100, loss: 0, backup: 0 }
    ],
    selectedItem: null, // { type: 'node'|'link', name?: string, index?: number }
    linkMode: false,
    linkModeSource: null,
    transit_subnet: "172.31.0.0/16",
    change_netem: false,
    previewMode: "topo", // 'topo' | 'scenario'
    panX: 0,
    panY: 0
  };

  const svg = document.getElementById("canvas-svg");
  const nodesContainer = document.getElementById("canvas-nodes");
  const inspectorContent = document.getElementById("inspector-content");
  const ipamTbody = document.getElementById("ipam-tbody");
  const routesBox = document.getElementById("routes-container");
  const validationOutput = document.getElementById("validation-output");
  const configPreview = document.getElementById("config-preview");
  const configSelect = document.getElementById("config-select");
  const xpConfigSelect = document.getElementById("xp-config-select");
  const btnExportTopo = document.getElementById("btn-export-topo");
  const btnExportScenario = document.getElementById("btn-export-scenario");
  const btnLoad = document.getElementById("btn-load");
  const btnLoadXp = document.getElementById("btn-load-xp");
  const canvasContainer = document.querySelector(".canvas-container");
  const addLinkBtn = document.getElementById("add-link-btn");
  const btnResetView = document.getElementById("reset-view-btn");
  const btnThemeToggle = document.getElementById("btn-theme-toggle");
  const inputBaseName = document.getElementById("input-base-name");
  const previewTopoPath = document.getElementById("preview-topo-path");
  const previewXpPath = document.getElementById("preview-xp-path");
  const tabTopoPreview = document.getElementById("tab-topo-preview");
  const tabScenarioPreview = document.getElementById("tab-scenario-preview");

  const NODE_W = 110;
  const NODE_H = 96;
  const ANCHOR_X = NODE_W / 2; // 55
  const ANCHOR_Y = NODE_H / 2; // 48
  const WORLD_W = 4000;
  const WORLD_H = 3000;

  let drag = null; // { el, node, offsetX, offsetY }
  let pan = null; // { startX, startY, origX, origY, moved }
  let isSpaceDown = false;
  let suppressNextClick = false;
  let panGroup = null;
  let timer = null;
  let validationSeq = 0;
  let lastValidationValid = true;
  let unreachableRouterSet = new Set();
  let currentUnreachablePairs = [];

  const clampWorld = (x, y) => ({
    x: Math.max(0, Math.min(WORLD_W - NODE_W, x)),
    y: Math.max(0, Math.min(WORLD_H - NODE_H, y))
  });

  function clampPan(px, py) {
    const cw = canvasContainer ? canvasContainer.clientWidth : 800;
    const ch = canvasContainer ? canvasContainer.clientHeight : 600;
    return {
      x: Math.max(0, Math.min(Math.max(0, WORLD_W - cw), px)),
      y: Math.max(0, Math.min(Math.max(0, WORLD_H - ch), py))
    };
  }

  function screenToWorld(clientX, clientY) {
    const r = canvasContainer.getBoundingClientRect();
    return {
      x: clientX - r.left + state.panX,
      y: clientY - r.top + state.panY
    };
  }

  function getSpawnCoordinates(offsetX = 0, offsetY = 0) {
    const cw = canvasContainer ? canvasContainer.clientWidth : 800;
    const ch = canvasContainer ? canvasContainer.clientHeight : 600;
    const rawVx = state.panX + (cw / 2 - NODE_W / 2) + offsetX;
    const rawVy = state.panY + (ch / 2 - NODE_H / 2) + offsetY;
    const vx = Math.max(state.panX, Math.min(state.panX + cw - NODE_W, rawVx));
    const vy = Math.max(state.panY, Math.min(state.panY + ch - NODE_H, rawVy));
    return clampWorld(vx, vy);
  }

  const isTypingTarget = (t) =>
    t && (/^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(t.tagName) || t.isContentEditable);

  function updateCursorState() {
    if (!canvasContainer) return;
    canvasContainer.classList.toggle("pan-ready", isSpaceDown && !pan && !state.linkMode);
    canvasContainer.classList.toggle("panning", !!pan);
  }

  function applyViewTransform() {
    const tx = -state.panX, ty = -state.panY;
    if (panGroup) panGroup.setAttribute("transform", `translate(${tx},${ty})`);
    if (nodesContainer) nodesContainer.style.transform = `translate(${tx}px, ${ty}px)`;
  }

  function escapeHTML(str) {
    if (str == null) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function getNextNodeName(prefix) {
    const allNodes = [...state.routers, ...state.hosts];
    const regex = new RegExp(`^${prefix}(\\d+)$`);
    const indices = new Set();
    allNodes.forEach(n => {
      const m = (n.name || "").match(regex);
      if (m) indices.add(parseInt(m[1], 10));
    });
    let idx = 0;
    while (indices.has(idx)) idx++;
    return `${prefix}${idx}`;
  }

  function getNextSubnet() {
    const usedThirdOctets = new Set();
    state.routers.forEach(r => {
      const m = (r.subnet || "").match(/^10\.0\.(\d+)\.0\/24$/);
      if (m) usedThirdOctets.add(parseInt(m[1], 10));
    });
    let octet = 1;
    while (usedThirdOctets.has(octet)) octet++;
    return `10.0.${octet}.0/24`;
  }

  function updatePathPreviews() {
    const base = (inputBaseName.value || "").trim() || "default";
    state.baseName = base;
    if (previewTopoPath) previewTopoPath.textContent = `Topo: config/topo/topo_${base}`;
    if (previewXpPath) previewXpPath.textContent = `Exp: config/xp/xp_${base}`;
  }

  if (inputBaseName) {
    inputBaseName.value = state.baseName;
    updatePathPreviews();
    inputBaseName.addEventListener("input", () => {
      updatePathPreviews();
      updatePreviewDisplay();
    });
  }

  // Theme Toggle Management (WP-5a, WP-6, C-1.2)
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  }

  function storedTheme() {
    try {
      return localStorage.getItem("gaia-theme");
    } catch (e) {
      return null;
    }
  }

  function syncThemeButton() {
    if (!btnThemeToggle) return;
    const dark = currentTheme() === "dark";
    btnThemeToggle.textContent = dark ? "🌙 Dark" : "☀️ Light";
    btnThemeToggle.setAttribute("aria-pressed", String(!dark));
  }

  if (btnThemeToggle) {
    btnThemeToggle.addEventListener("click", () => {
      const next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try {
        localStorage.setItem("gaia-theme", next);
      } catch (e) {}
      syncThemeButton();
    });
    syncThemeButton();
  }

  try {
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onSystemChange = (e) => {
      if (storedTheme()) return; // ignore if user explicitly chose
      document.documentElement.setAttribute("data-theme", e.matches ? "light" : "dark");
      syncThemeButton();
    };
    if (mq.addEventListener) {
      mq.addEventListener("change", onSystemChange);
    } else if (mq.addListener) {
      mq.addListener(onSystemChange);
    }
  } catch (e) {}

  // Global mouse listeners for dragging & panning
  window.addEventListener("mousemove", (e) => {
    if (pan) {
      const dx = e.clientX - pan.startX;
      const dy = e.clientY - pan.startY;
      if (!pan.moved && Math.hypot(dx, dy) > 3) pan.moved = true;
      const p = clampPan(pan.origX - dx, pan.origY - dy);
      state.panX = p.x;
      state.panY = p.y;
      applyViewTransform();
      return;
    }

    if (!drag || !canvasContainer) return;
    const worldPt = screenToWorld(e.clientX, e.clientY);
    const clamped = clampWorld(worldPt.x - drag.offsetX, worldPt.y - drag.offsetY);
    drag.node.x = clamped.x;
    drag.node.y = clamped.y;
    drag.el.style.left = `${drag.node.x}px`;
    drag.el.style.top = `${drag.node.y}px`;
    updateLinkLines();
  });

  window.addEventListener("mouseup", () => {
    if (pan) {
      if (pan.moved) suppressNextClick = true;
      pan = null;
      updateCursorState();
    }
    if (drag) {
      drag = null;
      triggerValidationDebounced();
    }
  });

  window.addEventListener("resize", () => {
    if (!canvasContainer) return;
    const p = clampPan(state.panX, state.panY);
    state.panX = p.x;
    state.panY = p.y;
    applyViewTransform();
  });

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.linkMode) {
      exitLinkMode();
      return;
    }
    if (e.key === "Home" && !isTypingTarget(e.target)) {
      state.panX = 0;
      state.panY = 0;
      applyViewTransform();
      return;
    }
    if (e.code === "Space" && !isTypingTarget(e.target) && !state.linkMode) {
      if (!isSpaceDown) {
        isSpaceDown = true;
        updateCursorState();
      }
      e.preventDefault();
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.code === "Space") {
      isSpaceDown = false;
      if (!pan) updateCursorState();
    }
  });

  window.addEventListener("blur", () => {
    isSpaceDown = false;
    pan = null;
    suppressNextClick = false;
    updateCursorState();
  });

  if (canvasContainer) {
    canvasContainer.addEventListener("mousedown", (e) => {
      if (isTypingTarget(document.activeElement)) {
        document.activeElement.blur();
      }
      if (!isSpaceDown || e.button !== 0) return;
      pan = {
        startX: e.clientX,
        startY: e.clientY,
        origX: state.panX,
        origY: state.panY,
        moved: false
      };
      e.preventDefault();
      e.stopPropagation();
      updateCursorState();
    }, true);

    canvasContainer.addEventListener("click", (e) => {
      if (suppressNextClick) {
        suppressNextClick = false;
        e.stopPropagation();
        return;
      }
      if (e.target === svg || e.target === canvasContainer || e.target === nodesContainer || (panGroup && e.target === panGroup)) {
        if (state.linkMode) {
          exitLinkMode();
        } else if (state.selectedItem) {
          state.selectedItem = null;
          renderCanvas();
          renderInspector();
        }
      }
    }, true);
  }

  // Palette Listeners
  document.getElementById("add-router-btn").addEventListener("click", () => {
    if (state.linkMode) exitLinkMode();
    const name = getNextNodeName("r");
    const subnet = getNextSubnet();
    const col = state.routers.length % 4;
    const row = Math.floor(state.routers.length / 4);
    const pos = getSpawnCoordinates(col * 160 - 240, row * 100 - 100);
    state.routers.push({ name, subnet, ip: null, x: pos.x, y: pos.y });
    state.selectedItem = { type: "node", name };
    renderCanvas();
    renderInspector();
    triggerValidationDebounced();
  });

  document.getElementById("add-client-btn").addEventListener("click", () => {
    if (state.linkMode) exitLinkMode();
    const name = getNextNodeName("c");
    const rName = state.routers[0] ? state.routers[0].name : "";
    const pos = getSpawnCoordinates(-150 + ((state.hosts.length * 50) % 200) - 50, 0);
    state.hosts.push({
      name,
      role: "client",
      router: rName,
      ip: null,
      x: pos.x,
      y: pos.y,
      workload: { kind: "iperf3_client", command: "" }
    });
    if (rName) {
      state.links.push({ a: name, b: rName, delay: 15, queue_size: 1000, bandwidth: 100, loss: 0, backup: 0 });
    }
    state.selectedItem = { type: "node", name };
    renderCanvas();
    renderInspector();
    triggerValidationDebounced();
  });

  document.getElementById("add-server-btn").addEventListener("click", () => {
    if (state.linkMode) exitLinkMode();
    const name = getNextNodeName("s");
    const rName = state.routers.length > 0 ? state.routers[state.routers.length - 1].name : "";
    const pos = getSpawnCoordinates(150 + ((state.hosts.length * 50) % 200) - 50, 0);
    state.hosts.push({
      name,
      role: "server",
      router: rName,
      ip: null,
      x: pos.x,
      y: pos.y,
      workload: { kind: "iperf3_server", command: "" }
    });
    if (rName) {
      state.links.push({ a: name, b: rName, delay: 15, queue_size: 1000, bandwidth: 100, loss: 0, backup: 0 });
    }
    state.selectedItem = { type: "node", name };
    renderCanvas();
    renderInspector();
    triggerValidationDebounced();
  });

  const addIdleBtn = document.getElementById("add-idle-btn");
  if (addIdleBtn) {
    addIdleBtn.addEventListener("click", () => {
      if (state.linkMode) exitLinkMode();
      const name = getNextNodeName("h");
      const rName = state.routers.length > 0 ? state.routers[0].name : "";
      const pos = getSpawnCoordinates(((state.hosts.length * 50) % 250) - 125, 40);
      state.hosts.push({
        name,
        role: "idle",
        router: rName,
        ip: null,
        x: pos.x,
        y: pos.y,
        workload: { kind: "none", command: "" }
      });
      if (rName) {
        state.links.push({ a: name, b: rName, delay: 15, queue_size: 1000, bandwidth: 100, loss: 0, backup: 0 });
      }
      state.selectedItem = { type: "node", name };
      renderCanvas();
      renderInspector();
      triggerValidationDebounced();
    });
  }

  if (btnResetView) {
    btnResetView.addEventListener("click", () => {
      state.panX = 0;
      state.panY = 0;
      applyViewTransform();
    });
  }

  if (addLinkBtn) {
    addLinkBtn.addEventListener("click", () => {
      state.linkMode = !state.linkMode;
      state.linkModeSource = null;
      addLinkBtn.classList.toggle("active", state.linkMode);
      if (canvasContainer) canvasContainer.classList.toggle("link-mode", state.linkMode);
      renderCanvas();
    });
  }

  document.getElementById("btn-validate").addEventListener("click", () => {
    validateAndIPAM();
  });

  if (btnExportTopo) {
    btnExportTopo.addEventListener("click", () => {
      exportTopoConfigFile(false);
    });
  }

  if (btnExportScenario) {
    btnExportScenario.addEventListener("click", () => {
      exportScenarioJSONFile(false);
    });
  }

  if (btnLoad) {
    btnLoad.addEventListener("click", () => {
      const selected = configSelect.value;
      if (selected) loadConfigFile(selected);
    });
  }

  if (btnLoadXp) {
    btnLoadXp.addEventListener("click", () => {
      const selected = xpConfigSelect.value;
      if (selected) loadScenarioFile(selected);
    });
  }

  if (tabTopoPreview && tabScenarioPreview) {
    tabTopoPreview.addEventListener("click", () => {
      state.previewMode = "topo";
      tabTopoPreview.classList.add("active");
      tabScenarioPreview.classList.remove("active");
      updatePreviewDisplay();
    });
    tabScenarioPreview.addEventListener("click", () => {
      state.previewMode = "scenario";
      tabScenarioPreview.classList.add("active");
      tabTopoPreview.classList.remove("active");
      updatePreviewDisplay();
    });
  }

  function exitLinkMode() {
    state.linkMode = false;
    state.linkModeSource = null;
    if (addLinkBtn) addLinkBtn.classList.remove("active");
    if (canvasContainer) canvasContainer.classList.remove("link-mode");
    renderCanvas();
  }

  function handleLinkModeClick(nodeName) {
    if (!state.linkModeSource) {
      const host = state.hosts.find(h => h.name === nodeName);
      if (host) {
        const existingLinks = state.links.filter(l => l.a === nodeName || l.b === nodeName);
        if (existingLinks.length >= 1) {
          alert(`Host '${nodeName}' already has a link. Multi-homing is currently not supported.`);
          return;
        }
      }
      state.linkModeSource = nodeName;
      renderCanvas();
      return;
    }

    const a = state.linkModeSource;
    const b = nodeName;

    if (a === b) {
      state.linkModeSource = null;
      renderCanvas();
      return;
    }

    const isHostA = state.hosts.some(h => h.name === a);
    const isHostB = state.hosts.some(h => h.name === b);

    if (isHostA && isHostB) {
      alert("Direct host-to-host links are not supported. Connect hosts to a router.");
      state.linkModeSource = null;
      renderCanvas();
      return;
    }

    if (isHostB) {
      const existingB = state.links.filter(l => l.a === b || l.b === b);
      if (existingB.length >= 1) {
        alert(`Host '${b}' already has a link. Multi-homing is currently not supported.`);
        state.linkModeSource = null;
        renderCanvas();
        return;
      }
    }

    const exists = state.links.some(l => (l.a === a && l.b === b) || (l.a === b && l.b === a));
    if (exists) {
      alert("A link already exists between these nodes.");
      state.linkModeSource = null;
      renderCanvas();
      return;
    }

    state.links.push({ a, b, delay: 15, queue_size: 1000, bandwidth: 100, loss: 0, backup: 0 });
    state.linkMode = false;
    state.linkModeSource = null;
    if (addLinkBtn) addLinkBtn.classList.remove("active");
    if (canvasContainer) canvasContainer.classList.remove("link-mode");

    state.selectedItem = { type: "link", index: state.links.length - 1 };
    syncAttachedRouters();
    renderCanvas();
    renderInspector();
    triggerValidationDebounced();
  }

  function syncAttachedRouters() {
    let changed = false;
    state.hosts.forEach(h => {
      const connectedLinks = state.links.filter(l => l.a === h.name || l.b === h.name);
      let attachedRouter = "";
      for (const l of connectedLinks) {
        const other = (l.a === h.name) ? l.b : l.a;
        if (state.routers.some(r => r.name === other)) {
          attachedRouter = other;
          break;
        }
      }
      if (attachedRouter !== h.router) {
        h.router = attachedRouter;
        h.ip = null;
        changed = true;
      }
    });
    return changed;
  }

  function renderCanvas() {
    svg.innerHTML = "";
    nodesContainer.innerHTML = "";

    panGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
    panGroup.id = "pan-group";

    // Draw Links
    state.links.forEach((link, idx) => {
      const posA = getNodePos(link.a);
      const posB = getNodePos(link.b);
      if (posA && posB) {
        const x1 = posA.x + ANCHOR_X;
        const y1 = posA.y + ANCHOR_Y;
        const x2 = posB.x + ANCHOR_X;
        const y2 = posB.y + ANCHOR_Y;

        const hit = document.createElementNS("http://www.w3.org/2000/svg", "line");
        hit.setAttribute("x1", x1);
        hit.setAttribute("y1", y1);
        hit.setAttribute("x2", x2);
        hit.setAttribute("y2", y2);
        hit.setAttribute("stroke", "transparent");
        hit.setAttribute("stroke-width", "14");
        hit.style.cursor = "pointer";
        hit.dataset.linkIdx = idx;

        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("x1", x1);
        line.setAttribute("y1", y1);
        line.setAttribute("x2", x2);
        line.setAttribute("y2", y2);
        line.classList.add("link-line");
        line.dataset.linkIdx = idx;

        if (state.selectedItem && state.selectedItem.type === "link" && state.selectedItem.index === idx) {
          line.classList.add("selected");
        }

        const handleLinkClick = (e) => {
          e.stopPropagation();
          if (state.linkMode) return;
          state.selectedItem = { type: "link", index: idx };
          renderCanvas();
          renderInspector();
        };

        hit.addEventListener("click", handleLinkClick);
        line.addEventListener("click", handleLinkClick);

        panGroup.appendChild(hit);
        panGroup.appendChild(line);

        const midX = (x1 + x2) / 2;
        const midY = (y1 + y2) / 2 - 6;
        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("x", midX);
        label.setAttribute("y", midY);
        label.classList.add("link-label");
        label.dataset.linkIdx = idx;
        label.textContent = `${link.bandwidth || 0}Mbps · ${link.delay || 0}ms`;
        panGroup.appendChild(label);
      }
    });

    svg.appendChild(panGroup);

    // Draw Routers
    state.routers.forEach(r => {
      const isIsolated = unreachableRouterSet.has(r.name);
      createNodeElement(r, "router", `🔀`, r.subnet, isIsolated);
    });

    // Draw Hosts
    state.hosts.forEach(h => {
      let icon = `💻`;
      let typeClass = "client";
      if (h.role === "server") {
        icon = `🖥️`;
        typeClass = "server";
      } else if (h.role === "idle") {
        icon = `⏸️`;
        typeClass = "idle";
      }
      createNodeElement(h, typeClass, icon, h.router ? `via ${h.router}` : `via —`, false);
    });

    applyViewTransform();
  }

  function createNodeElement(node, type, iconStr, subStr, isIsolated = false) {
    const el = document.createElement("div");
    el.className = `node-element node-${type}`;
    if (isIsolated) {
      el.classList.add("node-isolated");
    }

    if (state.selectedItem && state.selectedItem.type === "node" && state.selectedItem.name === node.name) {
      el.classList.add("selected");
    }
    if (state.linkModeSource === node.name) {
      el.classList.add("link-source");
    }

    el.style.left = `${node.x}px`;
    el.style.top = `${node.y}px`;

    el.innerHTML = `
      <div class="node-icon">${iconStr}</div>
      <div class="node-name">${escapeHTML(node.name)}</div>
      <div class="node-ip">${escapeHTML(node.ip || "Auto-IP")}</div>
      <div class="node-subnet">${escapeHTML(subStr)}</div>
    `;

    el.addEventListener("mousedown", (e) => {
      e.stopPropagation();
      if (state.linkMode) {
        handleLinkModeClick(node.name);
        return;
      }
      const worldPos = screenToWorld(e.clientX, e.clientY);
      drag = {
        el,
        node,
        offsetX: worldPos.x - node.x,
        offsetY: worldPos.y - node.y
      };
      state.selectedItem = { type: "node", name: node.name };

      nodesContainer.querySelectorAll(".node-element.selected").forEach(n => n.classList.remove("selected"));
      el.classList.add("selected");
      renderInspector();
    });

    nodesContainer.appendChild(el);
  }

  function getNodePos(name) {
    const r = state.routers.find(x => x.name === name);
    if (r) return { x: r.x, y: r.y };
    const h = state.hosts.find(x => x.name === name);
    if (h) return { x: h.x, y: h.y };
    return null;
  }

  function updateLinkLines() {
    state.links.forEach((link, idx) => {
      const posA = getNodePos(link.a);
      const posB = getNodePos(link.b);
      if (posA && posB) {
        const x1 = posA.x + ANCHOR_X;
        const y1 = posA.y + ANCHOR_Y;
        const x2 = posB.x + ANCHOR_X;
        const y2 = posB.y + ANCHOR_Y;

        const lines = svg.querySelectorAll(`line[data-link-idx="${idx}"]`);
        lines.forEach(line => {
          line.setAttribute("x1", x1);
          line.setAttribute("y1", y1);
          line.setAttribute("x2", x2);
          line.setAttribute("y2", y2);
        });

        const labels = svg.querySelectorAll(`text.link-label[data-link-idx="${idx}"]`);
        labels.forEach(label => {
          label.setAttribute("x", (x1 + x2) / 2);
          label.setAttribute("y", (y1 + y2) / 2 - 6);
        });
      }
    });
  }

  function renderInspector() {
    if (!state.selectedItem) {
      inspectorContent.innerHTML = `<p class="muted">Select a node or link on the canvas to inspect and edit properties.</p>`;
      return;
    }

    if (state.selectedItem.type === "node") {
      const nodeName = state.selectedItem.name;
      const router = state.routers.find(r => r.name === nodeName);
      const host = state.hosts.find(h => h.name === nodeName);

      if (router) {
        if (router.pcap === undefined) router.pcap = false;

        inspectorContent.innerHTML = `
          <div class="inspector-header">
            <span class="entity-badge badge-router">🔀 Router</span>
            <span class="entity-title">${escapeHTML(router.name)}</span>
          </div>

          <div class="inspector-section">
            <div class="inspector-field">
              <label>Router Name</label>
              <input type="text" id="inp-name" value="${escapeHTML(router.name)}" placeholder="e.g. r0">
            </div>
            <div class="inspector-field">
              <label>Subnet CIDR</label>
              <input type="text" id="inp-subnet" value="${escapeHTML(router.subnet)}" placeholder="e.g. 10.0.1.0/24">
            </div>
            <div class="inspector-field">
              <label>Router IP <span class="label-hint">(Optional)</span></label>
              <input type="text" id="inp-ip" value="${escapeHTML(router.ip || "")}" placeholder="Auto .1">
            </div>
          </div>

          <div class="inspector-card pcap-action-card">
            <div class="card-title-row">
              <label class="section-label">Packet Capture (Scenario)</label>
            </div>
            <button id="btn-toggle-pcap" type="button" class="pcap-button ${router.pcap ? "pcap-active" : "pcap-inactive"}">
              <span class="pcap-btn-icon">${router.pcap ? "📡" : "⚪"}</span>
              <span class="pcap-btn-label">Packet Capture</span>
              <span class="pcap-badge-state">${router.pcap ? "ACTIVE" : "OFF"}</span>
            </button>
            <div class="pcap-preview-badge ${router.pcap ? "active" : "inactive"}" id="pcap-preview-note">
              <span class="pcap-icon">📄</span>
              <code>{output_dir}/${escapeHTML(router.name)}.pcap</code>
            </div>
          </div>

          <button id="inp-delete" class="btn btn-danger-outline" style="width:100%;">
            <span>🗑️</span> Delete Router
          </button>
        `;

        document.getElementById("inp-name").addEventListener("change", (e) => {
          const oldName = router.name;
          const newName = e.target.value.trim();
          if (newName && newName !== oldName) {
            router.name = newName;
            state.hosts.forEach(h => { if (h.router === oldName) h.router = newName; });
            state.links.forEach(l => {
              if (l.a === oldName) l.a = newName;
              if (l.b === oldName) l.b = newName;
            });
            state.selectedItem.name = newName;
            const previewNote = document.getElementById("pcap-preview-note");
            if (previewNote) {
              previewNote.innerHTML = `<span class="pcap-icon">📄</span> <code>{output_dir}/${escapeHTML(newName)}.pcap</code>`;
            }
            renderCanvas();
            triggerValidationDebounced();
          }
        });

        const pcapBtn = document.getElementById("btn-toggle-pcap");
        if (pcapBtn) {
          pcapBtn.addEventListener("click", () => {
            router.pcap = !router.pcap;
            renderInspector();
            updatePreviewDisplay();
            triggerValidationDebounced();
          });
        }

        document.getElementById("inp-subnet").addEventListener("change", (e) => {
          router.subnet = e.target.value.trim();
          triggerValidationDebounced();
        });

        document.getElementById("inp-ip").addEventListener("change", (e) => {
          router.ip = e.target.value.trim() || null;
          triggerValidationDebounced();
        });

        document.getElementById("inp-delete").addEventListener("click", () => {
          state.routers = state.routers.filter(r => r.name !== router.name);
          state.links = state.links.filter(l => l.a !== router.name && l.b !== router.name);
          state.selectedItem = null;
          syncAttachedRouters();
          renderCanvas();
          renderInspector();
          triggerValidationDebounced();
        });

      } else if (host) {
        if (host.pcap === undefined) host.pcap = false;
        syncAttachedRouters();
        let routerOptions = `<option value="" ${!host.router ? "selected" : ""}>-- None (Disconnected) --</option>`;
        routerOptions += state.routers.map(r => `<option value="${escapeHTML(r.name)}" ${r.name === host.router ? "selected" : ""}>${escapeHTML(r.name)}</option>`).join("");

        if (!host.workload) {
          host.workload = {
            kind: host.role === "client" ? "iperf3_client" : (host.role === "server" ? "iperf3_server" : "none"),
            command: ""
          };
        }

        const isCustom = host.workload.kind === "custom_client" || host.workload.kind === "custom_server";
        const roleBadgeClass = host.role === "client" ? "badge-client" : (host.role === "server" ? "badge-server" : "badge-idle");
        const roleBadgeLabel = host.role === "client" ? "💻 Client" : (host.role === "server" ? "🖥️ Server" : "⏸️ Idle Host");

        inspectorContent.innerHTML = `
          <div class="inspector-header">
            <span class="entity-badge ${roleBadgeClass}">${roleBadgeLabel}</span>
            <span class="entity-title">${escapeHTML(host.name)}</span>
          </div>

          <div class="inspector-section">
            <div class="inspector-field">
              <label>Host Name</label>
              <input type="text" id="inp-name" value="${escapeHTML(host.name)}" placeholder="e.g. c0">
            </div>
            <div class="inspector-field">
              <label>Host Role</label>
              <select id="inp-role">
                <option value="client" ${host.role === "client" ? "selected" : ""}>Client (Traffic generator)</option>
                <option value="server" ${host.role === "server" ? "selected" : ""}>Server (Traffic receiver)</option>
                <option value="idle" ${host.role === "idle" ? "selected" : ""}>Idle (Background node)</option>
              </select>
            </div>
            <div class="inspector-field">
              <label>Attached Router</label>
              <select id="inp-router">${routerOptions}</select>
            </div>
          </div>

          <div class="inspector-card">
            <div class="card-title-row">
              <label class="section-label">Node Workload (Scenario)</label>
            </div>
            <div class="inspector-field">
              <select id="inp-workload-kind">
                <option value="none" ${host.workload.kind === "none" ? "selected" : ""}>None (Idle / No Traffic)</option>
                <option value="iperf3_client" ${host.workload.kind === "iperf3_client" ? "selected" : ""}>iPerf3 Client</option>
                <option value="iperf3_server" ${host.workload.kind === "iperf3_server" ? "selected" : ""}>iPerf3 Server</option>
                <option value="mesh_ping" ${host.workload.kind === "mesh_ping" ? "selected" : ""}>Mesh Ping Probe</option>
                <option value="custom_client" ${host.workload.kind === "custom_client" ? "selected" : ""}>Custom Client Command</option>
                <option value="custom_server" ${host.workload.kind === "custom_server" ? "selected" : ""}>Custom Server Command</option>
              </select>
            </div>
            <div id="custom-cmd-box" class="inspector-field" style="${isCustom ? "" : "display:none;"} margin-top:8px;">
              <label>Custom Shell Command</label>
              <textarea id="inp-workload-cmd" class="custom-cmd-textarea" placeholder="e.g. python3 -m http.server 8080">${escapeHTML(host.workload.command || "")}</textarea>
              <p class="muted" style="font-size:0.68rem; margin-top:4px; line-height:1.35;">
                Placeholders: <code>{server_ip}</code>, <code>{client_ip}</code>, <code>{node}</code>, <code>{iface}</code>, <code>{duration}</code>, <code>{output_dir}</code>
              </p>
            </div>
          </div>

          <div class="inspector-card pcap-action-card">
            <div class="card-title-row">
              <label class="section-label">Packet Capture (Scenario)</label>
            </div>
            <button id="btn-toggle-pcap" type="button" class="pcap-button ${host.pcap ? "pcap-active" : "pcap-inactive"}">
              <span class="pcap-btn-icon">${host.pcap ? "📡" : "⚪"}</span>
              <span class="pcap-btn-label">Packet Capture</span>
              <span class="pcap-badge-state">${host.pcap ? "ACTIVE" : "OFF"}</span>
            </button>
            <div class="pcap-preview-badge ${host.pcap ? "active" : "inactive"}" id="pcap-preview-note">
              <span class="pcap-icon">📄</span>
              <code>{output_dir}/${escapeHTML(host.name)}.pcap</code>
            </div>
          </div>

          <button id="inp-delete" class="btn btn-danger-outline" style="width:100%;">
            <span>🗑️</span> Delete Host
          </button>
        `;

        document.getElementById("inp-name").addEventListener("change", (e) => {
          const oldName = host.name;
          const newName = e.target.value.trim();
          if (newName && newName !== oldName) {
            host.name = newName;
            state.links.forEach(l => {
              if (l.a === oldName) l.a = newName;
              if (l.b === oldName) l.b = newName;
            });
            state.selectedItem.name = newName;
            const previewNote = document.getElementById("pcap-preview-note");
            if (previewNote) {
              previewNote.innerHTML = `<span class="pcap-icon">📄</span> <code>{output_dir}/${escapeHTML(newName)}.pcap</code>`;
            }
            renderCanvas();
            triggerValidationDebounced();
          }
        });

        const pcapBtn = document.getElementById("btn-toggle-pcap");
        if (pcapBtn) {
          pcapBtn.addEventListener("click", () => {
            host.pcap = !host.pcap;
            renderInspector();
            updatePreviewDisplay();
            triggerValidationDebounced();
          });
        }

        document.getElementById("inp-role").addEventListener("change", (e) => {
          host.role = e.target.value;
          renderCanvas();
          triggerValidationDebounced();
        });

        document.getElementById("inp-workload-kind").addEventListener("change", (e) => {
          host.workload.kind = e.target.value;
          const cmdBox = document.getElementById("custom-cmd-box");
          if (host.workload.kind === "custom_client" || host.workload.kind === "custom_server") {
            if (cmdBox) cmdBox.style.display = "";
          } else {
            if (cmdBox) cmdBox.style.display = "none";
          }
          updatePreviewDisplay();
        });

        const cmdInput = document.getElementById("inp-workload-cmd");
        if (cmdInput) {
          cmdInput.addEventListener("input", (e) => {
            host.workload.command = e.target.value;
            updatePreviewDisplay();
          });
        }

        document.getElementById("inp-router").addEventListener("change", (e) => {
          const newRouter = e.target.value;
          const linkIndex = state.links.findIndex(l =>
            (l.a === host.name && state.routers.some(r => r.name === l.b)) ||
            (l.b === host.name && state.routers.some(r => r.name === l.a))
          );

          if (newRouter) {
            if (linkIndex !== -1) {
              const l = state.links[linkIndex];
              if (l.a === host.name) l.b = newRouter;
              else l.a = newRouter;
            } else {
              state.links.push({ a: host.name, b: newRouter, delay: 15, queue_size: 1000, bandwidth: 100, loss: 0, backup: 0 });
            }
            host.router = newRouter;
            host.ip = null;
          } else {
            if (linkIndex !== -1) {
              state.links.splice(linkIndex, 1);
            }
            host.router = "";
            host.ip = null;
          }

          syncAttachedRouters();
          renderCanvas();
          triggerValidationDebounced();
        });

        document.getElementById("inp-delete").addEventListener("click", () => {
          state.hosts = state.hosts.filter(h => h.name !== host.name);
          state.links = state.links.filter(l => l.a !== host.name && l.b !== host.name);
          state.selectedItem = null;
          renderCanvas();
          renderInspector();
          triggerValidationDebounced();
        });
      }
    } else if (state.selectedItem.type === "link") {
      const link = state.links[state.selectedItem.index];
      if (link) {
        inspectorContent.innerHTML = `
          <div class="inspector-header">
            <span class="entity-badge badge-link">🔗 Link</span>
            <span class="entity-title">${escapeHTML(link.a)} ↔ ${escapeHTML(link.b)}</span>
          </div>

          <div class="inspector-section">
            <div class="inspector-grid">
              <div class="inspector-field"><label>Node A</label><input type="text" value="${escapeHTML(link.a)}" disabled class="disabled-input"></div>
              <div class="inspector-field"><label>Node B</label><input type="text" value="${escapeHTML(link.b)}" disabled class="disabled-input"></div>
            </div>
            <div class="inspector-grid">
              <div class="inspector-field"><label>Delay (ms)</label><input type="number" id="inp-delay" value="${link.delay}" min="0" step="1"></div>
              <div class="inspector-field"><label>Queue Size</label><input type="number" id="inp-qs" value="${link.queue_size}" min="1" step="10"></div>
              <div class="inspector-field"><label>Bandwidth (Mbps)</label><input type="number" id="inp-bw" value="${link.bandwidth}" min="0.1" step="10"></div>
              <div class="inspector-field"><label>Loss (%)</label><input type="number" id="inp-loss" value="${link.loss}" min="0" max="100" step="0.1"></div>
            </div>
          </div>

          <div class="inspector-card">
            <button id="btn-toggle-backup" type="button" class="pcap-button ${link.backup ? "pcap-active" : "pcap-inactive"}">
              <span class="pcap-btn-icon">${link.backup ? "🔀" : "⚪"}</span>
              <span class="pcap-btn-label">Backup Path (MPTCP)</span>
              <span class="pcap-badge-state">${link.backup ? "BACKUP" : "PRIMARY"}</span>
            </button>
          </div>

          <button id="inp-delete-link" class="btn btn-danger-outline" style="width:100%;">
            <span>🗑️</span> Delete Link
          </button>
        `;

        document.getElementById("inp-delay").addEventListener("change", (e) => {
          link.delay = parseFloat(e.target.value) || 0;
          triggerValidationDebounced();
        });
        document.getElementById("inp-qs").addEventListener("change", (e) => {
          link.queue_size = parseInt(e.target.value, 10) || 1000;
          triggerValidationDebounced();
        });
        document.getElementById("inp-bw").addEventListener("change", (e) => {
          link.bandwidth = parseFloat(e.target.value) || 100;
          triggerValidationDebounced();
        });
        document.getElementById("inp-loss").addEventListener("change", (e) => {
          link.loss = parseFloat(e.target.value) || 0;
          triggerValidationDebounced();
        });
        
        const backupBtn = document.getElementById("btn-toggle-backup");
        if (backupBtn) {
          backupBtn.addEventListener("click", () => {
            link.backup = link.backup ? 0 : 1;
            renderInspector();
            triggerValidationDebounced();
          });
        }

        document.getElementById("inp-delete-link").addEventListener("click", () => {
          state.links.splice(state.selectedItem.index, 1);
          state.selectedItem = null;
          syncAttachedRouters();
          renderCanvas();
          renderInspector();
          triggerValidationDebounced();
        });
      }
    }
  }

  function triggerValidationDebounced() {
    clearTimeout(timer);
    timer = setTimeout(validateAndIPAM, 250);
  }

  async function validateAndIPAM() {
    const reqSeq = ++validationSeq;
    try {
      const payload = {
        routers: state.routers,
        hosts: state.hosts,
        links: state.links,
        transit_subnet: state.transit_subnet,
        change_netem: state.change_netem
      };

      const res = await fetch("/api/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (reqSeq < validationSeq) return;

      lastValidationValid = !!data.valid;
      currentUnreachablePairs = data.unreachable_pairs || [];
      unreachableRouterSet.clear();
      currentUnreachablePairs.forEach(p => {
        unreachableRouterSet.add(p[0]);
        unreachableRouterSet.add(p[1]);
      });

      if (data.valid) {
        if (btnExportTopo) {
          btnExportTopo.disabled = false;
          btnExportTopo.title = "Save topology configuration file";
        }
        validationOutput.innerHTML = `<span class="status-ok">✓ Topology Valid & IPAM Configured</span>`;
        if (data.model) {
          state.routers.forEach(r => {
            const upd = data.model.routers.find(x => x.name === r.name);
            if (upd && upd.ip) r.ip = upd.ip;
          });
          state.hosts.forEach(h => {
            const upd = data.model.hosts.find(x => x.name === h.name);
            if (upd && upd.ip) h.ip = upd.ip;
          });
        }
        renderIPAMTable(data.model || { routers: state.routers, hosts: state.hosts }, false);
        renderRoutes(data.routes, currentUnreachablePairs);
      } else {
        if (btnExportTopo) {
          btnExportTopo.disabled = true;
          btnExportTopo.title = "Cannot generate: validation errors present";
        }
        const errList = (data.errors || []).map(e => `<li>${escapeHTML(e)}</li>`).join("");
        validationOutput.innerHTML = `<span class="status-err">❌ Errors:</span><ul>${errList}</ul>`;
        renderIPAMTable(data.model || { routers: state.routers, hosts: state.hosts }, true);
        renderRoutes(data.routes, currentUnreachablePairs);
      }

      updatePreviewDisplay();
      if (!drag) renderCanvas();
    } catch (e) {
      if (reqSeq < validationSeq) return;
      lastValidationValid = false;
      if (btnExportTopo) {
        btnExportTopo.disabled = true;
        btnExportTopo.title = "Cannot generate: validation request failed";
      }
      validationOutput.innerHTML = `<span class="status-err">Validation request failed</span>`;
      renderIPAMTable({ routers: state.routers, hosts: state.hosts }, true);
      updatePreviewDisplay();
      if (!drag) renderCanvas();
    }
  }

  function renderIPAMTable(modelData, isStale = false) {
    let html = "";
    const staleClass = isStale ? "stale-row" : "";
    const staleMarker = isStale ? " ⚠" : "";
    const routers = modelData.routers || state.routers;
    const hosts = modelData.hosts || state.hosts;

    routers.forEach(r => {
      html += `<tr class="${staleClass}"><td><strong>${escapeHTML(r.name)}</strong></td><td>Router</td><td>-</td><td><code>${escapeHTML(r.ip || "—")}${staleMarker}</code> (${escapeHTML(r.subnet)})</td></tr>`;
    });
    hosts.forEach(h => {
      const rName = h.router || "—";
      html += `<tr class="${staleClass}"><td>${escapeHTML(h.name)}</td><td>${escapeHTML(h.role)}</td><td>${escapeHTML(rName)}</td><td><code>${escapeHTML(h.ip || "—")}${staleMarker}</code></td></tr>`;
    });
    ipamTbody.innerHTML = html || `<tr><td colspan="4" class="muted">No nodes configured</td></tr>`;
  }

  function renderRoutes(routes, unreachablePairs = []) {
    if (state.routers.length <= 1) {
      routesBox.innerHTML = `<p class="muted">No static inter-router routes needed.</p>`;
      return;
    }
    let html = "";
    if (unreachablePairs && unreachablePairs.length > 0) {
      const pairList = unreachablePairs.map(p => `${escapeHTML(p[0])} ↔ ${escapeHTML(p[1])}`).join(", ");
      html += `<div class="isolated-warning">⚠️ Isolated Routers Detected:<br>${pairList}</div>`;
    }
    if (routes && Object.keys(routes).length > 0) {
      for (const [r, list] of Object.entries(routes)) {
        if (list && list.length > 0) {
          html += `<div><strong>${escapeHTML(r)}:</strong></div>`;
          list.forEach(rt => {
            html += `<div>&nbsp;&nbsp;→ ${escapeHTML(rt.destination)} via ${escapeHTML(rt.via)}</div>`;
          });
        }
      }
    }
    routesBox.innerHTML = html || `<p class="muted">Directly connected networks only.</p>`;
  }

  function generateConfigTextLocal() {
    let lines = ["topoType:Generic\n"];
    state.routers.forEach(r => lines.push(`router:${r.name},${r.subnet},${r.ip || ""}\n`));
    state.hosts.forEach(h => lines.push(`${h.role}:${h.name},${h.router},${h.ip || ""}\n`));
    state.links.forEach(l => lines.push(`link:${l.a},${l.b},${l.delay},${l.queue_size},${l.bandwidth},${l.loss},${l.backup || 0}\n`));
    lines.push(`transitSubnet:${state.transit_subnet}\n`);
    lines.push(`changeNetem:${state.change_netem}\n`);
    return lines.join("");
  }

  function generateScenarioSpecLocal() {
    const base = (inputBaseName.value || "").trim() || "default";
    const topoFilename = `config/topo/topo_${base}`;
    const xpName = `xp_${base}`;

    const workloads = state.hosts.map(h => {
      const w = h.workload || { kind: "none", command: "" };
      return {
        node: h.name,
        kind: w.kind || "none",
        command: (w.command && w.command.trim()) ? w.command.trim() : null
      };
    });

    const pcapNodes = [...state.hosts, ...state.routers]
      .filter(n => n.pcap)
      .map(n => n.name);

    return {
      name: xpName,
      topo_file: topoFilename,
      xp_type: "workload_exp",
      duration: 10,
      mptcp: "no",
      pcap: "no",
      pcap_nodes: pcapNodes,
      congctrl: "cubic",
      workloads: workloads
    };
  }

  function updatePreviewDisplay() {
    if (state.previewMode === "scenario") {
      const spec = generateScenarioSpecLocal();
      configPreview.value = JSON.stringify(spec, null, 2);
    } else {
      const prefix = lastValidationValid ? "" : "# ⚠ UNVERIFIED — validation failed\n";
      configPreview.value = prefix + generateConfigTextLocal();
    }
  }

  async function exportTopoConfigFile(force = false) {
    if (!lastValidationValid) {
      alert("Cannot export topo: validation errors present. Please fix errors first.");
      return;
    }
    const base = (inputBaseName.value || "").trim();
    const filename = base ? `topo_${base}` : null;

    try {
      const payload = {
        filename: filename,
        force: force,
        routers: state.routers,
        hosts: state.hosts,
        links: state.links,
        transit_subnet: state.transit_subnet,
        change_netem: state.change_netem
      };

      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();

      if (res.status === 409) {
        if (confirm(`Topology file '${data.filename}' already exists. Overwrite?`)) {
          return exportTopoConfigFile(true);
        }
        return;
      }

      if (data.success) {
        alert(`Topology config exported successfully:\n${data.filename || data.path}`);
        loadSavedConfigsList();
      } else {
        alert(`Failed to save topology: ${data.errors ? data.errors.join("\n") : (data.error || "Unknown error")}`);
      }
    } catch (e) {
      alert("Export topology request failed");
    }
  }

  async function exportScenarioJSONFile(force = false) {
    const spec = generateScenarioSpecLocal();
    spec.force = force;

    try {
      const res = await fetch("/api/export/scenario", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(spec)
      });
      const data = await res.json();

      if (res.status === 409) {
        if (confirm(`Scenario file '${data.filename}' already exists. Overwrite?`)) {
          return exportScenarioJSONFile(true);
        }
        return;
      }

      if (data.success) {
        alert(`Scenario exported successfully:\n${data.filename || data.path}`);
        loadSavedConfigsList();
      } else {
        alert(`Failed to export scenario: ${data.error || JSON.stringify(data.report?.errors)}`);
      }
    } catch (e) {
      alert("Export scenario request failed");
    }
  }

  async function loadSavedConfigsList() {
    try {
      const res = await fetch("/api/configs");
      const data = await res.json();
      if (data.configs && configSelect) {
        configSelect.innerHTML = `<option value="">-- Load Saved Topo --</option>` +
          data.configs.map(f => `<option value="${escapeHTML(f)}">${escapeHTML(f)}</option>`).join("");
      }

      const resXp = await fetch("/api/configs/xp");
      const dataXp = await resXp.json();
      if (dataXp.configs && xpConfigSelect) {
        xpConfigSelect.innerHTML = `<option value="">-- Load Saved Exp --</option>` +
          dataXp.configs.map(f => `<option value="${escapeHTML(f)}">${escapeHTML(f)}</option>`).join("");
      }
    } catch (e) {}
  }

  async function loadConfigFile(filename) {
    if (!filename) return;
    try {
      const res = await fetch(`/api/configs/${encodeURIComponent(filename)}`);
      const data = await res.json();
      if (!res.ok || !data.model) {
        alert(`Failed to load topo '${filename}':\n${data.error || "Invalid configuration"}`);
        return;
      }

      // Update base name if filename matches topo_<base>
      if (filename.startsWith("topo_")) {
        const base = filename.slice("topo_".length);
        if (inputBaseName) {
          inputBaseName.value = base;
          updatePathPreviews();
        }
      }

      state.transit_subnet = data.model.transit_subnet || "172.31.0.0/16";
      state.change_netem = !!data.model.change_netem;

      const containerW = canvasContainer ? canvasContainer.clientWidth : 800;
      const margin = 20;
      const perRow = Math.max(1, Math.floor((containerW - margin * 2) / (NODE_W + 50)));

      const routerMap = {};
      data.model.routers.forEach((r, i) => {
        const col = i % perRow;
        const row = Math.floor(i / perRow);
        const rawX = margin + 30 + col * (NODE_W + 50);
        const rawY = 40 + row * (NODE_H + 130);
        const pt = clampWorld(rawX, rawY);
        routerMap[r.name] = { ...r, x: pt.x, y: pt.y };
      });
      state.routers = Object.values(routerMap);

      const hostList = [];
      state.routers.forEach(r => {
        const attachedHosts = data.model.hosts.filter(h => h.router === r.name);
        attachedHosts.forEach((h, hIdx) => {
          const offsetX = (hIdx - (attachedHosts.length - 1) / 2) * (NODE_W + 10);
          const rawX = r.x + offsetX;
          const rawY = r.y + NODE_H + 25;
          const pt = clampWorld(rawX, rawY);
          hostList.push({
            ...h,
            x: pt.x,
            y: pt.y,
            workload: {
              kind: h.role === "client" ? "iperf3_client" : (h.role === "server" ? "iperf3_server" : "none"),
              command: ""
            }
          });
        });
      });
      const orphanHosts = data.model.hosts.filter(h => !routerMap[h.router]);
      orphanHosts.forEach((h, oIdx) => {
        const rawX = margin + oIdx * (NODE_W + 20);
        const rawY = 350;
        const pt = clampWorld(rawX, rawY);
        hostList.push({
          ...h,
          x: pt.x,
          y: pt.y,
          workload: {
            kind: h.role === "client" ? "iperf3_client" : (h.role === "server" ? "iperf3_server" : "none"),
            command: ""
          }
        });
      });
      state.hosts = hostList;
      state.links = data.model.links || [];
      state.selectedItem = null;
      state.currentTopoFilename = filename;

      // Reset viewport offset to origin on topology load (WP-9, C-1b)
      state.panX = 0;
      state.panY = 0;

      // D-11 Auto Rehydration check for matching xp_<base>
      if (filename.startsWith("topo_")) {
        const base = filename.slice("topo_".length);
        try {
          const xpRes = await fetch(`/api/configs/xp/xp_${encodeURIComponent(base)}`);
          if (xpRes.ok) {
            const xpData = await xpRes.json();
            if (xpData.spec) {
              const specTopo = (xpData.spec.topo_file || "").trim();
              const matchesTopo = !specTopo ||
                                  specTopo === filename ||
                                  specTopo === `config/topo/${filename}` ||
                                  specTopo.endsWith(`/${filename}`) ||
                                  specTopo.endsWith(`_${base}`);
              if (matchesTopo) {
                if (xpData.spec.workloads) {
                  rehydrateWorkloads(xpData.spec.workloads);
                }
                rehydratePcapNodes(xpData.spec.pcap_nodes || []);
              }
            }
          }
        } catch (ignore) {}
      }

      renderCanvas();
      renderInspector();
      triggerValidationDebounced();
    } catch (e) {
      alert(`Failed to load topo '${filename}': Network or server error`);
    }
  }

  async function loadScenarioFile(filename) {
    if (!filename) return;
    try {
      const res = await fetch(`/api/configs/xp/${encodeURIComponent(filename)}`);
      const data = await res.json();
      if (!res.ok || !data.spec) {
        alert(`Failed to load scenario '${filename}':\n${data.error || "Invalid scenario file"}`);
        return;
      }

      if (data.spec) {
        const currentTopo = state.currentTopoFilename || (inputBaseName ? `topo_${inputBaseName.value.trim()}` : "");
        const specTopo = (data.spec.topo_file || "").trim();
        if (specTopo && currentTopo) {
          const matches = specTopo === currentTopo ||
                          specTopo === `config/topo/${currentTopo}` ||
                          specTopo.endsWith(`/${currentTopo}`);
          if (!matches) {
            const proceed = confirm(
              `Scenario '${filename}' references topology '${specTopo}', but currently loaded topology is '${currentTopo}'.\n\nDo you want to proceed and apply matching node workloads?`
            );
            if (!proceed) return;
          }
        }

        let appliedWorkloads = 0;
        if (data.spec.workloads) {
          appliedWorkloads = rehydrateWorkloads(data.spec.workloads);
        }
        const appliedPcaps = rehydratePcapNodes(data.spec.pcap_nodes || []);
        renderInspector();
        updatePreviewDisplay();
        alert(`Loaded scenario '${filename}' and applied scenario properties (${appliedWorkloads} workload(s), ${appliedPcaps} pcap node(s)).`);
      }
    } catch (e) {
      alert(`Failed to load scenario '${filename}': Network or server error`);
    }
  }

  function rehydrateWorkloads(workloadsList) {
    let applied = 0;
    workloadsList.forEach(w => {
      const host = state.hosts.find(h => h.name === w.node);
      if (host) {
        host.workload = {
          kind: w.kind || "none",
          command: w.command || ""
        };
        applied++;
      }
    });
    return applied;
  }

  function rehydratePcapNodes(pcapNodesList) {
    let applied = 0;
    const nameSet = new Set(pcapNodesList || []);
    [...state.hosts, ...state.routers].forEach(n => {
      const isPcap = nameSet.has(n.name);
      n.pcap = isPcap;
      if (isPcap) applied++;
    });
    return applied;
  }

  // Initialize WebUI
  renderCanvas();
  renderInspector();
  loadSavedConfigsList();
  triggerValidationDebounced();
});
