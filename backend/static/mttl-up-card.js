class MTTLUpCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._registry = new Map();
    this._registryLoaded = false;
    this._registryLoading = null;
    this._signature = "";
    this._locked = true;
  }

  static getStubConfig() {
    return { mac: "000000000000", layout: "1x4", all_control: true };
  }

  static getConfigElement() {
    return document.createElement("mttl-up-card-editor");
  }

  setConfig(config) {
    const mac = String(config.mac || "").replace(/[^0-9a-f]/gi, "").toLowerCase();
    if (!/^[0-9a-f]{12}$/.test(mac)) {
      throw new Error("MTTL-UP card: mac must contain the full 12 hexadecimal characters");
    }
    const legacyLayout = Boolean(config.compact) ? "1x4" : "2x2";
    const layout = ["1x4", "2x2"].includes(config.layout) ? config.layout : legacyLayout;
    this._config = {
      mac,
      name: String(config.name || "").trim(),
      layout,
      all_control: config.all_control !== false,
    };
    this._locked = true;
    this._signature = "";
    this._registry.clear();
    this._registryLoaded = false;
    this._registryLoading = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    this._seedRegistryFromHass();
    this._ensureRegistry();
    const signature = this._stateSignature();
    if (signature !== this._signature) {
      this._signature = signature;
      this._render();
    }
  }

  getCardSize() {
    return this._config?.layout === "1x4" ? 2 : 3;
  }

  _uid(type, channel = null) {
    const base = `mttl_${this._config.mac}`;
    if (type === "switch") return `${base}_switch_${channel}`;
    if (type === "power") return channel == null ? `${base}_power_total` : `${base}_power_${channel}`;
    if (type === "energy") return channel == null ? `${base}_energy_total` : `${base}_energy_${channel}`;
    if (type === "temperature") return `${base}_temperature_${channel}`;
    if (type === "voltage") return `${base}_voltage`;
    if (type === "childlock") return `${base}_childlock_${channel}`;
    return "";
  }

  _neededUids() {
    const out = [this._uid("power"), this._uid("energy"), this._uid("voltage"), this._uid("childlock", 0)];
    for (let n = 1; n <= 4; n += 1) {
      out.push(this._uid("switch", n), this._uid("power", n), this._uid("energy", n), this._uid("temperature", n), this._uid("childlock", n));
    }
    return out;
  }

  _seedRegistryFromHass() {
    const entries = this._hass?.entities || {};
    for (const [entityId, entry] of Object.entries(entries)) {
      if (!entry?.unique_id) continue;
      this._registry.set(entry.unique_id, { entity_id: entityId, ...entry });
    }
  }

  _ensureRegistry() {
    if (!this._hass?.callWS || this._registryLoaded || this._registryLoading) return;
    const missing = this._neededUids().some(uid => !this._registry.has(uid));
    if (!missing) {
      this._registryLoaded = true;
      return;
    }
    this._registryLoading = this._hass.callWS({ type: "config/entity_registry/list" })
      .then(rows => {
        for (const entry of rows || []) {
          if (entry?.unique_id) this._registry.set(entry.unique_id, entry);
        }
        this._registryLoaded = true;
        this._registryLoading = null;
        this._signature = "";
        this._render();
      })
      .catch(() => {
        this._registryLoaded = true;
        this._registryLoading = null;
        this._signature = "";
        this._render();
      });
  }

  _entityId(uid) {
    const direct = this._registry.get(uid)?.entity_id;
    if (direct && this._hass?.states?.[direct]) return direct;
    const entries = this._hass?.entities || {};
    for (const [entityId, entry] of Object.entries(entries)) {
      if (entry?.unique_id === uid) {
        this._registry.set(uid, { entity_id: entityId, ...entry });
        return entityId;
      }
    }
    return direct || null;
  }

  _entity(uid) {
    const id = this._entityId(uid);
    return id ? this._hass?.states?.[id] : null;
  }

  _registryEntry(uid) {
    const id = this._entityId(uid);
    const current = id ? this._hass?.entities?.[id] : null;
    return current || this._registry.get(uid) || null;
  }

  _available(entity) {
    return Boolean(entity && !["unknown", "unavailable"].includes(entity.state));
  }

  _format(entity, fallback = "—") {
    if (!this._available(entity)) return fallback;
    const unit = entity.attributes?.unit_of_measurement || "";
    return `${entity.state}${unit ? ` ${unit}` : ""}`;
  }

  _formatFixed(entity, digits, fallback = "—") {
    if (!this._available(entity)) return fallback;
    const value = Number(entity.state);
    if (!Number.isFinite(value)) return fallback;
    const unit = entity.attributes?.unit_of_measurement || "";
    return `${value.toFixed(digits)}${unit ? ` ${unit}` : ""}`;
  }

  _number(entity) {
    if (!this._available(entity)) return null;
    const value = Number(entity.state);
    return Number.isFinite(value) ? value : null;
  }

  _channelName(n) {
    const uid = this._uid("switch", n);
    const entry = this._registryEntry(uid);
    if (entry?.name) return entry.name;
    const entity = this._entity(uid);
    const friendly = entity?.attributes?.friendly_name || "";
    const deviceName = this._deviceName(false);
    if (friendly && deviceName && friendly.toLowerCase().startsWith(deviceName.toLowerCase())) {
      const trimmed = friendly.slice(deviceName.length).replace(/^\s*[-–—:]?\s*/, "");
      if (trimmed) return trimmed;
    }
    if (entry?.original_name) return entry.original_name;
    return friendly || `Outlet ${n}`;
  }

  _deviceName(useConfig = true) {
    if (useConfig && this._config?.name) return this._config.name;
    const entry = this._registryEntry(this._uid("switch", 1));
    const device = entry?.device_id ? this._hass?.devices?.[entry.device_id] : null;
    return device?.name_by_user || device?.name || `MTTL ${this._config.mac.slice(-6).toUpperCase()}`;
  }

  _stateSignature() {
    if (!this._config || !this._hass) return "";
    const parts = [this._deviceName()];
    for (const uid of this._neededUids()) {
      const id = this._entityId(uid) || "missing";
      const entity = id !== "missing" ? this._hass.states?.[id] : null;
      const reg = this._registryEntry(uid);
      parts.push(`${uid}:${id}:${entity?.state || "missing"}:${entity?.attributes?.friendly_name || ""}:${reg?.name || ""}:${reg?.original_name || ""}`);
    }
    return parts.join("|");
  }

  _escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[ch]);
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const switches = [1, 2, 3, 4].map(n => this._entity(this._uid("switch", n)));
    const powers = [1, 2, 3, 4].map(n => this._entity(this._uid("power", n)));
    const temperatures = [1, 2, 3, 4].map(n => this._entity(this._uid("temperature", n)));
    const totalPower = this._entity(this._uid("power"));
    const voltage = this._entity(this._uid("voltage"));
    const childLocks = [1, 2, 3, 4].map(n => this._entity(this._uid("childlock", n)));
    const masterChildLock = this._entity(this._uid("childlock", 0));
    const switchIds = [1, 2, 3, 4].map(n => this._entityId(this._uid("switch", n))).filter(Boolean);
    const usableCount = switches.filter(entity => this._available(entity)).length;
    const onCount = switches.filter(entity => entity?.state === "on").length;
    const allState = onCount === 4 ? "ON" : onCount ? "PARTIAL" : "OFF";
    const allControl = this._config.all_control !== false;
    const title = this._deviceName();
    const channels = [1, 2, 3, 4].map((n, index) => {
      const entity = switches[index];
      const power = powers[index];
      const temperature = temperatures[index];
      const childLock = childLocks[index];
      const childLocked = childLock?.state === "on";
      const usable = this._available(entity);
      const active = entity?.state === "on";
      return `<button class="channel ${active ? "is-on" : "is-off"} ${usable ? "" : "unavailable"} ${this._locked ? "control-locked" : "control-unlocked"}"
        data-switch-uid="${this._uid("switch", n)}" data-power-uid="${this._uid("power", n)}" ${usable ? "" : "disabled"}
        aria-disabled="${this._locked || !usable ? "true" : "false"}" title="${this._locked ? "제어 잠김 — 상단 잠금을 해제하세요" : "짧게: ON/OFF · 길게: Power 상세"}">
        <div class="channel-top"><ha-icon class="power-socket-icon" icon="mdi:power-socket-de"></ha-icon>${childLocked ? `<ha-icon class="child-lock-indicator" icon="mdi:lock" title="${this._escape(this._channelName(n))} 물리 버튼 잠김" aria-label="${this._escape(this._channelName(n))} 물리 버튼 잠김"></ha-icon>` : ""}</div>
        <span class="channel-name">${this._escape(this._channelName(n))}</span>
        <div class="channel-metrics"><strong>${this._escape(this._format(power, "— W"))}</strong><span class="channel-temp">${this._escape(this._formatFixed(temperature, 0, "— °C"))}</span></div>
        <span class="state-pill ${active ? "on" : "off"}">${active ? "ON" : "OFF"}</span>
      </button>`;
    }).join("");

    this.shadowRoot.innerHTML = `<style>
      :host{display:block}ha-card{padding:15px;color:var(--primary-text-color);overflow:hidden}
      .header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
      .title{font-size:18px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .header-right{display:flex;align-items:center;gap:7px;flex:0 0 auto}
      .lock-toggle{box-sizing:border-box;width:26px;height:26px;min-width:26px;min-height:26px;border:1px solid var(--divider-color);border-radius:8px;background:var(--secondary-background-color);color:var(--secondary-text-color);display:inline-flex;align-items:center;justify-content:center;cursor:pointer;line-height:1;flex:0 0 26px;user-select:none;-webkit-user-select:none}
      .lock-toggle ha-icon{display:block;width:17px;height:17px;--mdc-icon-size:17px}.lock-toggle.locked{color:var(--primary-color);border-color:var(--primary-color)}.lock-toggle.unlocked{color:var(--secondary-text-color);border-color:var(--divider-color)}
      .availability{display:flex;align-items:center;gap:6px;color:var(--secondary-text-color);font-size:12px;white-space:nowrap}
      .status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--error-color,#d94b4b)}
      .status-dot.online{background:var(--success-color,#22a35a)}
      .summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:10px}.summary.no-all{grid-template-columns:repeat(3,minmax(0,1fr))}
      .summary button{box-sizing:border-box;min-width:0;height:48px;padding:4px 8px;border:1px solid var(--divider-color);border-radius:10px;background:var(--secondary-background-color);color:var(--primary-text-color);font:inherit;cursor:pointer;display:flex;flex-direction:column;justify-content:center;align-items:center}
      .summary span{font-size:11px;color:var(--secondary-text-color)}.summary strong{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
      .summary .all-label{display:flex;align-items:center;justify-content:center;gap:5px}.summary .all .all-child-lock{width:16px;height:16px;--mdc-icon-size:16px;color:var(--primary-text-color);pointer-events:none}.summary .all.on{border-color:var(--success-color,#22a35a);color:var(--success-color,#15803d)}.summary .all.partial{border-color:var(--primary-color);color:var(--primary-color)}.summary .all.control-locked{opacity:1;cursor:default;background:var(--card-background-color);border-color:var(--divider-color);color:var(--primary-text-color);box-shadow:none}.summary .all.control-locked span{color:var(--secondary-text-color)}.summary .all.control-locked strong{color:var(--primary-text-color)}.summary .all.control-unlocked.on{background:color-mix(in srgb,var(--success-color,#22a35a) 8%,var(--card-background-color))}.summary .all.control-unlocked.partial{background:color-mix(in srgb,var(--primary-color) 8%,var(--card-background-color))}.summary button:disabled{opacity:.45;cursor:not-allowed}
      .channels{display:grid;gap:8px}.channels.layout-1x4{grid-template-columns:repeat(4,minmax(0,1fr))}.channels.layout-2x2{grid-template-columns:repeat(2,minmax(0,1fr))}.channel{box-sizing:border-box;min-width:0;min-height:112px;padding:9px 7px;border:1px solid var(--divider-color);border-radius:11px;background:var(--card-background-color);color:var(--primary-text-color);font:inherit;cursor:pointer;display:flex;flex-direction:column;justify-content:space-between;align-items:center;gap:5px;transition:transform .08s,border-color .15s,background .15s,box-shadow .15s}.channel:active{transform:scale(.98)}.channel.unavailable{opacity:.45;cursor:not-allowed}.channel.control-locked:not(.unavailable){cursor:default;background:var(--card-background-color);border-color:var(--divider-color);box-shadow:none}.channel.control-unlocked.is-on{border-color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 8%,var(--card-background-color));box-shadow:0 0 0 1px color-mix(in srgb,var(--primary-color) 10%,transparent)}.channel.control-unlocked.is-off{background:var(--card-background-color)}.channel-top{display:flex;align-items:center;justify-content:center;gap:6px;min-height:24px;width:100%}.channel ha-icon{width:20px;height:20px;color:var(--secondary-text-color)}.channel.is-on .power-socket-icon{color:var(--success-color,#22a35a)}.child-lock-indicator{width:18px!important;height:18px!important;--mdc-icon-size:18px;color:var(--primary-text-color)!important;pointer-events:none}.channel-name{width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;font-weight:650;text-align:center;color:var(--primary-text-color)}.channel-metrics{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;line-height:1.05}.channel strong{font-size:13px;color:var(--primary-text-color)}.channel-temp{font-size:11px;color:var(--secondary-text-color);white-space:nowrap}.state-pill{display:inline-flex;align-items:center;justify-content:center;min-width:44px;height:23px;padding:0 10px;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.03em;border:1px solid transparent}.state-pill.on{background:color-mix(in srgb,var(--success-color,#22a35a) 18%,var(--card-background-color));border-color:color-mix(in srgb,var(--success-color,#22a35a) 55%,var(--divider-color));color:var(--success-color,#15803d)}.state-pill.off{background:var(--card-background-color);border-color:var(--divider-color);color:var(--secondary-text-color)}
      .hint{margin-top:9px;color:var(--secondary-text-color);font-size:11px;text-align:center}
      @media(max-width:600px){.summary{grid-template-columns:repeat(2,minmax(0,1fr))}.summary.no-all{grid-template-columns:repeat(2,minmax(0,1fr))}.channel{min-height:118px}.channels.layout-1x4{gap:5px}.channels.layout-1x4 .channel{padding:8px 4px}.channels.layout-1x4 .channel-name{font-size:12px}.channels.layout-1x4 .state-pill{min-width:38px;padding:0 7px}}
    </style><ha-card>
      <div class="header"><div class="title">${this._escape(title)}</div><div class="header-right"><span class="lock-toggle ${this._locked ? "locked" : "unlocked"}" data-lock role="button" tabindex="0" title="${this._locked ? "UI 제어 잠금 해제" : "UI 제어 잠금"}" aria-label="${this._locked ? "UI 제어 잠금 해제" : "UI 제어 잠금"}"><ha-icon icon="${this._locked ? "mdi:shield-lock" : "mdi:shield-outline"}"></ha-icon></span><div class="availability"><span class="status-dot ${usableCount ? "online" : ""}"></span>${usableCount ? "Online" : "Offline"}</div></div></div>
      <div class="summary ${allControl ? "" : "no-all"}">
        <button data-more-uid="${this._uid("power")}"><span>Total</span><strong>${this._escape(this._format(totalPower, "— W"))}</strong></button>
        <button data-more-uid="${this._uid("energy")}"><span>Meter</span><strong>${this._escape(this._formatFixed(this._entity(this._uid("energy")), 3, "— kWh"))}</strong></button>
        <button data-more-uid="${this._uid("voltage")}"><span>Voltage</span><strong>${this._escape(this._formatFixed(voltage, 1, "— V"))}</strong></button>
        ${allControl ? `<button class="all ${onCount === 4 ? "on" : onCount ? "partial" : ""} ${this._locked ? "control-locked" : "control-unlocked"}" data-all ${switchIds.length ? "" : "disabled"} aria-disabled="${this._locked || !switchIds.length ? "true" : "false"}"><span class="all-label">ALL${masterChildLock?.state === "on" ? `<ha-icon class="all-child-lock" icon="mdi:lock" title="${this._escape(title)} Button Lock · 물리 버튼 잠김" aria-label="${this._escape(title)} Button Lock · 물리 버튼 잠김"></ha-icon>` : ""}</span><strong>${allState}</strong></button>` : ""}
      </div>
      <div class="channels layout-${this._config.layout}">${channels}</div>
      ${this._registryLoaded && switchIds.length < 4 ? '<div class="hint">일부 MTTL entity를 찾지 못했습니다. HA Discovery 동기화를 확인하세요.</div>' : ''}
    </ha-card>`;

    this.shadowRoot.querySelectorAll("[data-more-uid]").forEach(button => {
      button.addEventListener("click", () => this._moreInfoByUid(button.dataset.moreUid));
    });
    const lock = this.shadowRoot.querySelector("[data-lock]");
    const toggleLock = () => { this._locked = !this._locked; this._render(); };
    lock?.addEventListener("click", toggleLock);
    lock?.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleLock(); }
    });
    this.shadowRoot.querySelector("[data-all]")?.addEventListener("click", () => {
      if (this._locked) return;
      const ids = [1, 2, 3, 4].map(n => this._entityId(this._uid("switch", n))).filter(Boolean);
      if (ids.length) this._callSwitch(onCount ? "turn_off" : "turn_on", ids);
    });
    this.shadowRoot.querySelectorAll(".channel").forEach(button => this._bindChannel(button));
  }

  _callSwitch(service, entityIds) {
    if (!this._hass || !entityIds?.length) return;
    this._hass.callService("switch", service, { entity_id: entityIds });
  }

  _bindChannel(button) {
    let timer = null;
    let longPressed = false;
    const cancel = () => {
      if (timer) clearTimeout(timer);
      timer = null;
    };
    button.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      longPressed = false;
      cancel();
      timer = setTimeout(() => {
        timer = null;
        longPressed = true;
        this._moreInfoByUid(button.dataset.powerUid);
      }, 600);
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(type => button.addEventListener(type, cancel));
    button.addEventListener("contextmenu", event => {
      if (longPressed) event.preventDefault();
    });
    button.addEventListener("click", event => {
      if (longPressed) {
        longPressed = false;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      if (this._locked) return;
      const id = this._entityId(button.dataset.switchUid);
      const entity = id ? this._hass?.states?.[id] : null;
      if (id && this._available(entity)) this._callSwitch(entity.state === "on" ? "turn_off" : "turn_on", [id]);
    });
  }

  _moreInfoByUid(uid) {
    const entityId = this._entityId(uid);
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      detail: { entityId }, bubbles: true, composed: true,
    }));
  }
}

class MTTLUpCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
  }

  setConfig(config) {
    this._config = { mac: "", name: "", layout: "1x4", all_control: true, ...config };
    if (!["1x4", "2x2"].includes(this._config.layout)) this._config.layout = this._config.compact ? "1x4" : "2x2";
    this._render();
  }

  _render() {
    if (!this._config) return;
    const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[ch]);
    this.shadowRoot.innerHTML = `<style>
      .editor{display:grid;gap:14px;padding:8px 0}.field{display:grid;gap:5px}.label{font-size:13px;font-weight:500;color:var(--primary-text-color)}input{box-sizing:border-box;width:100%;height:42px;padding:0 10px;border:1px solid var(--divider-color);border-radius:6px;background:var(--card-background-color);color:var(--primary-text-color);font:inherit}.row{display:flex;align-items:center;justify-content:space-between;gap:12px}.select{box-sizing:border-box;min-width:110px;height:38px;padding:0 9px;border:1px solid var(--divider-color);border-radius:6px;background:var(--card-background-color);color:var(--primary-text-color);font:inherit}.help{font-size:12px;color:var(--secondary-text-color)}
    </style><div class="editor">
      <label class="field"><span class="label">MTTL MAC address</span><input id="mac" value="${esc(this._config.mac || "")}" maxlength="17" placeholder="001122334455"><span class="help">전체 12자리 MAC. 콜론/하이픈은 자동 제거됩니다.</span></label>
      <label class="field"><span class="label">Card title override (optional)</span><input id="name" value="${esc(this._config.name || "")}" placeholder="HA 장치 이름 사용"></label>
      <label class="row"><span class="label">ALL ON/OFF 제어 표시</span><ha-switch id="allControl" ${this._config.all_control !== false ? "checked" : ""}></ha-switch></label>
      <label class="row"><span class="label">채널 배열</span><select class="select" id="layout"><option value="1x4" ${this._config.layout === "1x4" ? "selected" : ""}>1 × 4</option><option value="2x2" ${this._config.layout === "2x2" ? "selected" : ""}>2 × 2</option></select></label>
      <span class="help">방패 아이콘은 카드 UI 제어 잠금입니다. 잠긴 Child Lock만 채널/ALL에 자물쇠로 상태 표시되며, Child Lock 제어는 카드에서 하지 않습니다. 페이지를 다시 열면 UI 제어는 다시 잠깁니다.</span>
    </div>`;
    this.shadowRoot.querySelector("#mac").addEventListener("change", event => this._change("mac", event.target.value));
    this.shadowRoot.querySelector("#name").addEventListener("change", event => this._change("name", event.target.value));
    this.shadowRoot.querySelector("#allControl").addEventListener("change", event => this._change("all_control", event.target.checked));
    this.shadowRoot.querySelector("#layout").addEventListener("change", event => this._change("layout", event.target.value));
  }

  _change(key, value) {
    const next = { ...this._config };
    if (key === "mac") value = String(value || "").replace(/[^0-9a-f]/gi, "").slice(0, 12).toLowerCase();
    next[key] = value;
    this._config = next;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: next }, bubbles: true, composed: true }));
  }
}

if (!customElements.get("mttl-up-card")) customElements.define("mttl-up-card", MTTLUpCard);
if (!customElements.get("mttl-up-card-editor")) customElements.define("mttl-up-card-editor", MTTLUpCardEditor);
window.customCards = window.customCards || [];
if (!window.customCards.some(card => card.type === "mttl-up-card")) {
  window.customCards.push({
    type: "mttl-up-card",
    name: "MTTL-UP Power Strip",
    description: "MTTL-W01 4-channel control and power card using stable MQTT unique IDs.",
  });
}
