/* ==========================================================================
   البحث في المواد القانونية — يعمل بدون إنترنت وبوجود الإنترنت
   Statute search for the public landing page.

   Offline: the whole corpus is shipped as /ui/legal-index.json, which the
   service worker caches, and is searched in the browser. Online: the same
   query also goes to /api/v1/retrieval/articles so a database-backed corpus
   that is larger than the shipped index is included too. Results from both
   are merged and de-duplicated, and the badge on each hit shows where it
   came from.

   The Arabic normalizer mirrors app/core/text_utils.normalize_arabic so a
   query matches the same way on the client and the server.
   ========================================================================== */
(function (global) {
  "use strict";

  var DIACRITICS = /[\u064B-\u0652\u0670\u0640]/g;
  var ALEF = /[\u0623\u0625\u0622\u0671]/g;
  var YA = /[\u0649\u0626]/g;
  var HAMZA = /[\u0624\u0621]/g;
  var NON_WORD = /[^\u0621-\u064Aa-zA-Z0-9\u0660-\u0669\s]+/g;
  var WS = /\s+/g;
  var AR_DIGITS = "٠١٢٣٤٥٦٧٨٩";

  function normalize(text) {
    if (!text) return "";
    var out = String(text).normalize("NFKC");
    out = out.replace(DIACRITICS, "");
    out = out.replace(/[\u0660-\u0669\u06F0-\u06F9]/g, function (c) {
      var i = AR_DIGITS.indexOf(c);
      if (i >= 0) return String(i);
      return String(c.charCodeAt(0) - 0x06F0);
    });
    out = out.replace(ALEF, "ا").replace(YA, "ي").replace(HAMZA, "ي");
    out = out.replace(/\u0629/g, "\u0647"); // ta marbuta -> ha
    out = out.replace(NON_WORD, " ");
    return out.replace(WS, " ").trim();
  }

  function digitsOnly(value) {
    return normalize(value).replace(/\D/g, "").replace(/^0+/, "");
  }

  // Build a regex source that matches a term even when the text uses a letter
  // variant (أ/إ/آ for ا) or carries diacritics, so highlighting works on the
  // raw article text while matching what the search normalized.
  var VARIANTS = { "ا": "[اأإآٱ]", "ي": "[يىئ]", "ه": "[هة]", "و": "[وؤ]", "ء": "[ءؤئ]" };
  function tolerantPattern(term) {
    var out = "";
    for (var i = 0; i < term.length; i++) {
      var ch = term[i];
      var cls = VARIANTS[ch] || ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      out += cls + "[\\u064B-\\u0652\\u0670\\u0640]*";
    }
    return out;
  }

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // -- local (offline) index ------------------------------------------------
  var index = null;          // {articles: [...]} with a cached _norm/_terms
  var loading = null;

  function loadIndex() {
    if (index) return Promise.resolve(index);
    if (loading) return loading;
    loading = fetch("/ui/legal-index.json", { cache: "force-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("index " + r.status);
        return r.json();
      })
      .then(function (data) {
        var articles = data.articles || [];
        for (var i = 0; i < articles.length; i++) {
          var a = articles[i];
          a._norm = normalize(a.c);
          a._terms = a._norm.split(" ");
          a._num = digitsOnly(a.n);
        }
        index = { version: data.version, count: articles.length, articles: articles };
        return index;
      })
      .catch(function () {
        index = { version: 0, count: 0, articles: [] };
        return index;
      });
    return loading;
  }

  function lexicalScore(terms, article) {
    if (!terms.length) return 0;
    var set = {};
    for (var i = 0; i < article._terms.length; i++) set[article._terms[i]] = 1;
    var overlap = 0;
    for (var t = 0; t < terms.length; t++) if (set[terms[t]]) overlap++;
    if (!overlap) return 0;
    return overlap / Math.sqrt(terms.length * Math.max(article._terms.length, 1));
  }

  function searchLocal(number, query, limit) {
    var idx = index || { articles: [] };
    var target = digitsOnly(number);
    var terms = normalize(query).split(" ").filter(function (t) { return t.length > 1; });
    if (!target && !terms.length) return [];

    var hits = [];
    for (var i = 0; i < idx.articles.length; i++) {
      var a = idx.articles[i];
      var numMatch = target && a._num === target;
      if (target && !numMatch) continue;
      var score = terms.length ? lexicalScore(terms, a) : 0;
      if (terms.length && !numMatch && score <= 0) continue;
      hits.push({ a: a, num: numMatch ? 1 : 0, score: score });
    }
    hits.sort(function (x, y) {
      if (x.num !== y.num) return y.num - x.num;
      if (x.score !== y.score) return y.score - x.score;
      return String(x.a.i) < String(y.a.i) ? -1 : 1;
    });
    return hits.slice(0, limit).map(function (h) { return toResult(h.a, h.num, h.score); });
  }

  function toResult(a, numMatch, score) {
    return {
      id: a.i,
      article_number: a.n || null,
      article_label: a.l || "مادة",
      law_title: a.t || "",
      source_type: a.s || "other",
      source_url: a.u || null,
      content: a.c || "",
      relevance: numMatch ? 1 : Math.min(1, score * 4),
      origin: "offline",
    };
  }

  // -- online (API) search --------------------------------------------------
  function searchOnline(number, query, limit) {
    var params = [];
    if (number) params.push("number=" + encodeURIComponent(number));
    if (query) params.push("q=" + encodeURIComponent(query));
    params.push("limit=" + limit);
    return fetch("/api/v1/retrieval/articles?" + params.join("&"), {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("api " + r.status);
        return r.json();
      })
      .then(function (data) {
        return (data.results || []).map(function (r) {
          return {
            id: r.article_id,
            article_number: r.article_number || null,
            article_label: r.article_label || "مادة",
            law_title: r.law_title || "",
            source_type: r.source_type || "other",
            source_url: r.source_url || null,
            content: r.content || "",
            relevance: r.similarity == null ? 1 : r.similarity,
            origin: "online",
          };
        });
      });
  }

  // -- merge ----------------------------------------------------------------
  function keyOf(r) {
    return (r.law_title || "") + "|" + (digitsOnly(r.article_number) || r.id);
  }

  function merge(localHits, remoteHits) {
    var seen = {};
    var out = [];
    function add(list) {
      for (var i = 0; i < list.length; i++) {
        var k = keyOf(list[i]);
        if (seen[k]) continue;
        seen[k] = 1;
        out.push(list[i]);
      }
    }
    add(localHits);   // local results lead: they are instant and offline-safe
    add(remoteHits);
    return out;
  }

  /**
   * Run a search. Resolves with {results, offline, localCount, onlineCount}.
   * Local results are returned immediately when offline; when online the API
   * results are merged in before resolving.
   */
  function search(number, query, limit) {
    limit = limit || 30;
    return loadIndex().then(function () {
      var local = searchLocal(number, query, limit);
      var online = navigator.onLine !== false;
      if (!online) {
        return { results: local, offline: true, localCount: local.length, onlineCount: 0 };
      }
      return searchOnline(number, query, limit)
        .then(function (remote) {
          var merged = merge(local, remote);
          return {
            results: merged.slice(0, limit),
            offline: false,
            localCount: local.length,
            onlineCount: remote.length,
          };
        })
        .catch(function () {
          // The API is unreachable even though the browser reports online:
          // fall back to the local index so the search still answers.
          return { results: local, offline: true, localCount: local.length, onlineCount: 0 };
        });
    });
  }

  global.StatuteSearch = {
    normalize: normalize,
    digitsOnly: digitsOnly,
    escapeHtml: escapeHtml,
    tolerantPattern: tolerantPattern,
    loadIndex: loadIndex,
    search: search,
  };
})(window);
