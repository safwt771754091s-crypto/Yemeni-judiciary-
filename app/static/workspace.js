/* ==========================================================================
   منصة الذكاء الاصطناعي القانوني — مساحة العمل (Harvey-style workspace)
   A chat-first legal workspace grounded in the Yemeni corpus. The assistant
   answers strictly from retrieved statutes; every answer shows its citations,
   its reasoning steps and a grounding score, and the whole thread is private
   to the signed-in user.
   ========================================================================== */
(function () {
  "use strict";

  const P = window.Portal;

  const CASE_TYPES = ["", "مدني", "تجاري", "جنائي", "أسري", "عمالي", "إداري", "دستوري"];
  const CASE_LABELS = {
    "": "تلقائي (عام)", "مدني": "مدني", "تجاري": "تجاري", "جنائي": "جنائي",
    "أسري": "أحوال شخصية", "عمالي": "عمالي", "إداري": "إداري", "دستوري": "دستوري",
  };

  const SAMPLES = [
    { t: "التعويض عن الفصل التعسفي", q: "ما هي حقوق العامل عند الفصل التعسفي في القانون اليمني؟", ct: "عمالي" },
    { t: "فسخ عقد البيع", q: "ما أثر عدم سداد الثمن على عقد بيع سيارة، وما حق البائع؟", ct: "مدني" },
    { t: "قيمة الإثبات بالكتابة", q: "ما حجية الكتابة في الإثبات وفق قانون الإثبات اليمني؟", ct: "مدني" },
    { t: "شيك بدون رصيد", q: "ما المسؤولية القانونية عن إصدار شيك بدون رصيد؟", ct: "تجاري" },
    { t: "إحداث عاهة مستديمة", q: "ما العقوبة المقررة لإحداث عاهة مستديمة؟", ct: "جنائي" },
    { t: "النفقة والحضانة", q: "كيف تُقدَّر النفقة وتُقرَّر الحضانة وفق قانون الأحوال الشخصية؟", ct: "أسري" },
  ];

  const CTX_ICONS = { user: "أنا", assistant: "⚖" };

  const STATE = {
    user: null,
    conversations: [],
    currentId: null,
    messages: [],
    lastAnswer: null,
    caseType: "",
    topK: 8,
    history: true,
    sending: false,
  };

  // ------------------------------------------------------------------ shell
  function shell() {
    return `
    <div class="ws-shell" id="wsShell">
      <aside class="ws-col ws-side" id="wsSide">
        <div class="ws-brand">
          <img src="/ui/icons/icon-192.png" alt="">
          <div><div class="bt">مساحة العمل القانونية</div><div class="bs">الجمهورية اليمنية</div></div>
        </div>
        <button class="ws-new" id="newBtn">＋ محادثة جديدة</button>
        <h3>المحادثات</h3>
        <div class="ws-convs" id="convList"></div>
        <h3>البوابات</h3>
        <nav>
          <a href="/ui/index.html">الصفحة التعريفية</a>
          <a href="/ui/assistant.html">المساعد (البوابة)</a>
          <a href="/ui/judge.html">بوابة القاضي</a>
          <a href="/ui/lawyer.html">بوابة المحامي</a>
          <a href="/ui/prosecution.html">بوابة النيابة</a>
          <a href="/ui/researcher.html">بوابة الباحث</a>
          <a href="/ui/admin.html">لوحة التحكم</a>
        </nav>
        <div style="margin-top:12px;display:flex;gap:8px">
          <button class="ghost sm hide" id="installBtn" style="flex:1">تثبيت التطبيق</button>
        </div>
      </aside>

      <main class="ws-col">
        <div class="ws-top">
          <button class="ghost sm only-mobile" id="sideToggle" style="display:none">☰</button>
          <span class="ttl" id="convTitle">محادثة جديدة</span>
          <span class="sp"></span>
          <span class="chip" id="userChip">—</span>
          <button class="ghost sm ctx-toggle" id="ctxToggle" style="display:none">السياق</button>
          <button class="ghost sm" id="logoutBtn">خروج</button>
        </div>
        <div class="ws-log" id="log"></div>
        <div class="ws-composer">
          <form id="composer">
            <div class="composer-box">
              <textarea id="input" rows="1" placeholder="اطرح سؤالاً قانونياً… (Enter للإرسال، Shift+Enter لسطر جديد)"></textarea>
              <button class="send" type="submit" id="sendBtn" title="إرسال">➤</button>
            </div>
            <div class="composer-opts">
              <label>نوع الدعوى</label>
              <select id="caseType">
                ${CASE_TYPES.map((c) => `<option value="${c}">${CASE_LABELS[c]}</option>`).join("")}
              </select>
              <label>عدد النصوص</label>
              <select id="topK">
                ${[4, 6, 8, 12, 16].map((n) => `<option value="${n}"${n === 8 ? " selected" : ""}>${n}</option>`).join("")}
              </select>
              <label><input type="checkbox" id="useHistory" checked style="width:auto"> استخدام سياق المحادثة</label>
            </div>
          </form>
        </div>
      </main>

      <aside class="ws-col ws-ctx" id="wsCtx">
        <div class="ws-brand" style="border:0;padding-bottom:4px;margin-bottom:8px">
          <div><div class="bt">السياق والأسانيد</div><div class="bs">مصادر آخر إجابة</div></div>
        </div>
        <div id="ctxBody"></div>
      </aside>
    </div>

    <div class="login-wrap" id="loginWrap" style="display:none">
      <div class="login-card">
        <div class="logo"><img src="/ui/icons/icon-192.png" alt=""><h1>مساحة العمل القانونية</h1></div>
        <p class="sub">سجّل الدخول للوصول إلى مساحة العمل والمساعد القانوني.</p>
        <label>اسم المستخدم</label><input id="loginUser" autocomplete="username">
        <label>كلمة المرور</label><input id="loginPass" type="password" autocomplete="current-password">
        <div class="err hide" id="loginErr"></div>
        <div class="actions" style="margin-top:14px"><button class="ws-new" id="loginBtn" style="width:100%">دخول</button></div>
        <p class="sub" style="margin-top:14px">ليس لديك حساب؟ <a href="/ui/signup.html">إنشاء حساب جديد</a></p>
        <p class="sub" style="margin-top:8px"><a href="/ui/index.html">← العودة إلى الصفحة التعريفية</a></p>
      </div>
    </div>`;
  }

  // ------------------------------------------------------------------- boot
  async function boot() {
    document.getElementById("root").innerHTML = shell();
    P.pwa.register();
    P.pwa.wireInstallButton("#installBtn");
    document.getElementById("logoutBtn").onclick = () => P.auth.logout();
    document.getElementById("loginBtn").onclick = doLogin;
    document.getElementById("loginPass").addEventListener("keydown", (e) => {
      if (e.key === "Enter") doLogin();
    });
    document.getElementById("newBtn").onclick = newConversation;
    document.getElementById("composer").addEventListener("submit", sendQuestion);
    document.getElementById("caseType").onchange = (e) => { STATE.caseType = e.target.value; };
    document.getElementById("topK").onchange = (e) => { STATE.topK = parseInt(e.target.value, 10); };
    document.getElementById("useHistory").onchange = (e) => { STATE.history = e.target.checked; };
    document.getElementById("ctxToggle").onclick = () =>
      document.getElementById("wsShell").classList.toggle("ctx-open");
    document.getElementById("sideToggle").onclick = () =>
      document.getElementById("wsShell").classList.toggle("side-open");
    const input = document.getElementById("input");
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuestion(e); }
    });
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 160) + "px";
    });

    if (!P.api.token()) {
      document.getElementById("wsShell").style.display = "none";
      document.getElementById("loginWrap").style.display = "flex";
      return;
    }
    STATE.user = await P.auth.requireSession();
    if (!STATE.user) return;
    document.getElementById("userChip").textContent =
      (STATE.user.full_name || STATE.user.username) + " · " + (STATE.user.role_label_ar || STATE.user.role);
    renderEmpty();
    renderContext();
    await loadConversations();
  }

  async function doLogin() {
    const err = document.getElementById("loginErr");
    err.classList.add("hide");
    try {
      await P.auth.login(
        document.getElementById("loginUser").value.trim(),
        document.getElementById("loginPass").value
      );
      location.reload();
    } catch (e) {
      err.textContent = e.message;
      err.classList.remove("hide");
    }
  }

  // ------------------------------------------------------------- conversations
  async function loadConversations() {
    const list = document.getElementById("convList");
    try {
      const data = await P.api.get("/assistant/conversations?limit=50");
      STATE.conversations = data.items || data.conversations || data || [];
    } catch (e) {
      STATE.conversations = [];
    }
    if (!STATE.conversations.length) {
      list.innerHTML = '<div class="muted" style="padding:8px 11px;font-size:12.5px">لا محادثات بعد.</div>';
      return;
    }
    list.innerHTML = STATE.conversations.map((c) => `
      <div class="conv ${c.id === STATE.currentId ? "on" : ""}" data-id="${c.id}">
        <span class="t">${P.esc(c.title || "محادثة")}</span>
        <span class="x" data-del="${c.id}" title="حذف">✕</span>
      </div>`).join("");
    list.querySelectorAll(".conv").forEach((el) => {
      el.onclick = (ev) => {
        if (ev.target.dataset.del) { deleteConversation(ev.target.dataset.del); return; }
        openConversation(el.dataset.id);
      };
    });
  }

  async function deleteConversation(id) {
    try {
      await P.api.del("/assistant/conversations/" + id);
      if (STATE.currentId === id) newConversation();
      await loadConversations();
    } catch (e) { P.toast(e.message, "err"); }
  }

  async function openConversation(id) {
    try {
      const data = await P.api.get("/assistant/conversations/" + id);
      STATE.currentId = id;
      STATE.messages = data.messages || [];
      const conv = data.conversation || {};
      document.getElementById("convTitle").textContent = conv.title || "محادثة";
      const log = document.getElementById("log");
      log.innerHTML = "";
      STATE.lastAnswer = null;
      STATE.messages.forEach((m) => appendMessage(m.role, m.content, m.citations, m.needs_review, m));
      const lastAssistant = [...STATE.messages].reverse().find((m) => m.role === "assistant");
      if (lastAssistant) {
        STATE.lastAnswer = {
          answer: lastAssistant.content,
          cited_articles: lastAssistant.citations || [],
          reasoning: lastAssistant.reasoning || [],
          grounding_score: lastAssistant.grounding_score,
          needs_review: lastAssistant.needs_review,
        };
      }
      renderContext();
      document.getElementById("wsShell").classList.remove("side-open");
      await loadConversations();
    } catch (e) { P.toast(e.message, "err"); }
  }

  function newConversation() {
    STATE.currentId = null;
    STATE.messages = [];
    STATE.lastAnswer = null;
    document.getElementById("convTitle").textContent = "محادثة جديدة";
    renderEmpty();
    renderContext();
    document.getElementById("input").focus();
  }

  // ------------------------------------------------------------------ render
  function renderEmpty() {
    const log = document.getElementById("log");
    log.innerHTML = `
      <div class="ws-empty">
        <div class="hero">مساحة العمل القانونية</div>
        <p>اطرح سؤالاً قانونياً، وستُبنى الإجابة حصراً على النصوص اليمنية المسترجَعة من المدونة، مع إظهار المواد والسند في اللوحة الجانبية.</p>
        <div class="samples">
          ${SAMPLES.map((s, i) => `
            <div class="sample" data-i="${i}"><b>${P.esc(s.t)}</b><span>${P.esc(s.q)}</span></div>`).join("")}
        </div>
      </div>`;
    log.querySelectorAll(".sample").forEach((el) => {
      el.onclick = () => {
        const s = SAMPLES[parseInt(el.dataset.i, 10)];
        document.getElementById("caseType").value = s.ct;
        STATE.caseType = s.ct;
        document.getElementById("input").value = s.q;
        sendQuestion();
      };
    });
  }

  function appendMessage(role, content, citations, needsReview, meta) {
    const log = document.getElementById("log");
    if (log.querySelector(".ws-empty")) log.innerHTML = "";
    const node = document.createElement("div");
    node.className = "msg " + (role === "user" ? "user" : "bot");
    const isBot = role !== "user";
    node.innerHTML = `
      <div class="av">${isBot ? CTX_ICONS.assistant : CTX_ICONS.user}</div>
      <div class="body">
        <div class="who">${isBot ? "المساعد القانوني" : "أنت"}${meta && meta.created_at ? " · " + P.fmtDate(meta.created_at) : ""}</div>
        <div class="bubble">${isBot ? P.renderMarkdown(content) : P.esc(content)}</div>
        <div class="cites"></div>
        <div class="badge-row"></div>
        <div class="msg-actions"></div>
      </div>`;
    const citesBox = node.querySelector(".cites");
    (citations || []).forEach((c) => {
      const chip = document.createElement("div");
      chip.className = "cite-chip";
      chip.innerHTML = `<b>${P.esc(c.article_label || c.article || "")}</b> ${P.esc(c.law_title || "")}`;
      chip.title = c.quote || "";
      chip.onclick = () => showCitation(c);
      citesBox.appendChild(chip);
    });
    const badges = node.querySelector(".badge-row");
    if (isBot) {
      if (needsReview) badges.innerHTML += '<span class="tag review">⚠ تحتاج مراجعة بشرية</span>';
      else badges.innerHTML += '<span class="tag grounded">✓ مسندة إلى النصوص</span>';
    }
    const actions = node.querySelector(".msg-actions");
    if (isBot) {
      const copy = document.createElement("button");
      copy.textContent = "نسخ";
      copy.onclick = () => {
        navigator.clipboard.writeText(content).then(
          () => P.toast("تم نسخ الإجابة"),
          () => P.toast("تعذّر النسخ", "err")
        );
      };
      actions.appendChild(copy);
    }
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
  }

  function appendThinking() {
    const log = document.getElementById("log");
    const node = document.createElement("div");
    node.className = "msg bot";
    node.id = "thinking";
    node.innerHTML = `
      <div class="av">${CTX_ICONS.assistant}</div>
      <div class="body"><div class="bubble"><div class="thinking">جارٍ الاسترجاع والاستدلال
        <span class="dots"><span></span><span></span><span></span></span></div></div></div>`;
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
  }

  function removeThinking() {
    const node = document.getElementById("thinking");
    if (node) node.remove();
  }

  // ------------------------------------------------------------------- send
  async function sendQuestion(event) {
    if (event && event.preventDefault) event.preventDefault();
    if (STATE.sending) return;
    const input = document.getElementById("input");
    const question = input.value.trim();
    if (question.length < 2) return;
    STATE.sending = true;
    document.getElementById("sendBtn").disabled = true;
    appendMessage("user", question);
    input.value = "";
    input.style.height = "auto";
    appendThinking();
    try {
      const data = await P.api.post("/assistant/ask", {
        question: question,
        conversation_id: STATE.currentId || undefined,
        case_type: STATE.caseType || undefined,
        top_k: STATE.topK,
        include_history: STATE.history,
      });
      removeThinking();
      STATE.currentId = data.conversation_id;
      STATE.lastAnswer = data;
      appendMessage("assistant", data.answer, data.cited_articles, data.needs_review, data);
      document.getElementById("convTitle").textContent =
        STATE.conversations.find((c) => c.id === STATE.currentId)?.title || question.slice(0, 40);
      renderContext();
      await loadConversations();
    } catch (e) {
      removeThinking();
      appendMessage("assistant", "تعذّر الحصول على إجابة: " + e.message, [], true);
    } finally {
      STATE.sending = false;
      document.getElementById("sendBtn").disabled = false;
    }
  }

  // -------------------------------------------------------------- context
  function renderContext() {
    const box = document.getElementById("ctxBody");
    const a = STATE.lastAnswer;
    let html = `
      <div class="ctx-block">
        <h4>إعدادات الاسترجاع</h4>
        <div class="kv"><span>نوع الدعوى</span><b>${P.esc(CASE_LABELS[STATE.caseType] || STATE.caseType || "تلقائي")}</b></div>
        <div class="kv"><span>عدد النصوص</span><b>${STATE.topK}</b></div>
        <div class="kv"><span>سياق المحادثة</span><b>${STATE.history ? "مُفعّل" : "معطّل"}</b></div>
        <div class="kv"><span>المصادر المسترجَعة</span><b>${a ? (a.cited_articles || []).length : "—"}</b></div>
      </div>`;
    if (a && a.grounding_score != null) {
      const pct = Math.round((a.grounding_score || 0) * 100);
      html += `
        <div class="ctx-block">
          <h4>درجة الإسناد</h4>
          <div class="kv"><span>التقييم</span><b>${pct}%</b></div>
          <div class="gauge"><i style="width:${Math.max(2, pct)}%"></i></div>
        </div>`;
    }
    if (a && (a.reasoning || []).length) {
      html += `<div class="ctx-block"><h4>خطوات الاستدلال</h4>${
        a.reasoning.map((r, i) => `<div class="reason-step">${i + 1}. ${P.esc(r)}</div>`).join("")
      }</div>`;
    }
    const cites = (a && a.cited_articles) || [];
    if (cites.length) {
      html += `<div class="ctx-block"><h4>المواد المستند إليها (${cites.length})</h4>${
        cites.map((c) => citationCard(c)).join("")
      }</div>`;
    } else {
      html += `<div class="ctx-block"><h4>الأسانيد</h4><div class="muted" style="font-size:12.5px">
        ${a ? "لم تُعثر الإجابة على مواد مسندة في المدونة." : "ستظهر هنا المواد المسترجَعة بعد أول سؤال."}</div></div>`;
    }
    box.innerHTML = html;
    box.querySelectorAll(".cite-card").forEach((el, i) => {
      el.onclick = () => showCitation(cites[i]);
    });
  }

  function citationCard(c) {
    const q = c.quote ? `<span class="q">«${P.esc(c.quote)}»</span>` : "";
    const src = [c.law_title, c.law_number ? "رقم " + c.law_number : "", c.source_type ? c.source_type : ""]
      .filter(Boolean).join(" · ");
    return `<div class="cite-card"><b>${P.esc(c.article_label || c.article || "")}</b>${q}
      <span class="src">${P.esc(src)}${c.source_url ? ' · <a href="' + P.esc(c.source_url) + '" target="_blank" rel="noopener">المصدر</a>' : ""}</span></div>`;
  }

  function showCitation(c) {
    const box = document.getElementById("ctxBody");
    const detail = document.createElement("div");
    detail.className = "ctx-block";
    detail.innerHTML = `<h4>نص المادة</h4><div style="font-size:13px">${P.esc(c.quote || "لا يتوفر نص كامل.")}</div>
      <div class="kv" style="margin-top:8px"><span>${P.esc(c.law_title || "")}</span><b>${P.esc(c.article_label || "")}</b></div>`;
    box.prepend(detail);
    document.getElementById("wsShell").classList.add("ctx-open");
  }

  boot();
})();
