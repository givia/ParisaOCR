// ParisaOCR review panel: check a converted book page by page and fix it. Talks to its local server (review.py).
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const LATIN = /[A-Za-z]/g;
const latinShare = s => ((s || "").match(LATIN) || []).length / Math.max(1, (s || "").replace(/\s/g, "").length);

const ROLES = ["body", "heading", "byline", "epigraph", "quote", "verse", "note", "endnote", "reference", "caption",
               "table", "figure", "contents", "header", "pagenum", "noise", "other"];
const KEYS = {body: "b", heading: "h", byline: "y", epigraph: "i", quote: "q", verse: "v", note: "f", endnote: "e",
              reference: "r", caption: "c", table: "t", figure: "g", contents: "o", header: "k", pagenum: "j", noise: "x", other: "."};
const ROLE_OF_KEY = Object.fromEntries(Object.entries(KEYS).map(([r, k]) => [k, r]));
const PAGE_TYPES = ["text", "opening", "part", "contents", "cover", "title", "imprint", "dedication", "epigraph", "endnotes",
                    "bibliography", "index", "figure", "table", "ad", "blank", "other"];
const UNIT_KINDS = ["part", "chapter", "section", "figure"];
const META = ["title", "subtitle", "author", "translator", "editor", "publisher", "isbn", "language"];
const PARA_ROLES = ["body", "quote", "reference", "verse", "other"];
const MARKER_ROLES = ["body", "quote", "verse", "heading", "caption", "reference", "byline", "epigraph", "other"];

