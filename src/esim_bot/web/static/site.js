// NomadData вэб дэлгүүр: хөдөлгөөн, хайлт, хүснэгт эрэмбэлэх
(function () {
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var finePointer = window.matchMedia("(pointer: fine)").matches;

  // Гүйлгэхэд толгой хэсэгт сүүдэр нэмнэ
  var header = document.querySelector(".site-header");
  if (header) {
    var onScroll = function () { header.classList.toggle("scrolled", window.scrollY > 8); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // Хэсгүүд дэлгэцэнд орохдоо гарч ирнэ
  var reveals = document.querySelectorAll(".reveal");
  if (reduceMotion || !("IntersectionObserver" in window)) {
    reveals.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    reveals.forEach(function (el) { io.observe(el); });
  }

  // Карт заагчийг дагаж бага зэрэг хазайна (3D)
  if (finePointer && !reduceMotion) {
    document.querySelectorAll("a.tile").forEach(function (card) {
      card.addEventListener("pointermove", function (e) {
        var r = card.getBoundingClientRect();
        var x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
        card.style.setProperty("--ry", ((x - 0.5) * 8).toFixed(2) + "deg");
        card.style.setProperty("--rx", ((0.5 - y) * 8).toFixed(2) + "deg");
        card.style.setProperty("--mx", (x * 100).toFixed(1) + "%");
        card.style.setProperty("--my", (y * 100).toFixed(1) + "%");
      });
      card.addEventListener("pointerleave", function () {
        card.style.removeProperty("--rx");
        card.style.removeProperty("--ry");
      });
    });
  }

  // Улсын нэрээр (латин нэр, кодоор ч) хайж, карт, багц, хүснэгтийн мөрийг шүүнэ
  var form = document.querySelector("[data-search]");
  if (form) {
    var input = form.querySelector("input");
    var items = document.querySelectorAll("[data-q]");
    var empty = document.querySelector("[data-empty]");
    var filter = function () {
      var q = input.value.trim().toLowerCase(), any = false;
      items.forEach(function (el) {
        var hit = !q || el.getAttribute("data-q").indexOf(q) !== -1;
        el.hidden = !hit;
        if (hit) any = true;
      });
      if (empty) empty.hidden = any;
    };
    input.addEventListener("input", filter);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      filter();
      var first = document.querySelector("section.country[data-q]:not([hidden])");
      (input.value.trim() && first ? first : document.getElementById("esim")).scrollIntoView();
    });
  }

  // Хүснэгтийг баганын толгой дээр дарж эрэмбэлнэ
  document.querySelectorAll("table[data-sortable]").forEach(function (table) {
    var tbody = table.tBodies[0];
    table.querySelectorAll("button[data-sort]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var th = btn.closest("th"), key = btn.getAttribute("data-sort");
        var asc = th.getAttribute("aria-sort") !== "ascending";
        table.querySelectorAll("th[aria-sort]").forEach(function (h) { h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        var rows = Array.prototype.slice.call(tbody.rows);
        rows.sort(function (a, b) { return (Number(a.dataset[key]) - Number(b.dataset[key])) * (asc ? 1 : -1); });
        rows.forEach(function (row) {
          tbody.appendChild(row);
          row.classList.remove("moved");
          void row.offsetWidth;  // хөдөлгөөнийг дахин эхлүүлнэ
          row.classList.add("moved");
        });
      });
    });
  });
})();
