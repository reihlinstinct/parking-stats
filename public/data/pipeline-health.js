"use strict";
/* Client-side pipeline-health display (#36). No token, no storage, no rebuild:
   the page reads the public hold marker and the public Actions API and marks
   itself stale / unknown / paused accordingly. Logic is dependency-injected so
   the node tests exercise the exact code the browser runs. */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.PipelineHealth = api;
    if (typeof document !== "undefined") api.mount(document, fetch.bind(root));
  }
})(typeof self !== "undefined" ? self : this, function () {
  var STALE_AFTER_MS = 48 * 60 * 60 * 1000;
  var TEXTS = {
    he: {
      paused: "הפרסום האוטומטי מושהה",
      stale: "העדכון האוטומטי מתעכב - מוצגים הנתונים האחרונים שפורסמו",
      unknown: "מצב העדכון האוטומטי אינו ידוע"
    },
    en: {
      paused: "Automatic publication is paused",
      stale: "Automatic updates are delayed - showing the last published data",
      unknown: "Automatic update status is unknown"
    }
  };

  function parseHold(body) {
    return (
      typeof body === "object" && body !== null && body.hold === true
    );
  }

  function lastSuccessFromRuns(body) {
    if (
      typeof body !== "object" || body === null ||
      !Array.isArray(body.workflow_runs)
    ) return null;
    var best = null;
    for (var i = 0; i < body.workflow_runs.length; i++) {
      var run = body.workflow_runs[i];
      if (
        typeof run !== "object" || run === null ||
        run.status !== "completed" || run.conclusion !== "success" ||
        typeof run.updated_at !== "string"
      ) continue;
      var stamp = Date.parse(run.updated_at);
      if (isNaN(stamp)) continue;
      if (best === null || stamp > best) best = stamp;
    }
    return best;
  }

  function decide(state) {
    if (state.hold === true) return "paused";
    if (state.apiOk !== true) return "unknown";
    if (typeof state.lastSuccessMs !== "number" || isNaN(state.lastSuccessMs)) {
      return "unknown";
    }
    var age = state.nowMs - state.lastSuccessMs;
    if (isNaN(age) || age < 0) return "unknown";
    return age > STALE_AFTER_MS ? "stale" : "fresh";
  }

  function texts(lang) {
    var code = typeof lang === "string" ? lang.slice(0, 2) : "he";
    return TEXTS[code] || TEXTS.he;
  }

  function mount(doc, fetchFn) {
    var el = doc.getElementById("pipeline-health");
    if (!el) return;
    var repo = el.getAttribute("data-repo");
    var workflow = el.getAttribute("data-workflow") || "publish.yml";
    if (!repo || !/^[-\w.]+\/[-\w.]+$/.test(repo)) return;
    var labels = texts(doc.documentElement.lang);
    var hold = false;
    var apiOk = false;
    var lastSuccessMs = null;
    function show(state) {
      if (state === "fresh") {
        el.hidden = true;
        return;
      }
      el.hidden = false;
      el.setAttribute("data-health", state);
      el.textContent = labels[state];
    }
    function finish() {
      show(decide({hold: hold, apiOk: apiOk, lastSuccessMs: lastSuccessMs, nowMs: Date.now()}));
    }
    fetchFn("data/hold.json", {cache: "no-store"}).then(function (res) {
      return res.ok ? res.json() : null;
    }).then(function (body) {
      hold = parseHold(body);
    }).catch(function () {}).then(function () {
      return fetchFn(
        "https://api.github.com/repos/" + repo +
          "/actions/workflows/" + encodeURIComponent(workflow) +
          "/runs?per_page=10",
        {cache: "no-store", headers: {Accept: "application/vnd.github+json"}}
      );
    }).then(function (res) {
      if (!res.ok) return null;
      apiOk = true;
      return res.json();
    }).then(function (body) {
      lastSuccessMs = apiOk ? lastSuccessFromRuns(body) : null;
    }).catch(function () {
      apiOk = false;
    }).then(finish);
  }

  return {
    STALE_AFTER_MS: STALE_AFTER_MS,
    parseHold: parseHold,
    lastSuccessFromRuns: lastSuccessFromRuns,
    decide: decide,
    texts: texts,
    mount: mount
  };
});