const T = {
  fa: {
    lang: "English", undo: "واگرد", rebuild: "بازسازی EPUB", done: "پایان", tab_issues: "موارد", tab_pages: "صفحه‌ها", tab_book: "کتاب",
    counts: "{rev} از {pages} صفحه بازبینی شده · {iss} مورد باز", dirty: "تغییرها هنوز در EPUB نیامده؛ «بازسازی EPUB» را بزنید.",
    built: "EPUB ساخته شد ({s} ثانیه)", building: "در حال ساختن EPUB…", saved: "ذخیره شد", undone: "واگردانده شد", nothing_undo: "چیزی برای واگرد نیست",
    closed: "صفحهٔ بازبینی بسته شد. علامت‌ها و اصلاح‌ها ذخیره شده‌اند و در تبدیل‌های بعدی هم به کار می‌روند.",
    r_body: "متن", r_heading: "عنوان", r_byline: "نام نویسنده", r_epigraph: "سرلوحه", r_quote: "نقل‌قول", r_verse: "شعر",
    r_note: "پانویس", r_endnote: "یادداشت پایانی", r_reference: "منبع", r_caption: "زیرنویس", r_table: "جدول", r_figure: "تصویر",
    r_contents: "فهرست", r_header: "سرصفحه", r_pagenum: "شمارهٔ صفحه", r_noise: "زائد", r_other: "دیگر",
    k_role: "نقش نامطمئن", k_text: "متن نامطمئن", k_note: "پانویس بی‌نشانه", k_marker: "نشانهٔ بی‌پانویس", k_numbering: "شمارهٔ جاافتاده",
    k_contents: "مدخل فهرست پیدا نشد", k_chapter: "فصل خارج از فهرست", k_page: "صفحهٔ کم‌کیفیت", k_left: "صفحهٔ بیرون از کتاب",
    left_hint: "این صفحه در کتاب نیست: مبدل آن را نسخهٔ دوم صفحهٔ {n} (PDF {kept}) دانست. اگر چنین نیست، شمارهٔ چاپی‌اش را درست کنید یا آن را در کتاب نگه دارید.",
    left_hint0: "این صفحه در کتاب نیست. اگر باید باشد، شمارهٔ چاپی‌اش را درست کنید یا آن را در کتاب نگه دارید.",
    keep: "این صفحه در کتاب بماند", kept: "در کتاب می‌ماند (بی‌شماره، پس از صفحهٔ پیشینش در PDF)", unkeep: "برگرداندن", all: "همه", show_ignored: "نادیده‌گرفته‌ها",
    no_issues: "مورد بازی نمانده.", ignore: "نادیده بگیر", unignore: "برگردان",
    sort_doubt: "نامطمئن‌ترین اول", sort_order: "به ترتیب صفحه", pdf_page: "PDF {pdf}", book_page: "صفحهٔ {n}",
    pt_text: "متن", pt_opening: "آغاز فصل", pt_part: "آغاز بخش", pt_contents: "فهرست", pt_cover: "جلد", pt_title: "صفحهٔ عنوان", pt_imprint: "شناسنامه",
    pt_dedication: "تقدیم", pt_epigraph: "سرلوحه", pt_endnotes: "یادداشت‌های پایانی", pt_bibliography: "کتاب‌نامه", pt_index: "نمایه",
    pt_figure: "تصویر", pt_table: "جدول", pt_ad: "آگهی", pt_blank: "سفید", pt_other: "دیگر",
    page_type: "نوع صفحه", printed: "شمارهٔ چاپی", tool_select: "انتخاب", tool_cut: "پانویس‌ها از این‌جا", accept: "صفحه درست است",
    revert: "بازگرداندن صفحه", reviewed: "بازبینی‌شده", prev: "قبلی", next: "بعدی", preview: "این صفحه در EPUB (پس از بازسازی)",
    cut_hint: "روی تصویر جایی را بزنید که پانویس‌ها از آن‌جا شروع می‌شوند.", lines: "سطرهای صفحه", select_line: "سطری را انتخاب کنید (روی تصویر یا در فهرست سطرها).",
    n_sel: "{n} سطر انتخاب شده", role: "نقش", level: "سطح عنوان", lv1: "۱ فصل/بخش", lv2: "۲ زیربخش", lv3: "۳", note_no: "شمارهٔ پانویس",
    note_hint: "۰ یعنی ادامهٔ پانویس قبلی", para: "آغاز بند (پاراگراف)", text: "متن سطر", text_hint: "اصلاح متن؛ خالی یعنی همان متن اوسی‌آر",
    reset: "برگرداندن", words: "واژه‌ها (برای گذاشتن نشانهٔ پانویس روی واژه بزنید)", markers: "نشانه‌های پانویس", no_markers: "نشانه‌ای ندارد",
    marker_no: "شمارهٔ پانویسی که این نشانه به آن می‌رود:", remove: "حذف", add_heading: "افزودن عنوانی که اوسی‌آر نخوانده (بالای این سطر)",
    heading_text: "متن عنوان:", conf: "اطمینان", c_role: "نقش", c_text: "متن", model_role: "مدل: {r} ({p})", p_note: "پانویس", p_head: "عنوان",
    p_start: "آغاز پانویس", weakest: "ضعیف‌ترین واژه", sure: "مطمئن", check: "بررسی", unsure: "نامطمئن", by_hand: "دستی",
    notes: "پانویس‌های این صفحه", how_marker: "پیوند در نشانه", "how_end of page": "نشانه پیدا نشد؛ آخر صفحه", how_endnote: "یادداشت پایانی",
    no_notes: "پانویسی ندارد.", place_hint: "برای پیوند: سطر متن را انتخاب کنید و روی واژه‌ای بزنید که نشانه بعد از آن است.",
    bulk: "همین نقش برای همهٔ سطرهای هم‌متن در کتاب", bulk_done: "{n} صفحه تغییر کرد", missing: "سطرهای افزوده", toc: "مدخل‌های فهرست",
    add_line: "افزودن سطری که اوسی‌آر نخوانده (زیر این سطر)", line_text: "متن سطر:", split: "شکستن سطر در جای مکان‌نما",
    split_hint: "مکان‌نما را در متن سطر جایی بگذارید که سطر دوم از آن‌جا آغاز می‌شود، سپس «شکستن سطر» را بزنید.",
    verse_hint: "در بیت دومصراعی، دو مصراع را با / از هم جدا کنید.", after_line: "زیر «{t}»", before_line: "بالای «{t}»",
    toc_title: "عنوان", toc_page: "صفحه", toc_level: "سطح", add_row: "افزودن مدخل", units: "فصل‌ها و بخش‌ها", unit_add: "این صفحه آغاز:",
    u_part: "بخش", u_chapter: "فصل", u_section: "زیربخش", u_figure: "تصویر", u_none: "—", save_units: "ذخیرهٔ فصل‌ها", units_hint: "فهرست بالا تعیین‌کنندهٔ فصل‌هاست؛ پس از ذخیره «بازسازی» را بزنید.",
    sections: "زیربخش‌ها (از نقش «عنوان» سطرها)", meta: "مشخصات کتاب", m_title: "عنوان", m_subtitle: "زیرعنوان", m_author: "نویسنده",
    m_translator: "مترجم", m_editor: "ویراستار", m_publisher: "ناشر", m_isbn: "شابک", m_language: "زبان", save: "ذخیره",
    cover: "جلد", cover_page: "جلد: PDF {pdf}", no_cover: "جلدی انتخاب نشده", use_cover: "این صفحه جلد باشد", clear: "پاک کردن",
    keys: "کلیدها", keys_help: "<kbd>↑</kbd> <kbd>↓</kbd> سطر · <kbd>PgUp</kbd> <kbd>PgDn</kbd> صفحه · <kbd>n</kbd> مورد بعد · نقش‌ها با حرف کنار هر دکمه · <kbd>1</kbd>–<kbd>3</kbd> سطح · <kbd>p</kbd> آغاز بند · <kbd>Enter</kbd> ویرایش متن · <kbd>a</kbd> صفحه درست است · <kbd>Ctrl+Z</kbd> واگرد",
    error: "خطا", contents_hint: "مدخل‌های این صفحهٔ فهرست؛ اگر خالی بماند، قاعده‌ها آن را می‌خوانند.",
    rotate: "چرخش", crop: "برش تصویر", crop_hint: "دور بخشی از صفحه را که تصویر است بکشید.", crop_reset: "کل صفحه",
    pictures: "تصویرهای این صفحه", draw_picture: "کشیدن تصویر", picture_hint: "دور تصویری را که پیدا نشده بکشید.",
    picture_n: "تصویر {n}", no_pictures: "تصویری ندارد.", tables: "جدول‌ها", tables_default: "پیش‌فرض کتاب",
    tables_image: "به‌صورت تصویر", tables_html: "جدول HTML", table_n: "جدول {n}",
  },
  en: {
    lang: "فارسی", undo: "Undo", rebuild: "Rebuild EPUB", done: "Done", tab_issues: "Issues", tab_pages: "Pages", tab_book: "Book",
    counts: "{rev} of {pages} pages reviewed · {iss} open issues", dirty: "Changes are not in the EPUB yet: press Rebuild EPUB.",
    built: "EPUB built ({s} s)", building: "Building the EPUB…", saved: "Saved", undone: "Undone", nothing_undo: "Nothing to undo",
    closed: "The review panel is closed. Your marks and corrections are saved and used by every later conversion.",
    r_body: "Body", r_heading: "Heading", r_byline: "Byline", r_epigraph: "Epigraph", r_quote: "Quote", r_verse: "Verse",
    r_note: "Footnote", r_endnote: "Endnote", r_reference: "Reference", r_caption: "Caption", r_table: "Table", r_figure: "Figure",
    r_contents: "Contents", r_header: "Running head", r_pagenum: "Page number", r_noise: "Noise", r_other: "Other",
    k_role: "Unsure role", k_text: "Unsure text", k_note: "Note without marker", k_marker: "Marker without note", k_numbering: "Gap in note numbers",
    k_contents: "Contents entry not found", k_chapter: "Chapter not in contents", k_page: "Poorly read page", k_left: "Page left out of the book",
    left_hint: "This page is not in the book: the converter took it for a second scan of page {n} (PDF {kept}). If it is not, correct its printed number or keep it in the book.",
    left_hint0: "This page is not in the book. If it should be, correct its printed number or keep it in the book.",
    keep: "Keep this page in the book", kept: "Kept in the book (no number, after the page before it in the PDF)", unkeep: "Undo", all: "All", show_ignored: "Ignored",
    no_issues: "No open issues left.", ignore: "Ignore", unignore: "Restore",
    sort_doubt: "Least sure first", sort_order: "Page order", pdf_page: "PDF {pdf}", book_page: "page {n}",
    pt_text: "Text", pt_opening: "Chapter opening", pt_part: "Part opening", pt_contents: "Contents", pt_cover: "Cover", pt_title: "Title page", pt_imprint: "Imprint",
    pt_dedication: "Dedication", pt_epigraph: "Epigraph", pt_endnotes: "Endnotes", pt_bibliography: "Bibliography", pt_index: "Index",
    pt_figure: "Figure", pt_table: "Table", pt_ad: "Advert", pt_blank: "Blank", pt_other: "Other",
    page_type: "Page type", printed: "Printed no.", tool_select: "Select", tool_cut: "Footnotes start here", accept: "Page is right",
    revert: "Revert page", reviewed: "Reviewed", prev: "Previous", next: "Next", preview: "This page in the EPUB (after a rebuild)",
    cut_hint: "Click the image where the footnotes begin.", lines: "Lines of the page", select_line: "Select a line (on the image or in the list of lines).",
    n_sel: "{n} lines selected", role: "Role", level: "Heading level", lv1: "1 chapter/part", lv2: "2 section", lv3: "3", note_no: "Note number",
    note_hint: "0 continues the note before", para: "Starts a paragraph", text: "Line text", text_hint: "Correct the text; empty means the OCR's text",
    reset: "Reset", words: "Words (click one to put a note marker after it)", markers: "Note markers", no_markers: "No markers",
    marker_no: "Number of the note this marker refers to:", remove: "Remove", add_heading: "Add a heading the OCR missed (above this line)",
    heading_text: "Heading text:", conf: "Confidence", c_role: "Role", c_text: "Text", model_role: "model: {r} ({p})", p_note: "footnote", p_head: "heading",
    p_start: "note start", weakest: "weakest word", sure: "sure", check: "check", unsure: "unsure", by_hand: "by hand",
    notes: "Notes on this page", how_marker: "linked at its marker", "how_end of page": "marker not found: end of page", how_endnote: "endnote",
    no_notes: "No notes.", place_hint: "To link it: select the text line and click the word the marker follows.",
    bulk: "Same role for every line with this text in the book", bulk_done: "{n} pages changed", missing: "Added lines", toc: "Contents entries",
    add_line: "Add a line the OCR missed (below this line)", line_text: "Line text:", split: "Split the line at the cursor",
    split_hint: "Put the cursor in the line's text where the second line begins, then press Split.",
    verse_hint: "For a verse line in two halves, separate the halves with /.", after_line: "below “{t}”", before_line: "above “{t}”",
    toc_title: "Title", toc_page: "Page", toc_level: "Level", add_row: "Add entry", units: "Parts and chapters", unit_add: "This page opens a:",
    u_part: "Part", u_chapter: "Chapter", u_section: "Section", u_figure: "Figure", u_none: "—", save_units: "Save chapters", units_hint: "This list decides the chapters; save, then rebuild.",
    sections: "Sections (from lines whose role is Heading)", meta: "Book details", m_title: "Title", m_subtitle: "Subtitle", m_author: "Author",
    m_translator: "Translator", m_editor: "Editor", m_publisher: "Publisher", m_isbn: "ISBN", m_language: "Language", save: "Save",
    cover: "Cover", cover_page: "Cover: PDF {pdf}", no_cover: "No cover chosen", use_cover: "Use this page as the cover", clear: "Clear",
    keys: "Keys", keys_help: "<kbd>↑</kbd> <kbd>↓</kbd> line · <kbd>PgUp</kbd> <kbd>PgDn</kbd> page · <kbd>n</kbd> next issue · roles: the letter on each button · <kbd>1</kbd>–<kbd>3</kbd> level · <kbd>p</kbd> paragraph · <kbd>Enter</kbd> edit text · <kbd>a</kbd> page is right · <kbd>Ctrl+Z</kbd> undo",
    error: "Error", contents_hint: "The entries of this contents page; leave empty to let the rules read it.",
    rotate: "Rotate", crop: "Crop picture", crop_hint: "Drag around the part of the page that is the picture.", crop_reset: "Whole page",
    pictures: "Pictures on this page", draw_picture: "Draw a picture", picture_hint: "Drag around a picture that was not found.",
    picture_n: "Picture {n}", no_pictures: "No pictures.", tables: "Tables", tables_default: "Book default",
    tables_image: "As images", tables_html: "HTML tables", table_n: "Table {n}",
  },
};

let LANG = "fa";
try { LANG = localStorage.getItem("parisaocr.lang") || localStorage.getItem("parisaocr.review.lang") || "fa"; } catch (e) { /* storage blocked */ }
const S = {book: null, issues: [], page: null, pdf: null, sel: [], tab: "issues", filter: "all", showIgnored: false,
           sortPages: "doubt", tool: "select", band: null, busy: false};

function t(key, vars) {
  let s = T[LANG][key] ?? T.en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split("{" + k + "}").join(v);
  return s;
}
const num = n => (n == null ? "" : Number(n).toLocaleString(LANG === "fa" ? "fa-IR" : "en-US"));
const roleName = r => t("r_" + r);
const level = d => (d == null ? "sure" : d >= 0.5 ? "unsure" : d >= 0.2 ? "check" : "sure");
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

