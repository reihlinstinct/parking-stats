"use strict";
(function () {
  var svg = document.getElementById("map");
  if (!svg || !svg.getAttribute("data-home")) return;
  var controls = document.getElementById("map-controls");
  if (controls) controls.hidden = false;
  function nums(name) {
    return svg.getAttribute(name).split(",").map(Number);
  }
  var home = nums("data-home");
  var world = nums("data-world");
  var minSpan = Number(svg.getAttribute("data-min-span")) || 6;
  var aspect = home[3] / home[2];
  var maxSpan = Math.min(world[2], world[3] / aspect);
  var cx = home[0] + home[2] / 2;
  var cy = home[1] + home[3] / 2;
  var span = home[2];
  function apply() {
    span = Math.min(Math.max(span, minSpan), maxSpan);
    var w = span;
    var h = span * aspect;
    cx = Math.min(Math.max(cx, world[0] + w / 2), world[0] + world[2] - w / 2);
    cy = Math.min(Math.max(cy, world[1] + h / 2), world[1] + world[3] - h / 2);
    svg.setAttribute(
      "viewBox",
      (cx - w / 2) + "," + (cy - h / 2) + "," + w + "," + h
    );
  }
  function toSvg(clientX, clientY) {
    var pt = svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  }
  function zoom(factor, px, py) {
    var next = Math.min(Math.max(span * factor, minSpan), maxSpan);
    var k = next / span;
    cx = px + (cx - px) * k;
    cy = py + (cy - py) * k;
    span = next;
    apply();
  }
  svg.addEventListener(
    "wheel",
    function (e) {
      e.preventDefault();
      var p = toSvg(e.clientX, e.clientY);
      zoom(Math.pow(1.002, e.deltaY), p.x, p.y);
    },
    { passive: false }
  );
  var pointers = new Map();
  var lastDist = 0;
  svg.addEventListener("pointerdown", function (e) {
    svg.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      var pts = Array.from(pointers.values());
      lastDist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
    }
  });
  svg.addEventListener("pointermove", function (e) {
    if (!pointers.has(e.pointerId)) return;
    var prev = pointers.get(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 1) {
      var rect = svg.getBoundingClientRect();
      cx -= ((e.clientX - prev.x) * span) / rect.width;
      cy -= ((e.clientY - prev.y) * span * aspect) / rect.height;
      apply();
    } else if (pointers.size === 2) {
      var pts = Array.from(pointers.values());
      var d = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      if (lastDist > 0 && d > 0) {
        var p = toSvg((pts[0].x + pts[1].x) / 2, (pts[0].y + pts[1].y) / 2);
        zoom(lastDist / d, p.x, p.y);
      }
      lastDist = d;
    }
  });
  function drop(e) {
    pointers.delete(e.pointerId);
    lastDist = 0;
  }
  svg.addEventListener("pointerup", drop);
  svg.addEventListener("pointercancel", drop);
  if (controls) {
    controls.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      if (b.hasAttribute("data-reset")) {
        cx = home[0] + home[2] / 2;
        cy = home[1] + home[3] / 2;
        span = home[2];
        apply();
      } else if (b.getAttribute("data-zoom") === "in") {
        zoom(0.7, cx, cy);
      } else if (b.getAttribute("data-zoom") === "out") {
        zoom(1 / 0.7, cx, cy);
      }
    });
  }
  svg.style.touchAction = "none";
})();
