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

  // Жагсаалтын шүүлтүүр: сонголт өөрчлөгдмөгц хуудсыг шинэчилнэ; утсан дээр анхнаасаа хураалттай
  var filters = document.querySelector("[data-filters]");
  if (filters) {
    filters.addEventListener("change", function (e) {
      if (e.target.name !== "q") filters.submit();
    });
    var box = filters.querySelector("[data-collapse]");
    if (box && window.matchMedia("(max-width: 959px)").matches) box.removeAttribute("open");
  }

  // Танилцуулга хуудас: тоо ширхэг сонгоход нийт үнийг шинэчилнэ
  var buy = document.querySelector("[data-buy]");
  if (buy) {
    var qty = buy.querySelector("input[name=quantity]"), total = buy.querySelector("[data-total]");
    var price = Number(buy.dataset.price), max = Number(qty.max);
    var update = function (value) {
      var n = Math.min(Math.max(parseInt(value, 10) || 1, 1), max);
      qty.value = n;
      total.textContent = (price * n).toLocaleString("en-US") + "₮";
    };
    buy.querySelectorAll("[data-step]").forEach(function (b) {
      b.addEventListener("click", function () { update(Number(qty.value) + Number(b.dataset.step)); });
    });
    qty.addEventListener("change", function () { update(qty.value); });

    // Утсан дээрх доод мөрийг худалдан авах карт харагдах үед нууна
    var bar = document.querySelector("[data-mbar]");
    if (bar && "IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        bar.classList.toggle("away", entries[0].isIntersecting);
      }).observe(buy);
    }
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