function toast(msg, bad) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(el.__t);
  el.__t = setTimeout(() => (el.className = "toast"), bad ? 6000 : 1800);
}

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await r.json().catch(() => ({ok: false, error: r.statusText}));
  if (!r.ok || data.ok === false) throw new Error(data.error || r.statusText);
  return data;
}

// --- loading ---------------------------------------------------------------------------------------------------------

async function loadBook() {
  [S.book, S.issues] = await Promise.all([api("/api/book"), api("/api/issues")]);
  renderTop();
  renderSide();
}

async function openPage(pdf, rows) {
  const page = await api(`/api/page/${pdf}`);
  S.page = page;
  S.pdf = pdf;
  S.sel = rows || [];
  S.tool = "select";
  try { localStorage.setItem("parisaocr.review.page:" + S.book.epub, String(pdf)); } catch (e) { /* ignore */ }
  renderPage();
  renderSide();
  const target = S.sel.length && $(`rect.ln[data-row="${S.sel[0]}"]`);
  if (target) target.scrollIntoView({block: "center", behavior: "smooth"});
}

let issuesTimer = null;
function refreshLater() {
  clearTimeout(issuesTimer);
  issuesTimer = setTimeout(() => loadBook().catch(e => toast(e.message, true)), 400);
}

// --- top bar ---------------------------------------------------------------------------------------------------------

function renderTop() {
  const b = S.book;
  document.documentElement.lang = LANG;
  document.documentElement.dir = LANG === "fa" ? "rtl" : "ltr";
  $("#book").textContent = b.title;
  $("#book").dir = "auto";
  document.title = `${b.title} — ParisaOCR`;
  $("#counts").textContent = t("counts", {rev: num(b.reviewed), pages: num(b.pages.length), iss: num(b.issues)});
  const st = $("#status");
  if (S.busy || b.building) { st.innerHTML = `<span class="spin"></span> ${esc(t("building"))}`; st.className = "status"; }
  else if (b.dirty) { st.textContent = t("dirty"); st.className = "status warn"; }
  else { st.textContent = b.seconds != null ? t("built", {s: num(b.seconds)}) : ""; st.className = "status"; }
  $("#undo").textContent = t("undo");
  $("#undo").disabled = !b.history;
  $("#rebuild").textContent = t("rebuild");
  $("#rebuild").disabled = S.busy;
  $("#done").textContent = t("done");
  $("#lang").textContent = t("lang");
  $$("#tabs button").forEach(x => { x.textContent = t("tab_" + x.dataset.tab); x.classList.toggle("on", x.dataset.tab === S.tab); });
}

$("#lang").onclick = () => {
  LANG = LANG === "fa" ? "en" : "fa";
  try { localStorage.setItem("parisaocr.review.lang", LANG); } catch (e) { /* ignore */ }
  renderTop(); renderSide(); renderPage();
};
$$("#tabs button").forEach(x => x.onclick = () => { S.tab = x.dataset.tab; renderTop(); renderSide(); });
$("#undo").onclick = () => undo();
$("#rebuild").onclick = () => rebuild();
$("#done").onclick = async () => {
  try { await api("/api/quit", {}); } catch (e) { /* the server is going */ }
  document.body.innerHTML = `<div class="done">${esc(t("closed"))}</div>`;
};

async function undo() {
  try {
    const r = await api("/api/undo", {});
    if (!r.undone) return toast(t("nothing_undo"));
    toast(t("undone"));
    await loadBook();
    if (r.undone.pdf != null) await openPage(r.undone.pdf, S.pdf === r.undone.pdf ? S.sel : []);
  } catch (e) { toast(e.message, true); }
}

async function rebuild() {
  if (S.busy) return;
  S.busy = true;
  renderTop();
  try {
    const r = await api("/api/rebuild", {});
    S.busy = false;
    await loadBook();
    if (S.pdf != null) await openPage(S.pdf, S.sel);
    toast(t("built", {s: num(r.seconds)}));
    reloadPreview(true);
  } catch (e) {
    S.busy = false;
    renderTop();
    toast(e.message, true);
  }
}

// --- side panel ------------------------------------------------------------------------------------------------------

function renderSide() {
  const el = $("#tabbody");
  if (!S.book) return;
  if (S.tab === "issues") return renderIssues(el);
  if (S.tab === "pages") return renderPages(el);
  return renderBookTab(el);
}

function issueLine(x) {
  const what = x.kind === "numbering" ? (x.detail.missing || []).map(num).join("، ")
    : x.kind === "note" ? `${num(x.detail.num)}: ${x.text}`
    : x.kind === "left" && x.detail.kept != null ? `${t("book_page", {n: num(x.detail.number)})} = ${t("pdf_page", {pdf: num(x.detail.kept)})}`
    : x.text || "";
  return what;
}

function renderIssues(el) {
  const order = ["left", "note", "marker", "numbering", "contents", "chapter", "role", "text", "page"];
  const kinds = ["all", ...order.filter(k => S.issues.some(x => x.kind === k))];
  const count = k => S.issues.filter(x => (k === "all" || x.kind === k) && (S.showIgnored || !x.ignored)).length;
  const list = S.issues.filter(x => (S.filter === "all" || x.kind === S.filter) && (S.showIgnored || !x.ignored));
  el.innerHTML = `<div class="filters">${kinds.map(k => `<button type="button" class="chip ${S.filter === k ? "on" : ""}" data-k="${k}">${esc(k === "all" ? t("all") : t("k_" + k))} ${num(count(k))}</button>`).join("")}
      <button type="button" class="chip ${S.showIgnored ? "on" : ""}" data-ign>${esc(t("show_ignored"))}</button></div>
    ${list.length ? list.map((x, i) => `<div class="item ${x.ignored ? "ignored" : ""} ${S.pdf === x.pdf && x.row != null && S.sel.includes(x.row) ? "on" : ""}" data-i="${S.issues.indexOf(x)}">
        <span class="dot ${level(x.doubt)}"></span>
        <span class="what"><b>${esc(t("k_" + x.kind))}</b> <span class="muted">${x.pdf != null ? esc(t("pdf_page", {pdf: num(x.pdf)})) : ""}</span>
          <span class="t" dir="${x.ltr ? "ltr" : "auto"}">${esc(issueLine(x))}</span></span>
        <button type="button" class="btn small ghost x" data-ignore="${S.issues.indexOf(x)}" title="${esc(x.ignored ? t("unignore") : t("ignore"))}">${x.ignored ? "↺" : "×"}</button>
      </div>`).join("") : `<div class="empty">${esc(t("no_issues"))}</div>`}`;
  $$(".chip[data-k]", el).forEach(c => c.onclick = () => { S.filter = c.dataset.k; renderIssues(el); });
  $(".chip[data-ign]", el).onclick = () => { S.showIgnored = !S.showIgnored; renderIssues(el); };
  $$(".item", el).forEach(it => it.onclick = e => {
    if (e.target.closest("[data-ignore]")) return;
    const x = S.issues[+it.dataset.i];
    if (x.pdf != null) openPage(x.pdf, x.row != null ? [x.row] : []);
  });
  $$("[data-ignore]", el).forEach(b => b.onclick = async () => {
    const x = S.issues[+b.dataset.ignore];
    try { await api("/api/ignore", {id: x.id, ignored: !x.ignored}); await loadBook(); } catch (e) { toast(e.message, true); }
  });
}

function renderPages(el) {
  const pages = [...S.book.pages];
  if (S.sortPages === "doubt") pages.sort((a, b) => (a.reviewed - b.reviewed) || (b.issues - a.issues) || (b.doubt - a.doubt) || (a.pdf - b.pdf));
  el.innerHTML = `<div class="filters">
      <button type="button" class="chip ${S.sortPages === "doubt" ? "on" : ""}" data-s="doubt">${esc(t("sort_doubt"))}</button>
      <button type="button" class="chip ${S.sortPages === "order" ? "on" : ""}" data-s="order">${esc(t("sort_order"))}</button></div>
    ${pages.map(p => `<div class="pageitem ${S.pdf === p.pdf ? "on" : ""}" data-pdf="${p.pdf}">
        <img loading="lazy" src="/img/${p.pdf}.jpg" alt="">
        <span><b>${esc(t("pdf_page", {pdf: num(p.pdf)}))}</b>${p.n != null ? ` · <span class="muted">${esc(t("book_page", {n: num(p.n)}))}</span>` : ""}
          <br><span class="muted">${esc(t("pt_" + p.type) || p.type)}</span>${p.issues ? ` · <span class="muted">${num(p.issues)} ⚑</span>` : ""}
          <div class="bar"><i style="width:${Math.round(100 * p.doubt)}%"></i></div></span>
        <span>${p.reviewed ? `<span class="tick" title="${esc(t("reviewed"))}">✓</span>` : ""}</span></div>`).join("")}`;
  $$(".chip[data-s]", el).forEach(c => c.onclick = () => { S.sortPages = c.dataset.s; renderPages(el); });
  $$(".pageitem", el).forEach(it => it.onclick = () => openPage(+it.dataset.pdf));
}

