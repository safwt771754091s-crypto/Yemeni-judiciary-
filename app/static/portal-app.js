/* ==========================================================================
   المنصة القضائية القانونية اليمنية — تطبيق البوابة (مشترك بين كل الأدوار)
   Each role page sets window.PORTAL_CONFIG and loads this file. It renders the
   top bar, overview, workspace, grounded drafting and the AI assistant.
   ========================================================================== */
(function () {
  "use strict";

  const P = window.Portal;
  const CFG = window.PORTAL_CONFIG || {};
  const PORTAL = CFG.portal || "researcher";
  const KIND_LABELS = {
    session: "جلسة", memo: "مذكرة", contract: "عقد",
    investigation: "محضر تحقيق", charge: "قرار اتهام", note: "ملاحظة بحث",
  };
  const DRAFT_LABELS = {
    contract: "عقد", defence: "مذكرة دفاع", charge: "قرار اتهام",
    memo: "مذكرة قانونية", judgment_note: "حيثيات حكم",
  };
  const STATUS_LABELS = {
    draft: "مسودة", active: "نشط", scheduled: "مجدول", closed: "مغلق", archived: "مؤرشف",
  };

  let USER = null;
  let PORTAL_DATA = null;
  let ITEMS = [];
  let CONVERSATIONS = [];
  let CURRENT_CONV = null;

  // ----------------------------------------------------------------- shell
  function shell() {
    return `
      <header class="topbar">
        <div class="brand"><img src="/ui/icons/icon-192.png" alt=""><span id="brandTitle">${P.esc(CFG.title || "بوابة")}</span></div>
        <span class="role-badge" id="roleBadge">—</span>
        <div class="spacer"></div>
        <nav class="nav" id="nav"></nav>
        <span class="user-chip" id="userChip"></span>
        <button class="ghost sm hide" id="installBtn">تثبيت التطبيق</button>
        <button class="ghost sm" id="logoutBtn">خروج</button>
      </header>
      <div class="wrap" id="app" style="display:none">
        <div id="offlineNote"></div>
        <div class="grid3" id="statsRow"></div>
        <div class="card" id="overviewCard">
          <h2>نظرة عامة</h2>
          <div id="overviewBody" class="muted">جارٍ التحميل…</div>
        </div>
        <div class="card" id="workspaceCard">
          <h2 id="workspaceTitle">مساحة العمل</h2>
          <div class="grid" style="align-items:start">
            <div>
              <h3>عناصر العمل</h3>
              <div id="itemsList" class="muted">جارٍ التحميل…</div>
            </div>
            <div>
              <h3>إضافة عنصر</h3>
              <div id="itemForm"></div>
            </div>
          </div>
        </div>
        <div class="card" id="draftCard">
          <h2>الصياغة القانونية المسندة</h2>
          <div class="row">
            <div>
              <label>نوع المستند</label>
              <select id="draftType"></select>
              <label>الأطراف (اختياري)</label>
              <input id="draftParties" placeholder="مثال: الطرف الأول / الطرف الثاني">
              <label>موجز الصياغة</label>
              <textarea id="draftBrief" placeholder="اكتب وصفاً موجزاً للوقائع والمطلوب صياغته…"></textarea>
              <div class="actions"><button class="primary" id="draftBtn">صياغة المستند</button></div>
              <div class="spinner" id="draftSpinner"></div>
            </div>
            <div>
              <h3>المستند</h3>
              <div class="md" id="draftOut"><span class="muted">لم تصغ أي مستند بعد.</span></div>
              <div id="draftNotes"></div>
            </div>
          </div>
        </div>
        <div class="card" id="assistantCard">
          <h2>المساعد القانوني الذكي</h2>
          <div class="grid" style="grid-template-columns:280px 1fr;align-items:start">
            <div class="chat-side">
              <button class="ghost sm" id="newConvBtn" style="width:100%;margin-bottom:10px">+ محادثة جديدة</button>
              <div id="convList" class="muted">جارٍ التحميل…</div>
            </div>
            <div>
              <div class="chat">
                <div class="chat-log" id="chatLog"></div>
                <form class="chat-form" id="chatForm">
                  <textarea id="chatInput" placeholder="اكتب سؤالك القانوني…" rows="1"></textarea>
                  <button class="primary" type="submit" id="chatSend">إرسال</button>
                </form>
              </div>
            </div>
          </div>
        </div>
        <div class="card" id="adminCard" style="display:none">
          <h2>مراقبة النظام وسجل التدقيق</h2>
          <div id="adminBody" class="muted">—</div>
        </div>
      </div>
      <div class="login-wrap" id="loginWrap" style="display:none">
        <div class="login-card">
          <div class="logo"><img src="/ui/icons/icon-192.png" alt=""><h1>${P.esc(CFG.title || "بوابة")}</h1></div>
          <p class="sub">سجّل الدخول للوصول إلى بوابة ${P.esc(CFG.title || "")}.</p>
          <label>اسم المستخدم</label><input id="loginUser" autocomplete="username">
          <label>كلمة المرور</label><input id="loginPass" type="password" autocomplete="current-password">
          <div class="err hide" id="loginErr"></div>
          <div class="actions"><button class="primary" id="loginBtn">دخول</button></div>
        </div>
      </div>
      <footer>منصة الذكاء الاصطناعي القانوني — الجمهورية اليمنية · المخرجات مسودات آلية تحتاج مراجعة بشرية.</footer>
    `;
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    document.body.innerHTML = shell();
    P.pwa.register();
    P.pwa.wireInstallButton("#installBtn");
    document.getElementById("logoutBtn").onclick = () => P.auth.logout();
    document.getElementById("loginBtn").onclick = doLogin;
    document.getElementById("loginPass").addEventListener("keydown", (e) => {
      if (e.key === "Enter") doLogin();
    });

    USER = await P.auth.requireSession();
    if (!USER) return;
    // Refuse to render a page this role cannot open, even if the server gate
    // were bypassed; the two checks share the same permission registry.
    if (!P.auth.guardPage(USER)) return;
    await enterApp();
  }

  async function doLogin() {
    const err = document.getElementById("loginErr");
    err.classList.add("hide");
    try {
      await P.auth.login(
        document.getElementById("loginUser").value.trim(),
        document.getElementById("loginPass").value
      );
      USER = await P.auth.me();
      if (!P.auth.guardPage(USER)) return;
      await enterApp();
    } catch (e) {
      err.textContent = e.message;
      err.classList.remove("hide");
    }
  }

  async function enterApp() {
    document.getElementById("loginWrap").style.display = "none";
    document.getElementById("app").style.display = "block";
    renderNav();
    try {
      PORTAL_DATA = await P.api.get("/portals/me");
      renderHeader();
      renderOverview();
      if (CFG.assistantOnly) {
        ["workspaceCard", "draftCard", "overviewCard", "statsRow"].forEach((id) => {
          const node = document.getElementById(id);
          if (node) node.style.display = "none";
        });
      } else {
        await loadItems();
        renderItemForm();
        renderDraftOptions();
      }
      await loadConversations();
    } catch (e) {
      P.toast(e.message, "err");
    }
    if (!CFG.assistantOnly && (PORTAL === "admin" || P.auth.has(USER, "users.write"))) {
      document.getElementById("adminCard").style.display = "block";
      loadAdminMonitoring();
    }
    wireHandlers();
    window.addEventListener("online", () => showOffline(false));
    window.addEventListener("offline", () => showOffline(true));
    showOffline(!navigator.onLine);
  }

  function showOffline(isOffline) {
    const box = document.getElementById("offlineNote");
    box.innerHTML = isOffline
      ? '<div class="warn">لا يوجد اتصال بالإنترنت — بعض الميزات تتطلب اتصالاً بالخادم.</div>'
      : "";
  }

  function renderHeader() {
    const badge = document.getElementById("roleBadge");
    badge.textContent = (USER.full_name ? USER.full_name + " · " : "") +
      (USER.role_label_ar || USER.role);
    document.getElementById("userChip").textContent = "@" + USER.username;
  }

  function renderNav() {
    const nav = document.getElementById("nav");
    const portals = (PORTAL_DATA && PORTAL_DATA.portals) || [];
    nav.innerHTML = "";
    portals.forEach((entry) => {
      if (!entry.allowed) return;
      const button = document.createElement("button");
      button.textContent = entry.title_ar;
      if (entry.portal === PORTAL) button.classList.add("on");
      button.onclick = () => { location.href = "/ui/" + entry.portal + ".html"; };
      nav.appendChild(button);
    });
    const home = document.createElement("button");
    home.textContent = "الرئيسية";
    home.onclick = () => { location.href = "/ui/index.html"; };
    nav.appendChild(home);
    const workspace = document.createElement("button");
    workspace.textContent = "مساحة العمل";
    workspace.onclick = () => { location.href = "/ui/workspace.html"; };
    nav.appendChild(workspace);
  }

  function renderOverview() {
    const data = PORTAL_DATA || {};
    const stats = data.stats || {};
    document.getElementById("statsRow").innerHTML = `
      <div class="stat"><b>${stats.documents || 0}</b>قوانين في المدونة</div>
      <div class="stat"><b>${stats.articles || 0}</b>مادة قانونية</div>
      <div class="stat"><b>${stats.recent_cases || 0}</b>قضايا حديثة</div>
    `;
    const tools = (data.tools || [])
      .map((t) => `<div class="tool-tile"><div class="tt">${P.esc(t.title_ar)}</div></div>`)
      .join("");
    document.getElementById("overviewBody").innerHTML =
      `<p class="muted">${P.esc(data.portal_title_ar || "")} — الأدوات المتاحة لدورك:</p>` +
      `<div class="grid3">${tools || '<span class="muted">لا أدوات متاحة.</span>'}</div>`;
  }

  // ------------------------------------------------------------- workspace
  function allowedKinds() {
    return (CFG.workspaceKinds || []).filter((k) =>
      P.auth.has(USER, PERM_FOR_KIND[k])
    );
  }
  const PERM_FOR_KIND = {
    session: "sessions.manage", memo: "memos.review", contract: "contracts.draft",
    investigation: "prosecution.manage", charge: "prosecution.manage", note: "research.read",
  };

  async function loadItems() {
    const list = document.getElementById("itemsList");
    try {
      const data = await P.api.get("/workspace?portal=" + encodeURIComponent(PORTAL));
      ITEMS = data.items || [];
      if (!ITEMS.length) {
        list.innerHTML = '<span class="muted">لا عناصر بعد.</span>';
        return;
      }
      const rows = ITEMS.map((item) => `
        <tr>
          <td><b>${P.esc(item.title)}</b><br><span class="muted">${P.esc(item.kind_label_ar)}</span></td>
          <td><span class="pill p-${item.status}">${STATUS_LABELS[item.status] || item.status}</span></td>
          <td class="muted">${item.scheduled_at ? P.fmtDate(item.scheduled_at) : "—"}</td>
          <td>
            <button class="ghost sm" data-act="close" data-id="${item.id}">إغلاق</button>
            <button class="danger sm" data-act="del" data-id="${item.id}">حذف</button>
          </td>
        </tr>`).join("");
      list.innerHTML = `<table><thead><tr><th>العنوان</th><th>الحالة</th><th>الموعد</th><th>إجراء</th></tr></thead><tbody>${rows}</tbody></table>`;
      list.querySelectorAll("[data-act]").forEach((btn) => {
        btn.onclick = () => itemAction(btn.dataset.act, btn.dataset.id);
      });
    } catch (e) {
      list.innerHTML = `<div class="err">${P.esc(e.message)}</div>`;
    }
  }

  async function itemAction(action, id) {
    try {
      if (action === "del") {
        if (!confirm("تأكيد حذف العنصر؟")) return;
        await P.api.del("/workspace/" + id);
      } else if (action === "close") {
        await P.api.patch("/workspace/" + id, { status: "closed" });
      }
      await loadItems();
    } catch (e) { P.toast(e.message, "err"); }
  }

  function renderItemForm() {
    const kinds = allowedKinds();
    const form = document.getElementById("itemForm");
    if (!kinds.length) {
      form.innerHTML = '<span class="muted">لا تملك صلاحية إنشاء عناصر في هذه البوابة.</span>';
      return;
    }
    const options = kinds.map((k) => `<option value="${k}">${KIND_LABELS[k]}</option>`).join("");
    form.innerHTML = `
      <label>النوع</label><select id="itemKind">${options}</select>
      <label>العنوان</label><input id="itemTitle" placeholder="عنوان العنصر">
      <label>الموعد (للجلسات)</label><input id="itemDate" type="datetime-local">
      <label>التفاصيل (اختياري)</label><textarea id="itemBody"></textarea>
      <div class="actions"><button class="primary" id="itemCreate">إضافة</button></div>
      <div class="spinner" id="itemSpinner"></div>`;
    document.getElementById("itemCreate").onclick = createItem;
  }

  async function createItem() {
    const spinner = document.getElementById("itemSpinner");
    spinner.classList.add("on");
    const kind = document.getElementById("itemKind").value;
    const dateVal = document.getElementById("itemDate").value;
    const payload = {
      kind: kind,
      title: document.getElementById("itemTitle").value.trim(),
      body: document.getElementById("itemBody").value.trim() || null,
    };
    if (dateVal) payload.scheduled_at = new Date(dateVal).toISOString();
    try {
      await P.api.post("/workspace", payload);
      document.getElementById("itemTitle").value = "";
      document.getElementById("itemBody").value = "";
      await loadItems();
      P.toast("تمت الإضافة.");
    } catch (e) { P.toast(e.message, "err"); }
    finally { spinner.classList.remove("on"); }
  }

  // --------------------------------------------------------------- drafting
  function renderDraftOptions() {
    const select = document.getElementById("draftType");
    const types = (CFG.draftTypes || []).filter((t) =>
      P.auth.has(USER, PERM_FOR_DRAFT[t])
    );
    if (!types.length) {
      document.getElementById("draftCard").style.display = "none";
      return;
    }
    select.innerHTML = types.map((t) => `<option value="${t}">${DRAFT_LABELS[t] || t}</option>`).join("");
    document.getElementById("draftBtn").onclick = runDraft;
  }
  const PERM_FOR_DRAFT = {
    contract: "contracts.draft", defence: "defences.draft",
    charge: "prosecution.manage", memo: "memos.review", judgment_note: "judgments.draft",
  };

  async function runDraft() {
    const spinner = document.getElementById("draftSpinner");
    const out = document.getElementById("draftOut");
    const notes = document.getElementById("draftNotes");
    const brief = document.getElementById("draftBrief").value.trim();
    if (brief.length < 5) { P.toast("اكتب موجزاً أطول.", "err"); return; }
    spinner.classList.add("on");
    notes.innerHTML = "";
    try {
      const parties = {};
      const rawParties = document.getElementById("draftParties").value.trim();
      if (rawParties) parties["الأطراف"] = rawParties;
      const data = await P.api.post("/workspace/draft", {
        draft_type: document.getElementById("draftType").value,
        brief: brief,
        parties: parties,
        case_type: (CFG.caseTypes || [])[0] || null,
      });
      out.innerHTML = P.renderMarkdown(data.markdown);
      const cites = (data.cited_articles || []).map((c) => `
        <div class="cite"><b>${P.esc(c.article_label)}</b> — ${P.esc(c.law_title)}
        ${c.quote ? `<q>${P.esc(c.quote)}</q>` : ""}</div>`).join("");
      notes.innerHTML =
        (data.needs_review ? '<div class="warn">هذه المسودة تحتاج مراجعة بشرية.</div>' : "") +
        (data.notes || []).map((n) => `<div class="info">${P.esc(n)}</div>`).join("") +
        (cites ? `<h3>المواد المستشهد بها (${data.cited_articles.length})</h3>${cites}` : "");
    } catch (e) {
      out.innerHTML = `<div class="err">${P.esc(e.message)}</div>`;
    } finally { spinner.classList.remove("on"); }
  }

  // -------------------------------------------------------------- assistant
  async function loadConversations() {
    const list = document.getElementById("convList");
    try {
      const data = await P.api.get("/assistant/conversations");
      CONVERSATIONS = data.items || [];
      if (!CONVERSATIONS.length) {
        list.innerHTML = '<span class="muted">لا محادثات بعد.</span>';
        return;
      }
      list.innerHTML = CONVERSATIONS.map((c) => `
        <div class="conv-item ${c.id === CURRENT_CONV ? "on" : ""}" data-id="${c.id}">
          <div class="t">${P.esc(c.title)}</div>
          <div class="d">${P.fmtDate(c.updated_at)} · ${c.message_count} رسالة</div>
        </div>`).join("");
      list.querySelectorAll(".conv-item").forEach((node) => {
        node.onclick = () => openConversation(node.dataset.id);
      });
    } catch (e) {
      list.innerHTML = `<div class="err">${P.esc(e.message)}</div>`;
    }
  }

  async function openConversation(id) {
    try {
      const data = await P.api.get("/assistant/conversations/" + id);
      CURRENT_CONV = id;
      const log = document.getElementById("chatLog");
      log.innerHTML = "";
      (data.messages || []).forEach((m) => appendMessage(m.role, m.content, m.citations, m.needs_review));
      await loadConversations();
    } catch (e) { P.toast(e.message, "err"); }
  }

  function appendMessage(role, content, citations, needsReview) {
    const log = document.getElementById("chatLog");
    const node = document.createElement("div");
    node.className = "msg " + (role === "user" ? "user" : "bot");
    let html = P.esc(content);
    if (citations && citations.length) {
      html += '<div class="meta">المواد: ' + citations.map((c) =>
        P.esc(c.article_label || c.law_title)).join(" · ") + "</div>";
    }
    if (needsReview) html += '<div class="review">⚠ تحتاج مراجعة بشرية</div>';
    node.innerHTML = html;
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
  }

  async function sendQuestion(event) {
    event.preventDefault();
    const input = document.getElementById("chatInput");
    const question = input.value.trim();
    if (question.length < 2) return;
    appendMessage("user", question);
    input.value = "";
    const button = document.getElementById("chatSend");
    button.disabled = true;
    try {
      const data = await P.api.post("/assistant/ask", {
        question: question,
        conversation_id: CURRENT_CONV || undefined,
      });
      CURRENT_CONV = data.conversation_id;
      appendMessage("assistant", data.answer, data.cited_articles, data.needs_review);
      await loadConversations();
    } catch (e) {
      appendMessage("assistant", "تعذّر الحصول على إجابة: " + e.message);
    } finally { button.disabled = false; }
  }

  function newConversation() {
    CURRENT_CONV = null;
    document.getElementById("chatLog").innerHTML = "";
    document.getElementById("chatInput").focus();
  }

  // ---------------------------------------------------------- admin monitor
  async function loadAdminMonitoring() {
    const body = document.getElementById("adminBody");
    try {
      const overview = await P.api.get("/admin/overview");
      const audit = await P.api.get("/admin/audit?limit=15");
      const users = overview.users || {};
      const corpus = overview.corpus || {};
      const auditRows = (audit || []).map((a) => `
        <tr><td>${P.esc(a.entity)}</td><td>${P.esc(a.action)}</td>
        <td>${P.esc(a.actor || "—")}</td><td class="muted">${P.fmtDate(a.created_at)}</td></tr>`).join("");
      body.innerHTML = `
        <div class="grid3">
          <div class="stat"><b>${users.total || 0}</b>مستخدمون (${users.active || 0} نشط)</div>
          <div class="stat"><b>${overview.cases || 0}</b>قضايا</div>
          <div class="stat"><b>${corpus.articles || 0}</b>مادة قانونية</div>
          <div class="stat"><b>${overview.audit_events || 0}</b>حدث تدقيق</div>
        </div>
        <p class="muted">البيئة: ${P.esc(overview.app_env)} · قاعدة البيانات: ${overview.database ? "متصلة" : "محلية"} ·
          مزوّد النموذج: ${P.esc(overview.llm_provider)} · التضمين: ${P.esc(overview.embedding_provider)}</p>
        <h3>أحدث الأحداث</h3>
        <table><thead><tr><th>الكيان</th><th>الإجراء</th><th>المنفّذ</th><th>الوقت</th></tr></thead>
        <tbody>${auditRows || '<tr><td colspan="4" class="muted">لا أحداث.</td></tr>'}</tbody></table>`;
    } catch (e) {
      body.innerHTML = `<div class="muted">${P.esc(e.message)}</div>`;
    }
  }

  function wireHandlers() {
    const form = document.getElementById("chatForm");
    if (form) form.onsubmit = sendQuestion;
    const newBtn = document.getElementById("newConvBtn");
    if (newBtn) newBtn.onclick = newConversation;
  }

  boot();
})();