function renderBookTab(el) {
  const b = S.book;
  const unitRow = (u, i) => `<div class="unit" data-i="${i}">
      <span class="pg" data-go="${u.pdf}">${esc(t("pdf_page", {pdf: num(u.pdf)}))}</span>
      <select data-kind>${["none", ...UNIT_KINDS].map(k => `<option value="${k}" ${u.kind === k ? "selected" : ""}>${esc(t("u_" + k))}</option>`).join("")}</select>
      <input type="text" data-title dir="auto" value="${esc(u.title || "")}" ${u.kind === "figure" ? "disabled" : ""}>
      <span></span></div>`;
  el.innerHTML = `
    <div class="section"><h3>${esc(t("units"))}</h3>
      <div id="units">${b.chapters.map(unitRow).join("")}</div>
      ${S.pdf != null ? `<div class="row" style="margin-top:6px"><span class="muted">${esc(t("unit_add"))}</span>
        ${UNIT_KINDS.map(k => `<button type="button" class="btn small" data-add="${k}">${esc(t("u_" + k))}</button>`).join("")}</div>` : ""}
      <p class="help">${esc(t("units_hint"))}</p>
      <button type="button" class="btn primary small" id="saveunits">${esc(t("save_units"))}</button></div>
    <div class="section"><h3>${esc(t("sections"))}</h3>
      ${b.sections.length ? b.sections.slice(0, 400).map(s => `<div class="unit" style="grid-template-columns:auto 1fr"><span class="pg" ${s.pdf != null ? `data-go="${s.pdf}"` : ""}>${esc(t("pdf_page", {pdf: num(s.pdf)}))}</span><span dir="auto">${esc(s.title)}</span></div>`).join("") : `<span class="muted">—</span>`}</div>
    <div class="section"><h3>${esc(t("meta"))}</h3>
      ${META.map(k => `<label class="field"><span>${esc(t("m_" + k))}${b.meta_h[k] ? ` · <span class="tag h">${esc(t("by_hand"))}</span>` : ""}</span>
        <input type="text" data-meta="${k}" dir="auto" value="${esc(b.meta_h[k] ?? b.meta[k] ?? "")}"></label>`).join("")}
      <button type="button" class="btn primary small" id="savemeta">${esc(t("save"))}</button></div>
    <div class="section"><h3>${esc(t("cover"))}</h3>
      <div class="row">${b.cover != null ? `<img src="/img/${b.cover}.jpg" alt="" style="width:70px;border:1px solid var(--line)"><span>${esc(t("cover_page", {pdf: num(b.cover)}))}</span>` : `<span class="muted">${esc(t("no_cover"))}</span>`}</div>
      <div class="row" style="margin-top:6px">${S.pdf != null ? `<button type="button" class="btn small" id="usecover">${esc(t("use_cover"))}</button>` : ""}
        <button type="button" class="btn small ghost" id="clearcover">${esc(t("clear"))}</button></div></div>
    <div class="section"><h3>${esc(t("keys"))}</h3><p class="help">${t("keys_help")}</p></div>`;
  $$("[data-go]", el).forEach(g => g.onclick = () => g.dataset.go && openPage(+g.dataset.go));
  $$("#units select[data-kind]", el).forEach(s => s.onchange = () => {
    const row = s.closest(".unit");
    row.querySelector("[data-title]").disabled = s.value === "figure";
  });
  $$("[data-add]", el).forEach(btn => btn.onclick = () => {
    const kind = btn.dataset.add;
    const first = S.page && S.page.lines.find(l => ["heading", "body"].includes(l.d.r));
    const title = kind === "figure" ? "" : (first ? (first.x || first.text) : "");
    const existing = b.chapters.findIndex(u => u.pdf === S.pdf);
    if (existing >= 0) b.chapters[existing] = {...b.chapters[existing], kind, title: b.chapters[existing].title || title};
    else b.chapters.push({pdf: S.pdf, n: S.page && S.page.n, kind, title});
    b.chapters.sort((a, c) => a.pdf - c.pdf);
    renderBookTab(el);
  });
  $("#saveunits", el).onclick = async () => {
    const pages = {};
    $$("#units .unit", el).forEach(row => {
      const u = b.chapters[+row.dataset.i];
      const kind = row.querySelector("[data-kind]").value;
      if (kind !== "none") pages[u.pdf] = {kind, title: row.querySelector("[data-title]").value.trim()};
    });
    try { await api("/api/marks", {pages}); toast(t("saved")); await loadBook(); } catch (e) { toast(e.message, true); }
  };
  $("#savemeta", el).onclick = async () => {
    const meta = {};
    $$("[data-meta]", el).forEach(i => {
      const k = i.dataset.meta, v = i.value.trim();
      if (v !== (b.meta[k] ?? "") || b.meta_h[k]) meta[k] = v || null;
    });
    try { await api("/api/book", {meta}); toast(t("saved")); await loadBook(); } catch (e) { toast(e.message, true); }
  };
  const uc = $("#usecover", el);
  if (uc) uc.onclick = async () => { try { await api("/api/book", {cover: S.pdf}); toast(t("saved")); await loadBook(); } catch (e) { toast(e.message, true); } };
  $("#clearcover", el).onclick = async () => { try { await api("/api/book", {cover: null}); toast(t("saved")); await loadBook(); } catch (e) { toast(e.message, true); } };
}

// --- the page --------------------------------------------------------------------------------------------------------

function ordered(lines) {
  return [...lines].sort((a, b) => (a.bbox[1] - b.bbox[1]) || (b.bbox[2] - a.bbox[2]));
}

function renderPage() {
  const p = S.page;
  if (!p) { $("#ptool").innerHTML = ""; $("#stage").innerHTML = ""; $("#insp").innerHTML = ""; return; }
  const pages = S.book.pages.map(x => x.pdf);
  const at = pages.indexOf(p.pdf);
  $("#ptool").innerHTML = `
    <button type="button" class="btn small" id="pprev" ${at <= 0 ? "disabled" : ""}>${esc(t("prev"))}</button>
    <span class="lbl">${esc(t("pdf_page", {pdf: num(p.pdf)}))}${p.n != null ? ` · ${esc(t("book_page", {n: num(p.n)}))}` : ""}</span>
    <button type="button" class="btn small" id="pnext" ${at >= pages.length - 1 ? "disabled" : ""}>${esc(t("next"))}</button>
    <label class="row"><span class="muted">${esc(t("page_type"))}</span>
      <select id="ptype">${PAGE_TYPES.map(k => `<option value="${k}" ${p.type === k ? "selected" : ""}>${esc(t("pt_" + k))}</option>`).join("")}</select></label>
    <label class="row"><span class="muted">${esc(t("printed"))}</span><input type="text" class="pn" id="ppn" dir="ltr" value="${esc(p.page.pn || "")}"></label>
    <button type="button" class="btn small ${S.tool === "cut" ? "on" : ""}" id="pcut">${esc(t("tool_cut"))}</button>
    ${isFigure(p) ? `<span class="row"><span class="muted">${esc(t("rotate"))}</span>${[0, 90, 180, 270].map(a => `<button type="button" class="btn small ${(p.page.rotate ?? p.rotate ?? 0) === a ? "on" : ""}" data-rot="${a}">${num(a)}°</button>`).join("")}</span>
      <button type="button" class="btn small ${S.tool === "crop" ? "on" : ""}" id="pcrop">${esc(t("crop"))}</button>
      ${p.page.crop ? `<button type="button" class="btn small ghost" id="pcropreset">${esc(t("crop_reset"))}</button>` : ""}`
      : `<button type="button" class="btn small ${S.tool === "picture" ? "on" : ""}" id="ppic">${esc(t("draw_picture"))}</button>`}
    ${(p.regions || []).some(r => r.kind === "table") || p.type === "table" ? `<label class="row"><span class="muted">${esc(t("tables"))}</span>
      <select id="ptables">${["", "image", "html"].map(k => `<option value="${k}" ${(p.page.tables || "") === k ? "selected" : ""}>${esc(t(k ? "tables_" + k : "tables_default"))}</option>`).join("")}</select></label>` : ""}
    <span class="spacer" style="flex:1"></span>
    ${p.reviewed ? `<span class="reviewed">✓ ${esc(t("reviewed"))}</span>` : ""}
    <button type="button" class="btn small" id="paccept">${esc(t("accept"))}</button>
    ${p.reviewed ? `<button type="button" class="btn small ghost" id="prevert">${esc(t("revert"))}</button>` : ""}`;
  $("#pprev").onclick = () => openPage(pages[at - 1]);
  $("#pnext").onclick = () => openPage(pages[at + 1]);
  $("#ptype").onchange = e => save({page: {type: e.target.value}});
  $("#ppn").onchange = e => save({page: {pn: e.target.value.trim() || null}});
  $("#pcut").onclick = () => { S.tool = S.tool === "cut" ? "select" : "cut"; renderPage(); if (S.tool === "cut") toast(t("cut_hint")); };
  $$("[data-rot]").forEach(b => b.onclick = () => save({page: {rotate: +b.dataset.rot}}));
  const pc = $("#pcrop");
  if (pc) pc.onclick = () => { S.tool = S.tool === "crop" ? "select" : "crop"; renderPage(); if (S.tool === "crop") toast(t("crop_hint")); };
  const pcr = $("#pcropreset");
  if (pcr) pcr.onclick = () => save({page: {crop: null}});
  const pp = $("#ppic");
  if (pp) pp.onclick = () => { S.tool = S.tool === "picture" ? "select" : "picture"; renderPage(); if (S.tool === "picture") toast(t("picture_hint")); };
  const ptab = $("#ptables");
  if (ptab) ptab.onchange = e => save({page: {tables: e.target.value || null}});
  $("#paccept").onclick = () => accept();
  const rv = $("#prevert");
  if (rv) rv.onclick = async () => { try { const r = await api(`/api/page/${p.pdf}/revert`, {}); keepExtras(r.page); refreshLater(); } catch (e) { toast(e.message, true); } };
  renderStage();
  renderInsp();
  $("#previewsum").textContent = t("preview");
  reloadPreview(false);
}

function keepExtras(page) {
  page.notes = S.page.notes;
  page.issues = S.page.issues;
  S.page = page;
  renderPage();
}

function roleColor(r) { return css("--r-" + r) || "#888"; }

function renderStage() {
  const p = S.page;
  const stage = $("#stage");
  const W = p.width, H = p.height;
  const maxW = Math.min(1100, $("#stagewrap").clientWidth - 24);
  stage.style.width = Math.max(320, maxW) + "px";
  stage.classList.toggle("cutting", S.tool === "cut");
  stage.classList.toggle("drawing", S.tool === "crop" || S.tool === "picture");
  const rects = ordered(p.lines).map(l => {
    const [x0, y0, x1, y1] = l.bbox;
    const d = Math.max(l.doubt.role ?? 0, l.doubt.text ?? 0);
    const c = roleColor(l.d.r);
    return `<rect class="ln ${level(d)} ${S.sel.includes(l.row) ? "sel" : ""}" data-row="${l.row}" x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="${c}" stroke="${c}"><title>${esc(roleName(l.d.r))}: ${esc(l.x || l.text)}</title></rect>`;
  }).join("");
  const regions = (p.regions || []).map(r => `<rect x="${r.bbox[0]}" y="${r.bbox[1]}" width="${r.bbox[2] - r.bbox[0]}" height="${r.bbox[3] - r.bbox[1]}"
      fill="none" stroke="${roleColor(r.kind)}" stroke-width="4" stroke-dasharray="14 8" style="vector-effect:non-scaling-stroke;pointer-events:none"/>`).join("");
  const crop = isFigure(p) && (p.page.crop || p.crop) ? (([x0, y0, x1, y1]) => `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="none"
      stroke="${css("--accent")}" stroke-width="4" style="vector-effect:non-scaling-stroke;pointer-events:none"/>`)(p.page.crop || p.crop) : "";
  stage.innerHTML = `<img src="/img/${p.pdf}.jpg?w=1400" alt="" draggable="false"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${regions}${crop}${rects}</svg>`;
  const svg = $("svg", stage);
  const pt = e => {
    const r = svg.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width * W, (e.clientY - r.top) / r.height * H];
  };
  svg.onmousedown = e => {
    const [x, y] = pt(e);
    if (S.tool === "cut") { cutAt(y); return; }
    const drawing = S.tool === "crop" || S.tool === "picture";
    const hit = drawing ? null : e.target.closest("rect.ln");
    if (hit) {
      const row = +hit.dataset.row;
      if (e.shiftKey || e.ctrlKey || e.metaKey) S.sel = S.sel.includes(row) ? S.sel.filter(r => r !== row) : [...S.sel, row];
      else S.sel = [row];
      renderStageSel(); renderInsp();
      return;
    }
    S.band = {x0: x, y0: y, x1: x, y1: y, add: e.shiftKey, tool: S.tool};
    const band = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    band.setAttribute("class", "band");
    svg.appendChild(band);
    const move = ev => {
      const [mx, my] = pt(ev);
      Object.assign(S.band, {x1: mx, y1: my});
      const bx0 = Math.min(S.band.x0, mx), by0 = Math.min(S.band.y0, my);
      band.setAttribute("x", bx0); band.setAttribute("y", by0);
      band.setAttribute("width", Math.abs(mx - S.band.x0)); band.setAttribute("height", Math.abs(my - S.band.y0));
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      const b = S.band; S.band = null; band.remove();
      const bx0 = Math.min(b.x0, b.x1), bx1 = Math.max(b.x0, b.x1), by0 = Math.min(b.y0, b.y1), by1 = Math.max(b.y0, b.y1);
      if (drawing) {
        S.tool = "select";
        if (bx1 - bx0 < 20 || by1 - by0 < 20) { renderPage(); return; }
        const box = [bx0, by0, bx1, by1].map(Math.round);
        if (b.tool === "crop") { save({page: {crop: box}}); return; }
        const figures = [...pictureBoxes(p), box];
        const inside = p.lines.filter(l => (l.bbox[0] + l.bbox[2]) / 2 >= bx0 && (l.bbox[0] + l.bbox[2]) / 2 <= bx1
                                         && (l.bbox[1] + l.bbox[3]) / 2 >= by0 && (l.bbox[1] + l.bbox[3]) / 2 <= by1);
        save({page: {figures}, lines: Object.fromEntries(inside.map(l => [l.row, {r: "figure"}]))});
        return;
      }
      if (bx1 - bx0 < 5 && by1 - by0 < 5) { if (!b.add) { S.sel = []; renderStageSel(); renderInsp(); } return; }
      const rows = p.lines.filter(l => l.bbox[0] < bx1 && l.bbox[2] > bx0 && l.bbox[1] < by1 && l.bbox[3] > by0).map(l => l.row);
      S.sel = b.add ? [...new Set([...S.sel, ...rows])] : rows;
      renderStageSel(); renderInsp();
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };
}

function renderStageSel() {
  $$("#stage rect.ln").forEach(r => r.classList.toggle("sel", S.sel.includes(+r.dataset.row)));
  $$("#insp .lrow").forEach(r => r.classList.toggle("sel", S.sel.includes(+r.dataset.row)));
}

function leadNumber(text) {
  const m = (text || "").trim().match(/^[\s'"(\[\-–.]*([0-9۰-۹٠-٩]{1,3})/);
  if (!m) return 0;
  return +m[1].replace(/[۰-۹]/g, d => "۰۱۲۳۴۵۶۷۸۹".indexOf(d)).replace(/[٠-٩]/g, d => "٠١٢٣٤٥٦٧٨٩".indexOf(d));
}

function cutAt(y) {
  const p = S.page;
  const lines = {};
  for (const l of p.lines) {
    if (["header", "pagenum", "noise", "figure", "table"].includes(l.d.r)) continue;
    if (l.bbox[1] >= y - 2) lines[l.row] = {r: "note", n: l.d.r === "note" && l.d.n != null ? l.d.n : leadNumber(l.x || l.text)};
    else if (l.d.r === "note") lines[l.row] = {r: "body"};
  }
  S.tool = "select";
  save({lines});
}

// --- the inspector ---------------------------------------------------------------------------------------------------

function selLines() { return S.page.lines.filter(l => S.sel.includes(l.row)); }

function renderInsp() {
  const p = S.page;
  const el = $("#insp");
  const sel = selLines();
  const one = sel.length === 1 ? sel[0] : null;
  const cur = sel.length ? sel[0].d : null;
  let html = "";
  if (p.left || p.page.keep) {  // a page the converter left out of the book (taken for a second scan)
    html += p.page.keep
      ? `<div class="block warn"><p>${esc(t("kept"))}</p><button type="button" class="btn small ghost" id="unkeep">${esc(t("unkeep"))}</button></div>`
      : `<div class="block warn"><p>${esc(p.left.kept != null ? t("left_hint", {n: num(p.left.number), kept: num(p.left.kept)}) : t("left_hint0"))}</p>
         <button type="button" class="btn small primary" id="keep">${esc(t("keep"))}</button></div>`;
  }
  if (!sel.length) html += `<div class="block"><p class="help">${esc(t("select_line"))}</p><p class="help">${t("keys_help")}</p></div>`;
  else {
    const headLabel = one ? `${esc(roleName(cur.r))}${one.h.length ? ` <span class="tag h">${esc(t("by_hand"))}</span>` : ""}` : esc(t("n_sel", {n: num(sel.length)}));
    html += `<div class="block"><h3>${headLabel}</h3>
      <div class="roles">${ROLES.map(r => `<button type="button" data-role="${r}" class="${sel.every(l => l.d.r === r) ? "on" : ""}" style="${sel.every(l => l.d.r === r) ? `background:${roleColor(r)}` : `border-inline-start:4px solid ${roleColor(r)}`}">${esc(roleName(r))}<kbd>${esc(KEYS[r])}</kbd></button>`).join("")}</div>`;
    if (sel.every(l => l.d.r === "heading")) {
      html += `<div class="row" style="margin-top:8px"><span class="muted">${esc(t("level"))}</span>
        ${[1, 2, 3].map(k => `<button type="button" class="btn small ${sel.every(l => (l.d.l || 2) === k) ? "on" : ""}" data-level="${k}">${esc(t("lv" + k))}</button>`).join("")}</div>`;
    }
    if (one && ["note", "endnote"].includes(cur.r)) {
      html += `<label class="field" style="margin-top:8px"><span>${esc(t("note_no"))} · ${esc(t("note_hint"))}</span>
        <input type="number" id="noteno" min="0" max="999" value="${cur.n ?? 0}" dir="ltr"></label>`;
    }
    if (sel.every(l => PARA_ROLES.includes(l.d.r))) {
      const all = sel.every(l => l.d.p);
      html += `<label class="row" style="margin-top:8px"><input type="checkbox" id="para" ${all ? "checked" : ""}> ${esc(t("para"))} <kbd class="muted">p</kbd></label>`;
    }
    if (one && ["header", "noise", "pagenum"].includes(cur.r)) {
      html += `<div style="margin-top:8px"><button type="button" class="btn small" id="bulk">${esc(t("bulk"))}</button></div>`;
    }
    html += `</div>`;
    if (one) html += lineDetail(one);
  }
  html += notesBlock(p);
  if (!isFigure(p)) html += picturesBlock(p);
  if (p.type === "contents") html += tocBlock(p);
  html += missingBlock(p);
  html += `<div class="block"><h3>${esc(t("lines"))}</h3><div class="lines">${ordered(p.lines).map(l => {
      const d = Math.max(l.doubt.role ?? 0, l.doubt.text ?? 0);
      return `<div class="lrow ${S.sel.includes(l.row) ? "sel" : ""}" data-row="${l.row}">
        <span class="sw" style="background:${roleColor(l.d.r)}"></span>
        <span class="tx" dir="auto">${esc(l.x || l.text)}</span>
        <span class="tags">${l.d.r === "heading" ? `<span class="tag">${num(l.d.l || 2)}</span>` : ""}${["note", "endnote"].includes(l.d.r) && l.d.n ? `<span class="tag">${num(l.d.n)}</span>` : ""}${(l.d.m || []).length ? `<span class="tag">↑${(l.d.m || []).map(num).join(",")}</span>` : ""}${l.h.length ? `<span class="tag h">✎</span>` : ""}<span class="dot ${level(d)}" style="margin:0"></span></span></div>`;
    }).join("")}</div></div>`;
  el.innerHTML = html;
  bindInsp(el, sel, one);
}

function missingBlock(p) {
  // lines the OCR missed, added by hand (or headings a labeller gave): role, level or note number, text, where
  const ms = p.missing || [];
  if (!ms.length) return "";
  const rows = ms.map((m, i) => {
    const r = m.r || "heading";
    const extra = r === "heading"
      ? `<select data-mf="l" data-i="${i}" title="${esc(t("level"))}">${[1, 2, 3].map(k => `<option value="${k}" ${(m.l || 1) === k ? "selected" : ""}>${num(k)}</option>`).join("")}</select>`
      : ["note", "endnote"].includes(r)
        ? `<input type="number" data-mf="n" data-i="${i}" min="0" max="999" value="${m.n ?? 0}" dir="ltr" style="width:58px" title="${esc(t("note_no"))}">` : "";
    const anchor = p.lines.find(l => l.row === (m.after ?? m.before));
    const where = anchor ? t(m.after != null ? "after_line" : "before_line", {t: (anchor.x || anchor.text).slice(0, 28)}) : "";
    return `<div class="added"><div class="row">
        <select data-mf="r" data-i="${i}">${ROLES.map(x => `<option value="${x}" ${x === r ? "selected" : ""}>${esc(roleName(x))}</option>`).join("")}</select>${extra}
        <span class="spacer" style="flex:1"></span><button type="button" class="btn small ghost" data-unmiss="${i}">${esc(t("remove"))}</button></div>
      <input type="text" data-mf="t" data-i="${i}" dir="auto" value="${esc(m.t || "")}">
      ${where ? `<span class="help" dir="auto">${esc(where)}</span>` : ""}</div>`;
  }).join("");
  return `<div class="block"><h3>${esc(t("missing"))}</h3>${rows}</div>`;
}

function addedAfter(l, text) {
  // a line below L, of its role: a note's next note when it starts with a number, else the note going on
  const add = {r: l.d.r, t: text, after: l.row, p: false};
  if (["note", "endnote"].includes(l.d.r)) add.n = leadNumber(text);
  if (l.d.r === "heading") add.l = l.d.l || 2;
  return add;
}

function lineDetail(l) {
  const ltr = latinShare(l.x || l.text) > 0.5;
  const words = lineWords(l);
  const m = l.d.m || [], a = l.d.a || [];
  const mod = l.model || {};
  const fmt = v => v == null ? "–" : (Math.round(v * 100) / 100).toLocaleString(LANG === "fa" ? "fa-IR" : "en-US");
  const markedAt = {};
  const head = s => { const c = s.search(/[0-9۰-۹٠-٩]/); return c > 0 ? s.slice(0, c) : s; };  // the word before glued digits
  a.forEach((phrase, i) => { markedAt[head(phrase.trim().split(/\s+/).pop())] = m[i]; });
  let html = `<div class="block"><h3>${esc(t("text"))}${l.h.includes("x") ? ` <span class="tag h">${esc(t("by_hand"))}</span>` : ""}</h3>
    <textarea id="ltext" rows="2" dir="auto" placeholder="${esc(l.text)}">${esc(l.x || l.text)}</textarea>
    <div class="row" style="margin-top:4px"><span class="help">${esc(t("text_hint"))}</span><span class="spacer" style="flex:1"></span>
      ${l.x ? `<button type="button" class="btn small ghost" id="treset">${esc(t("reset"))}</button>` : ""}</div>`;
  if (MARKER_ROLES.includes(l.d.r)) {
    html += `<p class="help" style="margin:8px 0 0">${esc(t("words"))}</p>
      <div class="words ${ltr ? "ltr" : ""}">${words.map((w, i) => `<span class="word ${w.conf < 90 ? "weak" : ""} ${markedAt[head(w.text)] != null ? "marked" : ""}" data-w="${i}" title="${num(Math.round(w.conf))}%">${esc(w.text)}${markedAt[head(w.text)] != null ? `<sup>${num(markedAt[head(w.text)])}</sup>` : ""}</span>`).join("")}</div>
      <div class="row"><span class="muted">${esc(t("markers"))}:</span>${m.length ? m.map((k, i) => `<span class="tag">${num(k)}${a[i] ? ` ← ${esc(a[i])}` : ""} <button type="button" class="btn small ghost" data-unmark="${i}">×</button></span>`).join("") : `<span class="muted">${esc(t("no_markers"))}</span>`}</div>`;
  }
  if (l.d.r === "verse") html += `<p class="help" style="margin:6px 0 0">${esc(t("verse_hint"))}</p>`;
  html += `<div class="row" style="margin-top:8px"><button type="button" class="btn small" id="splitline">${esc(t("split"))}</button>
    <button type="button" class="btn small" id="addline">${esc(t("add_line"))}</button>
    <button type="button" class="btn small" id="addhead">${esc(t("add_heading"))}</button></div></div>`;
  html += `<div class="block"><h3>${esc(t("conf"))}</h3><div class="conf">
      <span class="k">${esc(t("c_role"))}</span><span><span class="dot ${level(l.doubt.role)}" style="display:inline-block;margin:0 4px"></span>${esc(t(level(l.doubt.role)))}${mod.role ? ` · ${esc(t("model_role", {r: roleName(mod.role) || mod.role, p: fmt(mod.role_p)}))}` : ""}</span>
      ${mod.note != null ? `<span class="k">${esc(t("p_note"))}</span><span>${fmt(mod.note)}</span>` : ""}
      ${mod.heading != null ? `<span class="k">${esc(t("p_head"))}</span><span>${fmt(mod.heading)}</span>` : ""}
      ${mod.start != null && ["note", "endnote"].includes(l.d.r) ? `<span class="k">${esc(t("p_start"))}</span><span>${fmt(mod.start)}</span>` : ""}
      <span class="k">${esc(t("c_text"))}</span><span><span class="dot ${level(l.doubt.text)}" style="display:inline-block;margin:0 4px"></span>${esc(t(level(l.doubt.text)))} · ${esc(t("weakest"))} ${num(Math.round(l.weak))}%</span>
    </div></div>`;
  return html;
}

function isFigure(p) { return p.kind === "figure" || ["figure", "cover"].includes(p.type); }

function pictureBoxes(p) {
  return (p.page.figures || (p.regions || []).filter(r => r.kind === "figure").map(r => r.bbox)).map(b => b.map(Math.round));
}

function picturesBlock(p) {
  const boxes = pictureBoxes(p);
  const tables = (p.regions || []).filter(r => r.kind === "table");
  if (!boxes.length && !tables.length) return "";
  return `<div class="block"><h3>${esc(t("pictures"))}</h3>
    ${boxes.map((b, i) => `<div class="row"><span class="tag" style="border-inline-start:4px solid ${roleColor("figure")}">${esc(t("picture_n", {n: num(i + 1)}))}</span>
      <span class="muted mono">${b.map(v => num(v)).join(" ")}</span><button type="button" class="btn small ghost" data-unpic="${i}">${esc(t("remove"))}</button></div>`).join("")}
    ${tables.map((r, i) => `<div class="row"><span class="tag" style="border-inline-start:4px solid ${roleColor("table")}">${esc(t("table_n", {n: num(i + 1)}))}</span></div>`).join("")}
  </div>`;
}

function notesBlock(p) {
  const notes = p.notes || [];
  return `<div class="block notes"><h3>${esc(t("notes"))}</h3>${notes.length ? notes.map(n => `<div class="nt ${n.how === "end of page" ? "bad" : ""}">
      <span class="num">${num(n.num)}</span><span dir="auto">${esc(n.text.slice(0, 90))}</span><span class="how">${esc(t("how_" + n.how))}</span></div>`).join("")
      + (notes.some(n => n.how === "end of page") ? `<p class="help">${esc(t("place_hint"))}</p>` : "") : `<span class="muted">${esc(t("no_notes"))}</span>`}</div>`;
}

function tocBlock(p) {
  const rows = (p.toc || []).map((e, i) => `<tr data-i="${i}"><td><input type="text" data-f="t" dir="auto" value="${esc(e.t || "")}"></td>
      <td style="width:62px"><input type="text" data-f="pg" dir="ltr" value="${esc(e.pg || "")}"></td>
      <td style="width:52px"><input type="number" data-f="l" min="1" max="3" value="${e.l || 1}"></td>
      <td style="width:28px"><button type="button" class="btn small ghost" data-deltoc="${i}">×</button></td></tr>`).join("");
  return `<div class="block"><h3>${esc(t("toc"))}</h3><p class="help">${esc(t("contents_hint"))}</p>
    <table class="toc"><tr class="muted"><td>${esc(t("toc_title"))}</td><td>${esc(t("toc_page"))}</td><td>${esc(t("toc_level"))}</td><td></td></tr>${rows}</table>
    <div class="row" style="margin-top:6px"><button type="button" class="btn small" id="addtoc">${esc(t("add_row"))}</button>
      <button type="button" class="btn small primary" id="savetoc">${esc(t("save"))}</button></div></div>`;
}

function bindInsp(el, sel, one) {
  $$("[data-role]", el).forEach(b => b.onclick = () => setRole(b.dataset.role));
  $$("[data-level]", el).forEach(b => b.onclick = () => setLevel(+b.dataset.level));
  const no = $("#noteno", el);
  if (no) no.onchange = () => save({lines: {[one.row]: {n: Math.max(0, parseInt(no.value || "0", 10))}}});
  const para = $("#para", el);
  if (para) para.onchange = () => save({lines: Object.fromEntries(sel.map(l => [l.row, {p: para.checked}]))});
  const bulk = $("#bulk", el);
  if (bulk) bulk.onclick = async () => {
    try {
      const r = await api("/api/bulk", {text: one.text, edits: {r: one.d.r}});
      toast(t("bulk_done", {n: num(r.pages.length)}));
      await loadBook();
      await openPage(S.pdf, S.sel);
    } catch (e) { toast(e.message, true); }
  };
  const tx = $("#ltext", el);
  if (tx) {
    tx.onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); tx.blur(); } if (e.key === "Escape") { tx.value = one.x || one.text; tx.blur(); } };
    tx.onchange = () => {
      const v = tx.value.replace(/\s+/g, " ").trim();
      if (v === (one.x || one.text)) return;  // left as it was (a click on Split blurs it)
      save({lines: {[one.row]: {x: v && v !== one.text ? v : null}}});
    };
  }
  const tr = $("#treset", el);
  if (tr) tr.onclick = () => save({lines: {[one.row]: {x: null}}});
  $$("[data-w]", el).forEach(w => w.onclick = () => addMarker(one, +w.dataset.w));
  $$("[data-unmark]", el).forEach(b => b.onclick = () => removeMarker(one, +b.dataset.unmark));
  const kp = $("#keep", el);
  if (kp) kp.onclick = () => save({page: {keep: true}});
  const uk = $("#unkeep", el);
  if (uk) uk.onclick = () => save({page: {keep: null}});
  const sp = $("#splitline", el);
  if (sp) sp.onclick = () => {
    const box = $("#ltext", el);
    const at = box.selectionStart ?? 0, full = box.value;
    const a = full.slice(0, at).replace(/\s+/g, " ").trim(), b = full.slice(at).replace(/\s+/g, " ").trim();
    if (!a || !b) { toast(t("split_hint"), true); return; }
    save({lines: {[one.row]: {x: a !== one.text ? a : null}}, missing: [...(S.page.missing || []), addedAfter(one, b)]});
  };
  const al = $("#addline", el);
  if (al) al.onclick = () => {
    const text = (prompt(t("line_text"), "") || "").replace(/\s+/g, " ").trim();
    if (text) save({missing: [...(S.page.missing || []), addedAfter(one, text)]});
  };
  $$("[data-mf]", el).forEach(inp => inp.onchange = () => {
    const i = +inp.dataset.i, f = inp.dataset.mf;
    const missing = (S.page.missing || []).map(m => ({...m}));
    let v = inp.value;
    if (f === "l" || f === "n") v = Math.max(0, parseInt(v || "0", 10) || 0);
    if (f === "t") { v = v.replace(/\s+/g, " ").trim(); if (!v) { inp.value = missing[i].t; return; } }
    missing[i][f] = v;
    if (f === "r") {
      delete missing[i].l; delete missing[i].n;
      if (v === "heading") missing[i].l = 2;
      if (["note", "endnote"].includes(v)) missing[i].n = leadNumber(missing[i].t);
    }
    save({missing});
  });
  const ah = $("#addhead", el);
  if (ah) ah.onclick = () => {
    const text = prompt(t("heading_text"), "");
    if (!text || !text.trim()) return;
    const lv = parseInt(prompt(t("level") + " (1-3)", "2") || "2", 10) || 2;
    const missing = [...(S.page.missing || []), {r: "heading", l: Math.min(3, Math.max(1, lv)), t: text.trim(), before: one.row}];
    save({missing});
  };
  $$("[data-unmiss]", el).forEach(b => b.onclick = () => {
    const missing = (S.page.missing || []).filter((_, i) => i !== +b.dataset.unmiss);
    save({missing});
  });
  $$(".lrow", el).forEach(r => r.onclick = e => {
    const row = +r.dataset.row;
    if (e.shiftKey || e.ctrlKey || e.metaKey) S.sel = S.sel.includes(row) ? S.sel.filter(x => x !== row) : [...S.sel, row];
    else S.sel = [row];
    renderStageSel(); renderInsp();
    const rect = $(`rect.ln[data-row="${row}"]`);
    if (rect) rect.scrollIntoView({block: "nearest", behavior: "smooth"});
  });
  $$("[data-unpic]", el).forEach(b => b.onclick = () => {
    const boxes = pictureBoxes(S.page);
    const [x0, y0, x1, y1] = boxes[+b.dataset.unpic];
    const figures = boxes.filter((_, i) => i !== +b.dataset.unpic);
    const inside = S.page.lines.filter(l => l.d.r === "figure" && (l.bbox[0] + l.bbox[2]) / 2 >= x0 && (l.bbox[0] + l.bbox[2]) / 2 <= x1
                                           && (l.bbox[1] + l.bbox[3]) / 2 >= y0 && (l.bbox[1] + l.bbox[3]) / 2 <= y1);
    save({page: {figures}, lines: Object.fromEntries(inside.map(l => [l.row, {r: "body"}]))});  // its text is text again
  });
  const addtoc = $("#addtoc", el);
  if (addtoc) addtoc.onclick = () => { S.page.toc = [...(S.page.toc || []), {t: "", pg: "", l: 1}]; renderInsp(); };
  $$("[data-deltoc]", el).forEach(b => b.onclick = () => { S.page.toc = S.page.toc.filter((_, i) => i !== +b.dataset.deltoc); renderInsp(); });
  const savetoc = $("#savetoc", el);
  if (savetoc) savetoc.onclick = () => {
    const toc = $$("table.toc tr[data-i]", el).map(tr => ({
      t: $("[data-f=t]", tr).value.trim(), pg: $("[data-f=pg]", tr).value.trim(), l: parseInt($("[data-f=l]", tr).value || "1", 10) || 1,
    })).filter(e => e.t);
    save({toc});
  };
}

// --- changes ---------------------------------------------------------------------------------------------------------

async function save(edits) {
  if (!S.page) return;
  try {
    const r = await api(`/api/page/${S.page.pdf}`, edits);
    keepExtras(r.page);
    S.book.dirty = true;
    renderTop();
    toast(t("saved"));
    refreshLater();
  } catch (e) { toast(e.message, true); }
}

async function accept() {
  try {
    const r = await api(`/api/page/${S.page.pdf}/accept`, {});
    keepExtras(r.page);
    toast(t("saved"));
    refreshLater();
  } catch (e) { toast(e.message, true); }
}

function setRole(role) {
  const sel = selLines();
  if (!sel.length) return;
  const lines = {};
  for (const l of sel) {
    const f = {r: role};
    if (role === "heading" && !l.d.l) f.l = 2;
    if (["note", "endnote"].includes(role) && l.d.r !== role) f.n = leadNumber(l.x || l.text);
    lines[l.row] = f;
  }
  save({lines});
}

function setLevel(k) {
  const sel = selLines();
  if (!sel.length) return;
  save({lines: Object.fromEntries(sel.map(l => [l.row, {r: "heading", l: k}]))});
}

function togglePara() {
  const sel = selLines().filter(l => PARA_ROLES.includes(l.d.r));
  if (!sel.length) return;
  const all = sel.every(l => l.d.p);
  save({lines: Object.fromEntries(sel.map(l => [l.row, {p: !all}]))});
}

function uniquePhrase(text, words, i) {
  // the shortest run of words ending at word I that occurs once in the line: where the marker goes (word I up to digits
  // the OCR glued into it, "است»۲خیلی": the marker goes in their place)
  const tok = words[i].text, cut = tok.search(/[0-9۰-۹٠-٩]/);
  const last = cut > 0 ? tok.slice(0, cut) : tok;
  for (let k = 1; k <= Math.min(4, i + 1); k++) {
    const phrase = [...words.slice(i - k + 1, i).map(w => w.text), last].join(" ");
    const first = text.indexOf(phrase);
    if (first >= 0 && text.indexOf(phrase, first + 1) < 0) return phrase;
  }
  return last;
}

function lineWords(l) {
  // the words to click: the OCR's (with their confidence), or those of the text as corrected
  if (l.x) return l.x.split(/\s+/).filter(Boolean).map(text => ({text, conf: 100}));
  const ltr = latinShare(l.text) > 0.5;
  return [...(l.words || [])].sort((a, b) => ltr ? a.bbox[0] - b.bbox[0] : b.bbox[2] - a.bbox[2]);
}

function addMarker(l, wi) {
  const words = lineWords(l);
  const text = l.x || l.text;
  const notes = (S.page.notes || []).map(n => n.num);
  const used = new Set((S.page.lines.flatMap(x => x.d.m || [])));
  const guess = notes.find(k => !used.has(k)) ?? (Math.max(0, ...notes, ...used) + 1);
  const k = parseInt(prompt(t("marker_no"), String(guess)) || "", 10);
  if (!k) return;
  const phrase = uniquePhrase(text, words, wi);
  const m = [...(l.d.m || [])], a = [...(l.d.a || [])];
  // a marker set earlier without its word is replaced: the person places each one
  const pairs = m.map((x, i) => [x, a[i]]).filter(([x, w]) => w);
  pairs.push([k, phrase]);
  pairs.sort((p, q) => (text.indexOf(p[1]) + p[1].length) - (text.indexOf(q[1]) + q[1].length));
  save({lines: {[l.row]: {m: pairs.map(p => p[0]), a: pairs.map(p => p[1])}}});
}

function removeMarker(l, i) {
  const m = [...(l.d.m || [])], a = [...(l.d.a || [])];
  m.splice(i, 1);
  if (a.length > i) a.splice(i, 1);
  save({lines: {[l.row]: m.length ? {m, a: a.length === m.length ? a : []} : {m: [], a: []}}});
}

// --- the EPUB preview ------------------------------------------------------------------------------------------------

function reloadPreview(force) {
  const det = $("#preview");
  const iframe = $("#pv");
  if (!S.page || !S.page.href) { det.hidden = true; return; }
  det.hidden = false;
  const src = "/epub/" + S.page.href.split("#")[0].split("/").map(encodeURIComponent).join("/") + "#" + (S.page.href.split("#")[1] || "");
  if (!det.open) { iframe.dataset.want = src; return; }
  if (force || iframe.dataset.src !== src) {
    iframe.dataset.src = src;
    iframe.src = force ? src.replace("#", `?t=${Date.now()}#`) : src;
  }
}
$("#preview").addEventListener("toggle", () => reloadPreview(false));

// --- keys ------------------------------------------------------------------------------------------------------------

document.addEventListener("keydown", e => {
  const tag = (e.target.tagName || "").toLowerCase();
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !["input", "textarea"].includes(tag)) { e.preventDefault(); undo(); return; }
  if (["input", "textarea", "select"].includes(tag) || e.ctrlKey || e.metaKey || e.altKey || !S.page) return;
  const lines = ordered(S.page.lines);
  const at = S.sel.length ? lines.findIndex(l => l.row === S.sel[S.sel.length - 1]) : -1;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const j = Math.max(0, Math.min(lines.length - 1, at + (e.key === "ArrowDown" ? 1 : -1)));
    if (!lines[j]) return;
    S.sel = e.shiftKey ? [...new Set([...S.sel, lines[j].row])] : [lines[j].row];
    renderStageSel(); renderInsp();
    const rect = $(`rect.ln[data-row="${lines[j].row}"]`);
    if (rect) rect.scrollIntoView({block: "nearest", behavior: "smooth"});
    return;
  }
  if (e.key === "PageDown" || e.key === "PageUp") {
    e.preventDefault();
    const pages = S.book.pages.map(x => x.pdf);
    const k = pages.indexOf(S.pdf) + (e.key === "PageDown" ? 1 : -1);
    if (pages[k] != null) openPage(pages[k]);
    return;
  }
  if (e.key === "Escape") { S.sel = []; S.tool = "select"; renderPage(); return; }
  if (e.key === "Enter") { const tx = $("#ltext"); if (tx) { e.preventDefault(); tx.focus(); tx.select(); } return; }
  if (e.key === "n" || e.key === "N") {
    e.preventDefault();
    const open = S.issues.filter(x => !x.ignored && x.pdf != null);
    if (!open.length) return;
    const cur = open.findIndex(x => x.pdf === S.pdf && (x.row == null || S.sel.includes(x.row)));
    const nx = open[(cur + (e.key === "n" ? 1 : open.length - 1) + open.length) % open.length] || open[0];
    openPage(nx.pdf, nx.row != null ? [nx.row] : []);
    return;
  }
  if (e.key === "p") { e.preventDefault(); togglePara(); return; }
  if (e.key === "a") { e.preventDefault(); accept(); return; }
  if (["1", "2", "3"].includes(e.key)) { e.preventDefault(); setLevel(+e.key); return; }
  const role = ROLE_OF_KEY[e.key];
  if (role) { e.preventDefault(); setRole(role); }
});

window.addEventListener("resize", () => { if (S.page) renderStage(); });

// --- start -----------------------------------------------------------------------------------------------------------

(async () => {
  try {
    await loadBook();
    let first = null;
    try { first = +localStorage.getItem("parisaocr.review.page:" + S.book.epub) || null; } catch (e) { /* ignore */ }
    if (!first || !S.book.pages.some(p => p.pdf === first)) {
      const top = S.issues.find(x => !x.ignored && x.pdf != null);
      first = top ? top.pdf : (S.book.pages[0] && S.book.pages[0].pdf);
      if (top && top.row != null) { await openPage(first, [top.row]); return; }
    }
    if (first != null) await openPage(first);
  } catch (e) {
    document.body.innerHTML = `<div class="done">${esc(t("error"))}: ${esc(e.message)}</div>`;
  }
})();
