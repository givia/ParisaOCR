// ParisaOCR app: the page. It talks to the local server (server.py) only; no external scripts or fonts.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const encPath = p => String(p).split("/").map(encodeURIComponent).join("/");
const ACTIVE = ["queued", "running"];
const active = job => ACTIVE.includes(job.status);
const main = $("#main");

const T = {
  fa: {
    app_name: "پریسا اوسی‌آر", nav_jobs: "کارها", nav_settings: "تنظیمات", lang_toggle: "English",
    start_title: "چه کاری انجام شود؟",
    kind_epub: "کتاب اسکن‌شده به EPUB",
    kind_epub_desc: "فصل‌ها و فهرست، پانویس‌های پیوندخورده، شعر، تصویرها و شمارهٔ صفحه‌ها؛ با گزارش و بازبینی فصل‌ها",
    kind_ocr: "اوسی‌آر: متن و PDF جست‌وجوپذیر",
    kind_ocr_desc: "صفحه‌ها (یک PDF یا چند تصویر) به متن ساده، hOCR، JSONL یا PDF جست‌وجوپذیر",
    kind_pages: "استخراج تصویر صفحه‌ها", kind_pages_desc: "صفحه‌های یک PDF به تصویر PNG، بدون اوسی‌آر",
    kind_short_epub: "EPUB", kind_short_ocr: "اوسی‌آر", kind_short_pages: "صفحه‌ها",
    jobs: "کارها", no_jobs: "هنوز کاری نیست. یکی از گزینه‌های بالا را انتخاب کنید.",
    st_draft: "پیش‌نویس", st_queued: "در صف", st_running: "در حال اجرا", st_waiting: "منتظر تأیید هزینه", st_done: "انجام شد",
    st_failed: "ناموفق", st_cancelled: "لغو شد", st_interrupted: "نیمه‌کاره",
    new_epub: "تبدیل کتاب به EPUB",
    new_epub_lead: "PDF کتاب اسکن‌شده را بدهید. اوسی‌آر، صفحه‌آرایی و ساختار کتاب روی همین رایانه انجام می‌شود.",
    new_ocr: "اوسی‌آر", new_ocr_lead: "یک PDF یا تصویرهای صفحه را بدهید و قالب‌های خروجی را انتخاب کنید.",
    new_pages: "استخراج تصویر صفحه‌ها", new_pages_lead: "صفحه‌های PDF به تصویر PNG درمی‌آیند: تصویر اسکن درون PDF، یا رندر صفحه.",
    edit_title: "ویرایش و اجرای دوباره", input: "ورودی",
    drop_epub: "PDF کتاب را اینجا رها کنید", drop_ocr: "PDF یا تصویرهای صفحه را اینجا رها کنید", drop_pages: "PDF را اینجا رها کنید",
    choose_file: "انتخاب فایل", choose_folder: "انتخاب پوشه",
    or_path_one: "یا مسیر PDF روی همین رایانه (کپی نمی‌شود)",
    or_path_many: "یا مسیر فایل‌ها یا پوشه‌ها روی همین رایانه، هر کدام در یک سطر (کپی نمی‌شوند)",
    job_name: "نام کار", job_name_ph: "اختیاری",
    command: "فرمان معادل", command_lead: "همین کار را در ترمینال هم با این فرمان می‌توانید انجام دهید.",
    cancel: "انصراف", start: "شروع", save_run: "ذخیره و اجرا",
    need_input: "یک فایل یا مسیر بدهید.", one_pdf: "برای این کار فقط یک PDF لازم است.", upload_failed: "بارگذاری نشد",
    llm_rules: "قاعده‌های صفحه‌آرایی", llm_rules_desc: "رایگان و بدون اینترنت؛ هیچ چیز از رایانه بیرون نمی‌رود.",
    llm_model: "مدل زبانی",
    llm_model_desc: "بهترین ساختار: مدل تصویر هر صفحه را می‌بیند و فصل‌ها، پانویس‌ها و مشخصات کتاب را تعیین می‌کند. پیش از فرستادن، هزینه را می‌بینید و تأیید می‌کنید.",
    llm_custom: "مدل دیگر…", llm_privacy: "تصویر صفحه‌ها و متن اوسی‌آر آن‌ها به سرویس مدل فرستاده می‌شود.",
    key_found: "کلید {service} پیدا شد.", key_missing: "کلید {service} پیدا نشد.", key_set_here: "در تنظیمات وارد کنید",
    back: "بازگشت به کارها", cancel_job: "لغو", run_again: "اجرای دوباره", resume: "ادامه", edit: "ویرایش گزینه‌ها",
    open_folder: "باز کردن پوشه", delete: "حذف",
    delete_confirm: "این کار و همهٔ فایل‌های پوشه‌اش، از جمله فایل‌های بارگذاری‌شده، حذف شود؟ فایل‌هایی که با مسیر داده شده‌اند دست نمی‌خورند.",
    confirm_title: "تأیید هزینه",
    confirm_text: "اوسی‌آر تمام شد. {todo} صفحه از {pages} صفحه برای {model} به {who} فرستاده می‌شود.",
    cost: "هزینهٔ تقریبی", send: "بفرست", no_llm: "بدون مدل زبانی ادامه بده",
    key_needed: "برای فرستادن، کلید {service} لازم است.",
    stage_pages: "تصویر صفحه‌ها", stage_ocr: "اوسی‌آر", stage_pdf: "PDF جست‌وجوپذیر", stage_notenum: "شمارهٔ پانویس‌ها",
    stage_llm: "مدل زبانی", stage_layout: "صفحه‌آرایی", stage_structure: "ساختار کتاب", stage_epub: "ساختن EPUB", stage_report: "گزارش",
    stage_wait: "منتظر تأیید هزینه", stage_figures: "چرخاندن صفحه‌های تصویر",
    of_pages: "{done} از {total} صفحه", n_pages: "{n} صفحه", eta: "حدود {t} مانده", minutes: "{n} دقیقه", seconds: "{n} ثانیه",
    notenum_kept: "{n} شماره از تصویر خوانده شد", lines: "{n} سطر", queued_note: "در صف؛ پس از کار در حال اجرا شروع می‌شود.",
    tab_book: "کتاب", tab_preview: "پیش‌نمایش", tab_report: "گزارش تبدیل", tab_review: "بازبینی فصل‌ها", tab_files: "فایل‌ها",
    tab_log: "خروجی فرمان", tab_pages: "صفحه‌ها",
    download_epub: "دریافت EPUB", summary: "خلاصهٔ گزارش", report_en: "گزارش به انگلیسی است.",
    review_lead: "همهٔ صفحه‌ها به ترتیب خواندن نشان داده می‌شوند. شروع بخش‌ها، فصل‌ها و زیربخش‌ها و صفحه‌های تصویر را علامت بزنید و «Rebuild EPUB» را بزنید؛ علامت‌ها در همهٔ تبدیل‌های بعدی هم به کار می‌روند. «Done» صفحه را می‌بندد. چند دقیقه برای یک کتاب، و فصل‌ها هر چه باشد اسکن درست درمی‌آیند.",
    review_open: "باز کردن صفحهٔ بازبینی", review_close: "بستن بازبینی", review_starting: "صفحهٔ بازبینی آماده می‌شود…",
    review_hint: "پس از «Rebuild EPUB»، کتاب تازه در زبانه‌های «کتاب» و «پیش‌نمایش» است.", new_tab: "در زبانهٔ تازه",
    review_busy: "تا وقتی کار در حال اجراست، بازبینی باز نمی‌شود.",
    download: "دریافت", download_zip: "دریافت (zip)", all_text: "همهٔ متن در یک فایل",
    file_epub: "کتاب EPUB", file_report: "گزارش تبدیل (Markdown)", file_marks: "علامت‌های بازبینی", file_txt: "متن ساده",
    file_hocr: "hOCR", file_jsonl: "JSONL", file_pdf: "PDF جست‌وجوپذیر", file_pages: "تصویر صفحه‌ها", file_text_all: "متن همهٔ صفحه‌ها",
    n_files: "{n} فایل", files_where: "پوشهٔ این کار:", log_run: "اجرای {n}", log_review: "بازبینی",
    mode_full: "اجرا", mode_estimate: "اوسی‌آر و برآورد هزینه", no_log: "هنوز خروجی‌ای نیست.",
    copy: "کپی", copied: "کپی شد", prev: "قبلی", next: "بعدی", toc: "فهرست", no_text: "متنی برای این صفحه نیست.",
    settings: "تنظیمات", keys_title: "کلیدهای API",
    keys_lead: "فقط برای تعیین ساختار با مدل زبانی لازم است. کلید روی همین رایانه ذخیره می‌شود و فقط حساب شما می‌تواند آن را بخواند.",
    key_env: "از متغیر محیطی {env} خوانده می‌شود", key_file: "ذخیره شده", key_none: "ذخیره نشده",
    key_placeholder: "کلید را اینجا بچسبانید", save: "ذخیره", remove: "حذف", get_key: "گرفتن کلید", saved: "ذخیره شد", removed: "حذف شد",
    device_title: "سخت‌افزار", device_gpu: "GPU: {name}", device_cpu: "GPU پیدا نشد؛ اوسی‌آر با CPU انجام می‌شود (کندتر).",
    device_checking: "در حال بررسی…", home_title: "پوشهٔ کارها", version: "نسخه", quit: "بستن برنامه",
    quit_confirm: "برنامه بسته شود؟ کار در حال اجرا متوقف می‌شود و بعداً می‌توانید آن را ادامه دهید.",
    quit_done: "برنامه بسته شد. این زبانه را می‌توانید ببندید.",
    locked: "ارتباط با برنامه برقرار نیست یا کلید این صفحه کهنه است. نشانی‌ای را که parisaocr app در ترمینال چاپ کرده دوباره باز کنید.",
    see_log: "خروجی فرمان", interrupted_note: "برنامه هنگام اجرای این کار بسته شد. «ادامه» از همان جا ادامه می‌دهد.",
    failed_title: "اجرا ناموفق بود", uploading: "بارگذاری", working: "صبر کنید…", remove_file: "برداشتن",
    kb: "کیلوبایت", mb: "مگابایت", key_remove_confirm: "کلید ذخیره‌شدهٔ {service} از این رایانه حذف شود؟",
  },
  en: {
    app_name: "ParisaOCR", nav_jobs: "Jobs", nav_settings: "Settings", lang_toggle: "فارسی",
    start_title: "What would you like to do?",
    kind_epub: "Scanned book to EPUB",
    kind_epub_desc: "Chapters and contents, linked footnotes, verse, figures and page numbers; with a report and chapter review",
    kind_ocr: "OCR: text and searchable PDF",
    kind_ocr_desc: "Pages (one PDF or images) to plain text, hOCR, JSONL or a searchable PDF",
    kind_pages: "Extract page images", kind_pages_desc: "A PDF's pages as PNG images, no OCR",
    kind_short_epub: "EPUB", kind_short_ocr: "OCR", kind_short_pages: "Pages",
    jobs: "Jobs", no_jobs: "No jobs yet. Pick one of the options above.",
    st_draft: "Draft", st_queued: "Queued", st_running: "Running", st_waiting: "Waiting for your OK", st_done: "Done",
    st_failed: "Failed", st_cancelled: "Cancelled", st_interrupted: "Interrupted",
    new_epub: "Book to EPUB", new_epub_lead: "Give the scanned book's PDF. OCR, page layout and the book's structure run on this computer.",
    new_ocr: "OCR", new_ocr_lead: "Give a PDF or page images and choose the output formats.",
    new_pages: "Extract page images", new_pages_lead: "A PDF's pages as PNG images: the scan inside the PDF, or the page rendered.",
    edit_title: "Edit and run again", input: "Input",
    drop_epub: "Drop the book's PDF here", drop_ocr: "Drop a PDF or page images here", drop_pages: "Drop the PDF here",
    choose_file: "Choose a file", choose_folder: "Choose a folder",
    or_path_one: "or the PDF's path on this computer (not copied)",
    or_path_many: "or paths of files or folders on this computer, one per line (not copied)",
    job_name: "Job name", job_name_ph: "optional",
    command: "Same as this command", command_lead: "You can do the same in a terminal with this command.",
    cancel: "Cancel", start: "Start", save_run: "Save and run",
    need_input: "Give a file or a path.", one_pdf: "This job takes one PDF.", upload_failed: "Upload failed",
    llm_rules: "Layout rules", llm_rules_desc: "Free and offline; nothing leaves this computer.",
    llm_model: "A language model",
    llm_model_desc: "The best structure: the model sees every page and decides the chapters, footnotes and the book's details. You see the cost and agree to it before anything is sent.",
    llm_custom: "Another model…", llm_privacy: "The page images and their OCR text are sent to the model's service.",
    key_found: "{service} key found.", key_missing: "No {service} key found.", key_set_here: "add it in Settings",
    back: "Back to jobs", cancel_job: "Cancel", run_again: "Run again", resume: "Resume", edit: "Edit options",
    open_folder: "Open folder", delete: "Delete",
    delete_confirm: "Delete this job and every file in its folder, uploaded files included? Files given by path are not touched.",
    confirm_title: "Agree to the cost",
    confirm_text: "The OCR is done. {todo} of {pages} pages go to {who} for {model}.",
    cost: "Expected cost", send: "Send", no_llm: "Go on without the language model",
    key_needed: "Sending needs a {service} key.",
    stage_pages: "Page images", stage_ocr: "OCR", stage_pdf: "Searchable PDF", stage_notenum: "Note numbers",
    stage_llm: "Language model", stage_layout: "Page layout", stage_structure: "Book structure", stage_epub: "Writing the EPUB", stage_report: "Report",
    stage_wait: "Waiting for your OK", stage_figures: "Turning figure pages",
    of_pages: "{done} of {total} pages", n_pages: "{n} pages", eta: "about {t} left", minutes: "{n} min", seconds: "{n} s",
    notenum_kept: "{n} numbers read from the image", lines: "{n} lines", queued_note: "Queued: starts after the job that is running.",
    tab_book: "Book", tab_preview: "Preview", tab_report: "Report", tab_review: "Chapter review", tab_files: "Files",
    tab_log: "Command output", tab_pages: "Pages",
    download_epub: "Download EPUB", summary: "Report summary", report_en: "",
    review_lead: "Every page is shown in reading order. Mark where parts, chapters and sections begin, and the figure pages, then press “Rebuild EPUB”; the marks are used by every later conversion too. “Done” closes the page. A few minutes for a book, and the chapters are right whatever the scan.",
    review_open: "Open the review page", review_close: "Close the review", review_starting: "Preparing the review page…",
    review_hint: "After “Rebuild EPUB”, the new book is in the Book and Preview tabs.", new_tab: "In a new tab",
    review_busy: "The review cannot open while the job runs.",
    download: "Download", download_zip: "Download (zip)", all_text: "All text in one file",
    file_epub: "EPUB book", file_report: "Conversion report (Markdown)", file_marks: "Review marks", file_txt: "Plain text",
    file_hocr: "hOCR", file_jsonl: "JSONL", file_pdf: "Searchable PDF", file_pages: "Page images", file_text_all: "Text of all pages",
    n_files: "{n} files", files_where: "This job's folder:", log_run: "Run {n}", log_review: "Review",
    mode_full: "run", mode_estimate: "OCR and cost estimate", no_log: "No output yet.",
    copy: "Copy", copied: "Copied", prev: "Previous", next: "Next", toc: "Contents", no_text: "No text for this page.",
    settings: "Settings", keys_title: "API keys",
    keys_lead: "Needed only when a language model decides the structure. The key is saved on this computer, readable by your account only.",
    key_env: "read from the environment variable {env}", key_file: "saved", key_none: "not saved",
    key_placeholder: "Paste the key here", save: "Save", remove: "Remove", get_key: "Get a key", saved: "Saved", removed: "Removed",
    device_title: "Hardware", device_gpu: "GPU: {name}", device_cpu: "No GPU found; OCR runs on the CPU (slower).",
    device_checking: "Checking…", home_title: "Jobs folder", version: "Version", quit: "Quit the app",
    quit_confirm: "Quit the app? A running job stops; you can resume it later.",
    quit_done: "The app has stopped. You can close this tab.",
    locked: "The app is not reachable, or this page's key is stale. Open the address that parisaocr app printed in the terminal again.",
    see_log: "Command output", interrupted_note: "The app was closed while this job ran. Resume continues where it stopped.",
    failed_title: "The run failed", uploading: "Uploading", working: "Please wait…", remove_file: "Remove",
    kb: "KB", mb: "MB", key_remove_confirm: "Remove the saved {service} key from this computer?",
  },
};

let LANG = "fa";
try { LANG = localStorage.getItem("parisaocr.lang") || "fa"; } catch (e) { /* storage blocked */ }
const S = {info: null, schema: null, timer: null, view: null};

function t(key, vars) {
  let s = T[LANG][key] ?? T.en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split("{" + k + "}").join(v);
  return s;
}
const L = (obj, base) => obj[`${base}_${LANG}`] || obj[`${base}_en`] || "";
const locale = () => (LANG === "fa" ? "fa-IR" : "en-US");
const num = (n, o) => (n == null ? "" : Number(n).toLocaleString(locale(), o));
function fmtDate(v) {
  const d = typeof v === "number" ? new Date(v) : new Date(v);
  return isNaN(d) ? "" : d.toLocaleString(locale(), {dateStyle: "medium", timeStyle: "short"});
}
function fmtSize(b) {
  if (b == null) return "";
  if (b < 1024 * 1024) return num(Math.max(1, Math.round(b / 1024))) + " " + t("kb");
  return num(b / 1048576, {maximumFractionDigits: 1}) + " " + t("mb");
}
function fmtSecs(s) {
  return s < 90 ? t("seconds", {n: num(Math.round(s))}) : t("minutes", {n: num(Math.round(s / 60))});
}
const money = c => "$" + Number(c).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});

function setHTML(el, html) {
  if (el && el.__html !== html) { el.innerHTML = html; el.__html = html; return true; }
  return false;
}
function toast(msg, bad) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(el.__t);
  el.__t = setTimeout(() => (el.className = "toast"), bad ? 6000 : 2200);
}

async function api(path, body) {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-ParisaOCR": "1"}, body: JSON.stringify(body)};
  let r;
  try { r = await fetch(path, opt); } catch (e) { showLocked(); throw e; }
  if (r.status === 403) { showLocked(); throw new Error("locked"); }
  const kind = r.headers.get("Content-Type") || "";
  const data = kind.includes("json") ? await r.json() : await r.text();
  if (!r.ok || (data && data.ok === false && !path.endsWith("/api/command"))) throw new Error((data && data.error) || r.statusText);
  return data;
}
function showLocked() {
  clearInterval(S.timer);
  main.innerHTML = `<div class="alert warn"><p>${esc(t("locked"))}</p></div>`;
}
function poll(fn, ms) {
  clearInterval(S.timer);
  S.timer = setInterval(() => { if (!document.hidden) fn().catch(() => {}); }, ms);
}

const ICON = {
  epub: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/></svg>',
  ocr: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 9h10M7 12h10M7 15h6"/></svg>',
  pages: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/></svg>',
};
const ACCEPT = {epub: ".pdf,application/pdf", pages: ".pdf,application/pdf", ocr: ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.pbm,.pgm,.ppm,application/pdf,image/*"};
const INPUT_RE = /\.(pdf|png|jpe?g|tiff?|bmp|webp|pbm|pgm|ppm)$/i;

const statusPill = s => `<span class="pill s-${esc(s)}">${esc(t("st_" + s))}</span>`;
function bar(st) {
  if (!st || st.total == null || !st.total) return `<div class="bar indet"><i></i></div>`;
  const pct = Math.min(100, Math.round(100 * (st.done || 0) / st.total));
  return `<div class="bar"><i style="width:${pct}%"></i></div>`;
}

// --- shell -------------------------------------------------------------------------------------------------------

function applyLang() {
  document.documentElement.lang = LANG;
  document.documentElement.dir = LANG === "fa" ? "rtl" : "ltr";
  document.title = t("app_name");
  $("#brand").textContent = t("app_name");
  $("#nav_jobs").textContent = t("nav_jobs");
  $("#nav_settings").textContent = t("nav_settings");
  $("#lang").textContent = t("lang_toggle");
}
$("#lang").onclick = () => {
  LANG = LANG === "fa" ? "en" : "fa";
  try { localStorage.setItem("parisaocr.lang", LANG); } catch (e) { /* ignore */ }
  applyLang();
  route();
};
window.addEventListener("hashchange", route);

async function route() {
  clearInterval(S.timer);
  S.timer = null;
  const parts = (location.hash.slice(1) || "/").split("/").filter(Boolean);
  $$(".topnav a").forEach(a => a.classList.toggle("on", a.dataset.nav === (parts[0] === "settings" ? "settings" : "jobs")));
  window.scrollTo(0, 0);
  try {
    if (!parts.length) return await viewLibrary();
    if (parts[0] === "new" && S.schema[parts[1]]) return await viewForm(parts[1], null);
    if (parts[0] === "job" && parts[2] === "edit") return await viewForm(null, parts[1]);
    if (parts[0] === "job" && parts[1]) return await viewJob(parts[1], parts[2] || null);
    if (parts[0] === "settings") return await viewSettings();
  } catch (e) {
    if (e.message !== "locked") main.innerHTML = `<div class="alert bad"><p>${esc(e.message)}</p></div>`;
    return;
  }
  location.hash = "#/";
}

// --- library -----------------------------------------------------------------------------------------------------

async function viewLibrary() {
  const card = k => `<a class="card" href="#/new/${k}"><span class="icon">${ICON[k]}</span><b>${esc(t("kind_" + k))}</b><span>${esc(t("kind_" + k + "_desc"))}</span></a>`;
  main.innerHTML = `<h1>${esc(t("start_title"))}</h1><div class="cards">${card("epub")}${card("ocr")}${card("pages")}</div>
    <h2>${esc(t("jobs"))}</h2><div id="joblist" class="joblist"></div>`;
  const refresh = async () => {
    const jobs = await api("/api/jobs");
    const html = jobs.length ? jobs.map(jobRow).join("") : `<p class="empty">${esc(t("no_jobs"))}</p>`;
    if (setHTML($("#joblist"), html)) {
      $$("#joblist .jobrow").forEach(r => {
        r.onclick = () => (location.hash = "#/job/" + r.dataset.id);
        r.onkeydown = e => { if (e.key === "Enter") r.onclick(); };
      });
    }
  };
  await refresh();
  poll(refresh, 2000);
}

function jobRow(j) {
  let extra = "";
  if (j.status === "running") {
    const st = j.progress;
    extra = bar(st) + `<small>${esc(t("stage_" + (j.stage || "ocr")))}${st && st.total ? " · " + esc(t("of_pages", {done: num(st.done || 0), total: num(st.total)})) : ""}</small>`;
  } else if (j.status === "failed" && j.error) {
    extra = `<small dir="auto">${esc(j.error.slice(0, 90))}</small>`;
  }
  return `<div class="jobrow" data-id="${esc(j.id)}" role="link" tabindex="0">
    <span class="badge k-${esc(j.kind)}">${esc(t("kind_short_" + j.kind))}</span>
    <span class="jt" dir="auto">${esc(j.title)}</span>
    <span class="js">${statusPill(j.status)}${extra}</span>
    <span class="jd">${esc(fmtDate(j.created))}</span></div>`;
}

// --- new job / edit ----------------------------------------------------------------------------------------------

async function viewForm(kind, jobId) {
  let job = null;
  S.info = await api("/api/info");
  if (jobId) { job = await api("/api/jobs/" + jobId); kind = job.kind; }
  const schema = S.schema[kind];
  const F = {kind, job, files: [], removed: new Set(), values: {}};
  for (const sec of schema) for (const f of sec.fields) F.values[f.dest] = f.default;
  if (job) Object.assign(F.values, job.options);
  const many = kind === "ocr";
  main.innerHTML = `
    <h1>${esc(job ? t("edit_title") : t("new_" + kind))}</h1>
    <p class="lead">${esc(t("new_" + kind + "_lead"))}</p>
    <form id="jobform" novalidate>
      <section class="panel"><h2>${esc(t("input"))}</h2>
        <div class="inputs">
          <div id="drop" class="drop">
            <input type="file" id="files" accept="${ACCEPT[kind]}" ${many ? "multiple" : ""} hidden>
            ${many ? '<input type="file" id="folder" webkitdirectory hidden>' : ""}
            <p>${esc(t("drop_" + kind))}</p>
            <div class="row"><button type="button" class="btn" id="pick">${esc(t("choose_file"))}</button>
            ${many ? `<button type="button" class="btn" id="pickdir">${esc(t("choose_folder"))}</button>` : ""}</div>
          </div>
          <ul id="filelist" class="filelist"></ul>
          <label class="field wide"><span>${esc(t(many ? "or_path_many" : "or_path_one"))}</span>
            <textarea id="paths" rows="${many ? 3 : 1}" dir="ltr" spellcheck="false" placeholder="${many ? "/home/me/scans/" : "/home/me/book.pdf"}">${esc((job?.paths || []).join("\n"))}</textarea></label>
          <label class="field"><span>${esc(t("job_name"))}</span><input id="jobname" dir="auto" value="${esc(job?.name || "")}" placeholder="${esc(t("job_name_ph"))}"></label>
        </div>
      </section>
      ${schema.map(sec => sectionHtml(sec, F)).join("")}
      <section class="panel"><h2>${esc(t("command"))}</h2><p class="muted" style="margin-top:0">${esc(t("command_lead"))}</p><pre id="cmd" class="cmdbox" dir="ltr"></pre></section>
      <div class="actions sticky"><span id="formerr" class="err"></span>
        <a class="btn" href="${job ? "#/job/" + esc(job.id) : "#/"}">${esc(t("cancel"))}</a>
        <button class="btn primary" id="go" type="submit">${esc(job ? t("save_run") : t("start"))}</button></div>
    </form>`;

  const form = $("#jobform");
  const renderFiles = () => {
    const existing = (job?.uploads || []).filter(n => !F.removed.has(n)).map(n => ({name: n, old: true}));
    const items = existing.concat(F.files.map((f, i) => ({name: f.name, size: f.size, i})));
    $("#filelist").innerHTML = items.map(it => `<li data-old="${it.old ? esc(it.name) : ""}" data-i="${it.i ?? ""}">
      <span class="name" dir="auto">${esc(it.name)}</span>${it.size != null ? `<span class="muted">${fmtSize(it.size)}</span>` : ""}
      <span class="upl"></span><button type="button" class="btn small ghost" data-rm>${esc(t("remove_file"))}</button></li>`).join("");
    $$("#filelist [data-rm]").forEach(b => b.onclick = () => {
      const li = b.closest("li");
      if (li.dataset.old) F.removed.add(li.dataset.old); else F.files.splice(+li.dataset.i, 1);
      renderFiles(); preview();
    });
  };
  const addFiles = list => {
    let files = [...list].filter(f => INPUT_RE.test(f.name));
    if (!many) { files = files.filter(f => /\.pdf$/i.test(f.name)).slice(0, 1); F.files = []; if (files.length) (job?.uploads || []).forEach(n => F.removed.add(n)); }
    files.sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name, undefined, {numeric: true}));
    F.files.push(...files);
    renderFiles(); preview();
  };
  $("#pick").onclick = () => $("#files").click();
  $("#files").onchange = e => { addFiles(e.target.files); e.target.value = ""; };
  if (many) { $("#pickdir").onclick = () => $("#folder").click(); $("#folder").onchange = e => { addFiles(e.target.files); e.target.value = ""; }; }
  const drop = $("#drop");
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => addFiles(e.dataTransfer.files));

  let timer = null;
  const paths = () => $("#paths").value.split("\n").map(s => s.trim()).filter(Boolean);
  const uploadNames = () => (job?.uploads || []).filter(n => !F.removed.has(n)).concat(F.files.map(f => f.name));
  function preview() {
    readValues(F);
    updateVisibility(F);
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const r = await api("/api/command", {kind, options: F.values, paths: paths(), uploads: uploadNames()});
        $("#cmd").textContent = r.ok ? r.command : r.error;
        $("#cmd").style.color = r.ok ? "" : "var(--danger)";
      } catch (e) { /* shown by api() */ }
    }, 250);
  }
  form.addEventListener("input", preview);
  form.addEventListener("change", preview);
  bindLlm(F, preview);
  renderFiles();
  preview();

  form.onsubmit = async e => {
    e.preventDefault();
    readValues(F);
    const err = $("#formerr");
    const p = paths(), up = uploadNames();
    err.textContent = "";
    if (!p.length && !up.length) return (err.textContent = t("need_input"));
    if (!many && p.length + up.length > 1) return (err.textContent = t("one_pdf"));
    const go = $("#go");
    go.disabled = true;
    go.textContent = t("working");
    try {
      const name = $("#jobname").value.trim();
      if (!F.job) F.job = job = await api("/api/jobs", {kind, name, options: F.values, paths: p});
      else {
        await api(`/api/jobs/${F.job.id}/update`, {name, options: F.values, paths: p});
        for (const n of F.removed) await api(`/api/jobs/${F.job.id}/remove-input`, {name: n});
      }
      const lis = $$("#filelist li").filter(li => li.dataset.i !== "");
      for (const [i, f] of F.files.entries()) {
        const slot = lis[i] && lis[i].querySelector(".upl");
        await upload(F.job.id, f, frac => { if (slot) slot.innerHTML = `<div class="bar" style="width:120px"><i style="width:${Math.round(frac * 100)}%"></i></div>`; });
      }
      F.files = [];
      await api(`/api/jobs/${F.job.id}/start`, {});
      location.hash = "#/job/" + F.job.id;
    } catch (ex) {
      if (ex.message !== "locked") err.textContent = ex.message;
      go.disabled = false;
      go.textContent = job ? t("save_run") : t("start");
    }
  };
}

function upload(jobId, file, onprog) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", `/api/jobs/${encodeURIComponent(jobId)}/upload?name=${encodeURIComponent(file.name)}`);
    x.setRequestHeader("X-ParisaOCR", "1");
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = e => e.lengthComputable && onprog(e.loaded / e.total);
    x.onload = () => {
      let d = {};
      try { d = JSON.parse(x.responseText); } catch (e) { /* not JSON */ }
      if (x.status === 200 && d.ok) resolve(d); else reject(new Error(d.error || t("upload_failed")));
    };
    x.onerror = () => reject(new Error(t("upload_failed")));
    x.send(file);
  });
}

function sectionHtml(sec, F) {
  const closed = ["advanced", "other"].includes(sec.section);
  return `<details class="panel" ${closed ? "" : "open"}><summary><h2>${esc(L(sec, "title"))}</h2></summary>
    <div class="fields">${sec.fields.map(f => fieldHtml(f, F)).join("")}</div></details>`;
}

function fieldHtml(f, F) {
  const v = F.values[f.dest];
  const id = "f_" + f.dest;
  const label = esc(L(f, "label"));
  const desc = L(f, "desc") || (LANG === "en" ? f.help : "");
  const flag = `<span class="flag" dir="ltr" title="${esc(f.help)}">${esc(f.flag)}</span>`;
  const help = desc ? `<small class="desc">${esc(desc)}</small>` : "";
  const needs = f.needs ? ` data-needs="${esc(f.needs)}"` : "";
  const label2 = (txt) => (LANG === "fa" ? txt[0] : txt[1]);
  if (f.widget === "llm") return llmHtml(f, F);
  if (f.widget === "formats") {
    const on = String(v || "").split(",").map(s => s.trim());
    return `<div class="field wide" data-dest="${esc(f.dest)}"${needs}><span>${label} ${flag}</span><div class="choices">
      ${Object.entries(f.choices).map(([c, txt]) => `<label><input type="checkbox" name="${id}" value="${esc(c)}" ${on.includes(c) ? "checked" : ""}>${esc(label2(txt))}</label>`).join("")}
      </div>${help}</div>`;
  }
  if (f.kind === "check") {
    return `<label class="field check" data-dest="${esc(f.dest)}"${needs}><input type="checkbox" id="${id}" ${v ? "checked" : ""}><span>${label} ${flag}</span>${help}</label>`;
  }
  let input;
  if (f.choices) {
    input = `<select id="${id}">${Object.entries(f.choices).map(([c, txt]) => `<option value="${esc(c)}" ${String(v) === c ? "selected" : ""}>${esc(label2(txt))}</option>`).join("")}</select>`;
  } else if (f.kind === "number") {
    input = `<input type="number" id="${id}" dir="ltr" step="${f.step || 1}" value="${v ?? ""}" placeholder="${esc(f.default ?? "")}">`;
  } else {
    const list = f.suggest ? `<datalist id="${id}_l">${f.suggest.map(s => `<option value="${esc(s)}">`).join("")}</datalist>` : "";
    input = `<input id="${id}" dir="auto" spellcheck="false" value="${esc(v ?? "")}" placeholder="${esc(f.default ?? "")}" ${f.suggest ? `list="${id}_l"` : ""}>${list}`;
  }
  return `<label class="field" data-dest="${esc(f.dest)}"${needs}><span>${label} ${flag}</span>${input}${help}</label>`;
}

function llmHtml(f, F) {
  const v = F.values.llm;
  const preset = f.presets.find(p => p.value === v);
  return `<div class="field llm" data-dest="llm"><span>${esc(L(f, "label"))} <span class="flag" dir="ltr" title="${esc(f.help)}">--llm [MODEL]</span></span>
    <label class="radio"><input type="radio" name="llm_on" value="0" ${v ? "" : "checked"}><span><b>${esc(t("llm_rules"))}</b><small>${esc(t("llm_rules_desc"))}</small></span></label>
    <label class="radio"><input type="radio" name="llm_on" value="1" ${v ? "checked" : ""}><span><b>${esc(t("llm_model"))}</b><small>${esc(t("llm_model_desc"))}</small></span></label>
    <div class="llmpick" id="llmpick" ${v ? "" : "hidden"}>
      <select id="llm_preset">${f.presets.map(p => `<option value="${esc(p.value)}" ${p.value === v ? "selected" : ""}>${esc(p[LANG] || p.en)}</option>`).join("")}
        <option value="" ${v && !preset ? "selected" : ""}>${esc(t("llm_custom"))}</option></select>
      <input id="llm_custom" dir="ltr" spellcheck="false" placeholder="openrouter:VENDOR/NAME" value="${v && !preset ? esc(v) : ""}" ${v && !preset ? "" : "hidden"}>
      <p id="llm_key" class="note"></p>
      <p class="note warn">${esc(t("llm_privacy"))}</p>
    </div></div>`;
}

function serviceOf(model) {
  const p = (S.info?.llm_presets || []).find(x => x.value === model);
  return p ? p.key : String(model || "").startsWith("openrouter:") ? "openrouter" : "gemini";
}
const serviceName = s => (s === "openrouter" ? "OpenRouter" : "Gemini");
function hasKey(s) { const k = S.info?.keys?.[s]; return !!(k && (k.env || k.file)); }

function bindLlm(F, changed) {
  if (!$("#llm_preset")) return;
  const sync = () => {
    const on = $("input[name=llm_on]:checked")?.value === "1";
    $("#llmpick").hidden = !on;
    $("#llm_custom").hidden = $("#llm_preset").value !== "";
    const model = on ? ($("#llm_preset").value || $("#llm_custom").value.trim()) : null;
    if (model) {
      const s = serviceOf(model), ok = hasKey(s);
      const el = $("#llm_key");
      el.className = "note " + (ok ? "ok" : "bad");
      el.innerHTML = ok ? esc(t("key_found", {service: serviceName(s)}))
        : `${esc(t("key_missing", {service: serviceName(s)}))} <a href="#/settings" target="_blank">${esc(t("key_set_here"))}</a>`;
    }
  };
  $$("input[name=llm_on]").forEach(r => r.addEventListener("change", sync));
  $("#llm_preset").addEventListener("change", () => {
    const p = (S.info?.llm_presets || []).find(x => x.value === $("#llm_preset").value);
    const jobs = $("#f_llm_jobs");
    if (p && p.jobs && jobs && !jobs.value) jobs.value = p.jobs;
    sync(); changed();
  });
  $("#llm_custom").addEventListener("input", sync);
  sync();
}

function readValues(F) {
  for (const sec of S.schema[F.kind]) for (const f of sec.fields) {
    if (f.widget === "llm") {
      const on = $("input[name=llm_on]:checked")?.value === "1";
      F.values.llm = on ? ($("#llm_preset").value || $("#llm_custom").value.trim() || null) : null;
      continue;
    }
    if (f.widget === "formats") {
      F.values[f.dest] = $$(`input[name=f_${f.dest}]:checked`).map(i => i.value).join(",");
      continue;
    }
    const el = $("#f_" + f.dest);
    if (!el) continue;
    if (f.kind === "check") F.values[f.dest] = el.checked;
    else if (f.kind === "number") F.values[f.dest] = el.value === "" ? null : Number(el.value);
    else F.values[f.dest] = el.value.trim() === "" ? null : el.value.trim();
  }
}

function updateVisibility(F) {
  $$("[data-needs]").forEach(el => {
    const n = el.dataset.needs;
    const show = n === "llm" ? !!F.values.llm : n === "pdf" ? String(F.values.format || "").split(",").includes("pdf") : true;
    el.hidden = !show;
  });
}

// --- one job -----------------------------------------------------------------------------------------------------

async function viewJob(id, wanted) {
  const J = {id, tab: null, tabs: [], sig: null, reviewUrl: undefined, data: null, prevStatus: null, wanted};
  main.innerHTML = `<div id="jh"></div><div id="jalert"></div><div id="jwait"></div><div id="jprog"></div><nav id="jtabs" class="tabs" hidden></nav><div id="jpanel"></div>`;
  J.refresh = async () => {
    let job;
    try { job = await api("/api/jobs/" + encodeURIComponent(id)); } catch (e) { if (/no such job/.test(e.message)) location.hash = "#/"; return; }
    if (!S.info || job.status === "waiting") S.info = await api("/api/info");  // a key saved meanwhile counts
    J.data = job;
    renderHead(J); renderAlert(J); renderWait(J); renderProgress(J);
    const tabs = tabsFor(job);
    const justDone = J.prevStatus && ACTIVE.includes(J.prevStatus) && job.status === "done";
    J.prevStatus = job.status;
    if (tabs.join() !== J.tabs.join()) {
      J.tabs = tabs;
      renderTabs(J);
      if (J.wanted && tabs.includes(J.wanted)) { switchTab(J, J.wanted); J.wanted = null; }
      else if (!tabs.includes(J.tab) || justDone) switchTab(J, tabs[0] || null);
    } else if (justDone && tabs[0] !== J.tab) switchTab(J, tabs[0]);
    const sig = JSON.stringify(job.files.map(f => [f.path, f.mtime, f.size])) + job.status;
    if (sig !== J.sig) {
      const first = J.sig === null;
      J.sig = sig;
      // the page viewer keeps its place while a run adds pages; it is drawn again once the run ends
      if (!first && (["book", "files"].includes(J.tab) || (J.tab === "pages" && !active(job)))) renderPanel(J);
    }
    if (J.tab === "review" && (job.review ? job.review.url || "" : null) !== J.reviewUrl) renderPanel(J);
    if (J.tab === "log" && (active(job) || job.review)) refreshLog(J);
  };
  await J.refresh();
  poll(J.refresh, 1500);
}

function renderHead(J) {
  const job = J.data;
  const act = active(job), busy = act || !!job.review;
  const runLabel = job.status === "draft" ? t("start") : job.status === "done" ? t("run_again") : t("resume");
  const canRun = !busy && job.status !== "waiting" && job.command;
  const html = `<div class="jobhead"><div>
      <a href="#/" class="back">${esc(t("back"))}</a>
      <h1 dir="auto">${esc(job.title)}</h1>
      <div class="meta"><span class="badge k-${esc(job.kind)}">${esc(t("kind_short_" + job.kind))}</span>${statusPill(job.status)}<span class="muted">${esc(fmtDate(job.created))}</span></div></div>
    <div class="btns">
      ${act ? `<button class="btn danger" data-act="cancel">${esc(t("cancel_job"))}</button>` : ""}
      ${canRun ? `<button class="btn primary" data-act="start">${esc(runLabel)}</button>` : ""}
      ${!busy ? `<a class="btn" href="#/job/${esc(job.id)}/edit">${esc(t("edit"))}</a>` : ""}
      <button class="btn" data-act="reveal">${esc(t("open_folder"))}</button>
      ${!busy ? `<button class="btn ghost" data-act="delete">${esc(t("delete"))}</button>` : ""}
    </div></div>
    ${job.command ? `<details class="cmdline"><summary>${esc(t("command"))}</summary><pre dir="ltr">${esc(job.command)}</pre></details>` : ""}`;
  const el = $("#jh");
  const open = el.querySelector("details")?.open;
  if (setHTML(el, html)) {
    if (open && el.querySelector("details")) el.querySelector("details").open = true;
    bindActs(J, el);
  }
}

function bindActs(J, el) {
  $$("[data-act]", el).forEach(b => b.onclick = () => jobAction(J, b.dataset.act, b));
}

async function jobAction(J, act, btn) {
  const id = encodeURIComponent(J.id);
  try {
    if (act === "delete") {
      if (!confirm(t("delete_confirm"))) return;
      await api(`/api/jobs/${id}/delete`, {});
      location.hash = "#/";
      return;
    }
    if (btn) btn.disabled = true;
    if (act === "send") await api(`/api/jobs/${id}/confirm`, {llm: true});
    else if (act === "nollm") await api(`/api/jobs/${id}/confirm`, {llm: false});
    else if (act === "log") { switchTab(J, "log"); return; }
    else await api(`/api/jobs/${id}/${act}`, {});
    await J.refresh();
  } catch (e) {
    if (e.message !== "locked") toast(e.message, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderAlert(J) {
  const job = J.data;
  let html = "";
  if (job.status === "failed" || (job.error && !active(job))) {
    html = `<div class="alert bad"><b>${esc(t("failed_title"))}</b><p dir="auto">${esc(job.error || "")}</p>
      <button class="btn small" data-act="log">${esc(t("see_log"))}</button></div>`;
  } else if (job.status === "interrupted") {
    html = `<div class="alert warn"><p>${esc(t("interrupted_note"))}</p></div>`;
  } else if (job.status === "queued") {
    html = `<div class="alert warn"><p>${esc(t("queued_note"))}</p></div>`;
  }
  if (setHTML($("#jalert"), html)) bindActs(J, $("#jalert"));
}

function renderWait(J) {
  const job = J.data, e = job.estimate;
  let html = "";
  if (job.status === "waiting" && e) {
    const s = serviceOf(e.model), ok = hasKey(s);
    const who = s === "openrouter" ? "OpenRouter" : "Google";
    html = `<div class="panel confirm"><h2>${esc(t("confirm_title"))}</h2>
      <p>${esc(t("confirm_text", {todo: num(e.todo), pages: num(e.pages), who, model: e.model}))}</p>
      <p>${esc(t("cost"))}: <span class="cost" dir="ltr">${e.cost != null ? money(e.cost) : esc(e.text)}</span></p>
      <p class="note warn">${esc(t("llm_privacy"))}</p>
      ${ok ? "" : `<p class="note bad">${esc(t("key_needed", {service: serviceName(s)}))} <a href="#/settings">${esc(t("key_set_here"))}</a></p>`}
      <div class="btns" style="margin-top:12px">
        <button class="btn primary" data-act="send" ${ok ? "" : "disabled"}>${esc(t("send"))}</button>
        <button class="btn" data-act="nollm">${esc(t("no_llm"))}</button>
        <button class="btn ghost" data-act="cancel">${esc(t("cancel_job"))}</button></div></div>`;
  }
  if (setHTML($("#jwait"), html)) bindActs(J, $("#jwait"));
}

function stagesFor(job) {
  const st = job.stages || {};
  const fmts = String(job.options.format || "").split(",");
  const pdfIn = (job.inputs || []).some(p => /\.pdf$/i.test(p));
  let list;
  if (job.kind === "epub") list = ["pages", "ocr", "notenum", ...(job.options.llm || st.llm ? ["llm"] : []), "layout",
                                    ...(st.figures ? ["figures"] : []), "structure", "epub", "report"];
  else if (job.kind === "ocr") list = [...(pdfIn || st.pages ? ["pages"] : []), "ocr", ...(fmts.includes("pdf") ? ["pdf"] : [])];
  else list = ["pages"];
  for (const k of Object.keys(st)) if (!list.includes(k)) list.push(k);
  return list;
}

function stageDetail(name, st) {
  if (!st) return "";
  const parts = [];
  if (st.total != null && ["pages", "ocr", "llm"].includes(name)) {
    parts.push(st.state === "done" && name !== "llm" ? t("n_pages", {n: num(st.total)}) : t("of_pages", {done: num(st.done || 0), total: num(st.total)}));
  }
  if (name === "llm" && st.cost != null) parts.push(money(st.cost));
  if (name === "notenum" && st.kept != null) parts.push(t("notenum_kept", {n: num(st.kept)}));
  if (name === "ocr" && st.lines) parts.push(t("lines", {n: num(st.lines)}));
  if (st.state === "active" && st.total && st.done && st.t && st.t0 && st.done < st.total) {
    const left = (st.t - st.t0) / st.done * (st.total - st.done);
    if (left > 5) parts.push(t("eta", {t: fmtSecs(left)}));
  }
  return parts.join(" · ");
}

function renderProgress(J) {
  const job = J.data;
  const st = job.stages || {};
  if (!Object.keys(st).length && !active(job)) return setHTML($("#jprog"), "");
  const failed = job.status === "failed" || job.status === "cancelled" || job.status === "interrupted";
  const rows = stagesFor(job).map(name => {
    const s = st[name];
    let state = s ? s.state : "pending";
    if (state === "active" && failed) state = "failed";
    if (state === "active" && job.status === "waiting") state = "done";
    const icon = state === "done" ? "✓" : state === "failed" ? "✕" : "";
    const showBar = state === "active" && ["pages", "ocr", "llm", "notenum"].includes(name);
    return `<div class="stage ${state}"><span class="ic">${icon}</span><span>${esc(t("stage_" + name))}</span>
      <span class="what">${showBar ? bar(s) : ""}<small dir="auto">${esc(stageDetail(name, s))}</small></span></div>`;
  });
  if (job.status === "waiting") rows.splice(stagesFor(job).indexOf("ocr") + 1, 0,
    `<div class="stage active"><span class="ic"></span><span>${esc(t("stage_wait"))}</span><span class="what"></span></div>`);
  setHTML($("#jprog"), `<div class="panel"><div class="stages">${rows.join("")}</div></div>`);
}

function tabsFor(job) {
  const kinds = new Set(job.files.map(f => f.kind));
  const tabs = [];
  if (job.kind === "epub") {
    if (kinds.has("epub")) tabs.push("book", "preview");
    if (kinds.has("report")) tabs.push("report");
    if (kinds.has("epub")) tabs.push("review");
  } else if (kinds.has("txt") || kinds.has("jsonl") || kinds.has("pages")) {
    tabs.push("pages");
  }
  if (job.files.length) tabs.push("files");
  if (job.runs.length || job.review_log) tabs.push("log");
  return tabs;
}

function renderTabs(J) {
  const nav = $("#jtabs");
  nav.hidden = !J.tabs.length;
  nav.innerHTML = J.tabs.map(k => `<button type="button" data-tab="${k}" class="${k === J.tab ? "on" : ""}">${esc(t("tab_" + k))}</button>`).join("");
  $$("button", nav).forEach(b => b.onclick = () => switchTab(J, b.dataset.tab));
}

function switchTab(J, tab) {
  J.tab = tab;
  if (tab) history.replaceState(null, "", `#/job/${encodeURIComponent(J.id)}/${tab}`);  // a link to this tab
  $$("#jtabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  renderPanel(J);
}

function renderPanel(J) {
  const el = $("#jpanel");
  el.__html = null;
  const f = {book: panelBook, preview: panelPreview, report: panelReport, review: panelReview, files: panelFiles, log: panelLog, pages: panelPages}[J.tab];
  if (!f) { el.innerHTML = ""; return; }
  Promise.resolve(f(J, el)).catch(e => { if (e.message !== "locked") el.innerHTML = `<div class="alert bad"><p>${esc(e.message)}</p></div>`; });
}

const fileUrl = (J, f, dl) => `/api/jobs/${encodeURIComponent(J.id)}/file/${encPath(f.path)}${dl ? "?download=1" : ""}`;
async function fetchText(url) { const r = await fetch(url); if (r.status === 403) { showLocked(); throw new Error("locked"); } return r.text(); }

async function panelBook(J, el) {
  const f = J.data.files.find(x => x.kind === "epub");
  const r = J.data.files.find(x => x.kind === "report");
  el.innerHTML = `<div class="panel bookcard"><div><h2 dir="auto">${esc(J.data.result?.title || J.data.title)}</h2>
      <p class="muted">${esc(fmtSize(f.size))} · ${esc(fmtDate(f.mtime * 1000))}</p></div>
      <a class="btn primary big" href="${fileUrl(J, f, true)}">${esc(t("download_epub"))}</a></div><div id="bsum"></div>`;
  if (!r) return;
  const text = await fetchText(fileUrl(J, r));
  const keep = ["Pages", "Structure", "Footnotes"];
  const parts = [];
  let cur = null;
  for (const line of text.split("\n")) {
    const h = line.match(/^## (.+)/);
    if (h) { cur = keep.includes(h[1].trim()) ? {title: h[1].trim(), items: []} : null; if (cur) parts.push(cur); continue; }
    if (cur && /^- /.test(line) && cur.items.length < 4) cur.items.push(line.slice(2));
  }
  if (parts.length) {
    $("#bsum", el).innerHTML = `<div class="panel summary report"><h2>${esc(t("summary"))}</h2>${LANG === "fa" ? `<p class="muted" dir="rtl" style="font-family:var(--font)">${esc(t("report_en"))}</p>` : ""}
      ${parts.map(p => `<h3>${esc(p.title)}</h3><ul>${p.items.map(i => `<li dir="auto">${mdInline(i)}</li>`).join("")}</ul>`).join("")}</div>`;
  }
}

async function panelPreview(J, el) {
  const d = await api(`/api/jobs/${encodeURIComponent(J.id)}/epub-toc`);
  el.innerHTML = `<div class="preview"><nav class="toc" dir="rtl">${d.toc.map((x, i) => `<a href="#" data-i="${i}" style="padding-inline-start:${8 + x.depth * 16}px">${esc(x.title || "…")}</a>`).join("")}</nav>
    <div class="reader"><div class="readerbar"><button class="btn small" id="pv_prev" type="button">${esc(t("prev"))}</button>
    <span id="pv_where" class="muted" dir="auto"></span><button class="btn small" id="pv_next" type="button">${esc(t("next"))}</button></div>
    <iframe id="pv" title="EPUB" sandbox="allow-same-origin"></iframe></div></div>`;
  const base = `/api/jobs/${encodeURIComponent(J.id)}/epub/`;
  let idx = 0;
  const show = href => {
    const [file, frag] = href.split("#");
    idx = Math.max(0, d.spine.indexOf(file));
    $("#pv", el).src = base + encPath(file) + (frag ? "#" + frag : "");
    const hit = d.toc.findIndex(x => x.href.split("#")[0] === file);
    $$(".toc a", el).forEach((a, i) => a.classList.toggle("on", i === hit));
    $("#pv_where", el).textContent = hit >= 0 ? d.toc[hit].title : `${num(idx + 1)} / ${num(d.spine.length)}`;
  };
  $$(".toc a", el).forEach(a => a.onclick = e => { e.preventDefault(); show(d.toc[+a.dataset.i].href); });
  $("#pv_prev", el).onclick = () => idx > 0 && show(d.spine[idx - 1]);
  $("#pv_next", el).onclick = () => idx + 1 < d.spine.length && show(d.spine[idx + 1]);
  const first = d.spine.find(s => !/cover/.test(s)) || d.spine[0] || (d.toc[0] && d.toc[0].href);
  if (first) show(first);
}

async function panelReport(J, el) {
  const r = J.data.files.find(x => x.kind === "report");
  const text = await fetchText(fileUrl(J, r));
  el.innerHTML = `<div class="panel report">${md(text)}</div>`;
}

function panelReview(J, el) {
  const rv = J.data.review;
  J.reviewUrl = rv ? rv.url || "" : null;
  if (!rv) {
    const busy = active(J.data);
    el.innerHTML = `<div class="panel"><p>${esc(t("review_lead"))}</p>
      ${busy ? `<p class="note warn">${esc(t("review_busy"))}</p>` : `<button class="btn primary" data-act="review">${esc(t("review_open"))}</button>`}</div>`;
  } else if (!rv.url) {
    el.innerHTML = `<div class="panel"><p class="muted"><span class="spin"></span>${esc(t("review_starting"))}</p></div>`;
  } else {
    el.innerHTML = `<div class="reviewbar"><span class="muted">${esc(t("review_hint"))}</span>
      <a class="btn ghost" href="${esc(rv.url)}" target="_blank" rel="noopener">${esc(t("new_tab"))}</a>
      <button class="btn" data-act="review-close">${esc(t("review_close"))}</button></div>
      <iframe class="reviewframe" src="${esc(rv.url)}" title="review"></iframe>`;
  }
  bindActs(J, el);
}

function panelFiles(J, el) {
  const id = encodeURIComponent(J.id);
  const rows = J.data.files.map(f => {
    const dir = f.size == null;
    const href = dir ? `/api/jobs/${id}/zip/${encPath(f.path)}` : fileUrl(J, f, true);
    return `<tr><td>${esc(t("file_" + f.kind))}</td><td class="mono" dir="ltr">${esc(f.path)}</td>
      <td>${dir ? esc(t("n_files", {n: num(f.count)})) : esc(fmtSize(f.size))}</td>
      <td><a class="btn small" href="${href}">${esc(dir ? t("download_zip") : t("download"))}</a></td></tr>`;
  });
  if (J.data.kind === "ocr" && J.data.files.some(f => f.kind === "txt" || f.kind === "jsonl")) {
    rows.push(`<tr><td>${esc(t("file_text_all"))}</td><td class="mono" dir="ltr">.txt</td><td></td>
      <td><a class="btn small" href="/api/jobs/${id}/text-all">${esc(t("download"))}</a></td></tr>`);
  }
  el.innerHTML = `<div class="panel"><table class="files">${rows.join("")}</table>
    <p class="muted">${esc(t("files_where"))} <code dir="ltr">${esc(J.data.dir)}</code></p>
    <button class="btn" data-act="reveal">${esc(t("open_folder"))}</button></div>`;
  bindActs(J, el);
}

async function panelLog(J, el) {
  const runs = J.data.runs;
  el.innerHTML = `<div class="panel"><select id="logsel" style="width:auto">
      ${runs.map((r, i) => `<option value="${i + 1}" ${i === runs.length - 1 ? "selected" : ""}>${esc(t("log_run", {n: num(i + 1)}))} · ${esc(t("mode_" + r.mode))} · ${esc(fmtDate(r.started))}</option>`).join("")}
      ${J.data.review_log ? `<option value="review" ${runs.length ? "" : "selected"}>${esc(t("log_review"))}</option>` : ""}
    </select><pre id="logtext" class="log" dir="ltr"></pre></div>`;
  $("#logsel", el).onchange = () => refreshLog(J, true);
  await refreshLog(J, true);
}

async function refreshLog(J, force) {
  const sel = $("#logsel"), pre = $("#logtext");
  if (!sel || !pre) return;
  const v = sel.value;
  const last = String(J.data.runs.length);
  if (!force && v !== last && v !== "review") return;
  const q = v === "review" ? "name=review" : "run=" + v;
  const d = await api(`/api/jobs/${encodeURIComponent(J.id)}/log?${q}`);
  const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
  if (pre.textContent !== d.text) {
    pre.textContent = d.text || t("no_log");
    if (atEnd || force) pre.scrollTop = pre.scrollHeight;
  }
}

async function panelPages(J, el) {
  const id = encodeURIComponent(J.id);
  const pages = await api(`/api/jobs/${id}/pages`);
  const img = (pid, w) => `/api/jobs/${id}/page/${encodeURIComponent(pid)}.jpg?w=${w}`;
  if (J.data.kind === "pages") {
    el.innerHTML = `<div class="thumbs">${pages.map(p => `<figure data-pid="${esc(p.pid)}"><img loading="lazy" src="${img(p.pid, 300)}" alt=""><figcaption>${esc(p.pid)}</figcaption></figure>`).join("")}</div>`;
    $$("figure", el).forEach(f => f.onclick = () => zoom(img(f.dataset.pid, 1600)));
    return;
  }
  el.innerHTML = `<div class="viewer"><nav class="plist">${pages.map((p, i) => `<a href="#" data-i="${i}">${esc(p.pid)}</a>`).join("")}</nav>
    <div><div class="pbar"><button class="btn small" id="pg_prev" type="button">${esc(t("prev"))}</button><b id="pg_id"></b>
      <button class="btn small" id="pg_next" type="button">${esc(t("next"))}</button><span style="flex:1"></span>
      <button class="btn small" id="pg_copy" type="button">${esc(t("copy"))}</button></div>
    <div class="ppair"><div class="pimg"><img id="pg_img" alt=""></div><textarea id="pg_text" dir="rtl" wrap="off" readonly></textarea></div></div></div>`;
  let cur = 0;
  const show = async i => {
    if (!pages.length) return;
    cur = Math.max(0, Math.min(pages.length - 1, i));
    const p = pages[cur];
    $$(".plist a", el).forEach((a, k) => a.classList.toggle("on", k === cur));
    $(".plist a.on", el)?.scrollIntoView({block: "nearest"});
    $("#pg_id", el).textContent = p.pid;
    $("#pg_img", el).src = p.image ? img(p.pid, 1100) : "";
    const d = await api(`/api/jobs/${id}/text/${encodeURIComponent(p.pid)}`);
    $("#pg_text", el).value = d.text ?? t("no_text");
  };
  $$(".plist a", el).forEach(a => a.onclick = e => { e.preventDefault(); show(+a.dataset.i); });
  $("#pg_prev", el).onclick = () => show(cur - 1);
  $("#pg_next", el).onclick = () => show(cur + 1);
  $("#pg_copy", el).onclick = async () => {
    try { await navigator.clipboard.writeText($("#pg_text", el).value); toast(t("copied")); } catch (e) { $("#pg_text", el).select(); }
  };
  $("#pg_img", el).onclick = () => pages[cur] && pages[cur].image && zoom(img(pages[cur].pid, 2000));
  show(0);
}

function zoom(src) {
  const z = document.createElement("div");
  z.className = "zoom";
  z.innerHTML = `<img src="${esc(src)}" alt="">`;
  z.onclick = () => z.remove();
  document.body.appendChild(z);
}

// --- settings ----------------------------------------------------------------------------------------------------

async function viewSettings() {
  S.info = await api("/api/info");
  const render = () => {
    const k = S.info.keys, dev = S.info.device;
    const keyRow = s => {
      const st = k[s];
      const status = st.env ? t("key_env", {env: st.env_name}) : st.file ? t("key_file") : t("key_none");
      return `<div class="keyrow"><div class="row"><b>${esc(serviceName(s))}</b><span class="pill ${st.env || st.file ? "s-done" : ""}">${esc(status)}</span></div>
        <div class="row"><input type="password" id="key_${s}" dir="ltr" autocomplete="off" spellcheck="false" placeholder="${esc(t("key_placeholder"))}">
          <button class="btn primary" type="button" data-save="${s}">${esc(t("save"))}</button>
          ${st.file ? `<button class="btn ghost" type="button" data-remove="${s}">${esc(t("remove"))}</button>` : ""}
          <a href="${esc(st.url)}" target="_blank" rel="noopener">${esc(t("get_key"))}</a></div>
        <small class="muted" dir="ltr">${esc(st.file_path)}</small></div>`;
    };
    main.innerHTML = `<h1>${esc(t("settings"))}</h1>
      <section class="panel"><h2>${esc(t("keys_title"))}</h2><p class="muted">${esc(t("keys_lead"))}</p>${keyRow("gemini")}${keyRow("openrouter")}</section>
      <section class="panel"><h2>${esc(t("device_title"))}</h2><p>${dev == null ? `<span class="spin"></span>${esc(t("device_checking"))}`
        : dev.cuda ? esc(t("device_gpu", {name: dev.name})) : esc(t("device_cpu"))}</p></section>
      <section class="panel"><h2>${esc(t("home_title"))}</h2><p><code dir="ltr">${esc(S.info.home)}</code></p></section>
      <section class="panel"><h2>${esc(t("version"))}</h2><p dir="ltr" style="text-align:start">ParisaOCR ${esc(S.info.version)} ·
        <a href="https://github.com/givia/ParisaOCR" target="_blank" rel="noopener">GitHub</a></p></section>
      <div class="actions" style="justify-content:flex-start"><button class="btn danger" id="quit" type="button">${esc(t("quit"))}</button></div>`;
    $$("[data-save]").forEach(b => b.onclick = async () => {
      const s = b.dataset.save, v = $("#key_" + s).value.trim();
      if (!v) return;
      try { S.info.keys = (await api("/api/keys", {service: s, key: v})).keys; toast(t("saved")); render(); } catch (e) { toast(e.message, true); }
    });
    $$("[data-remove]").forEach(b => b.onclick = async () => {
      if (!confirm(t("key_remove_confirm", {service: serviceName(b.dataset.remove)}))) return;
      try { S.info.keys = (await api("/api/keys", {service: b.dataset.remove, key: ""})).keys; toast(t("removed")); render(); } catch (e) { toast(e.message, true); }
    });
    $("#quit").onclick = async () => {
      if (!confirm(t("quit_confirm"))) return;
      try { await api("/api/quit", {}); } catch (e) { /* the server is going */ }
      clearInterval(S.timer);
      main.innerHTML = `<div class="alert warn"><p>${esc(t("quit_done"))}</p></div>`;
    };
  };
  render();
  if (S.info.device == null) {
    poll(async () => {
      const info = await api("/api/info");
      if (info.device != null) { S.info.device = info.device; clearInterval(S.timer); render(); }
    }, 2000);
  }
}

// --- the conversion report (Markdown) ----------------------------------------------------------------------------

function mdInline(s) {
  return esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
}
function md(src) {
  const lines = src.split("\n"), out = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    const h = l.match(/^(#{1,4}) (.*)/);
    if (h) { const n = h[1].length + 1; out.push(`<h${n} dir="auto">${mdInline(h[2])}</h${n}>`); i++; continue; }
    if (/^\s*\|/.test(l)) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      const body = rows.filter(r => !/^\s*\|?\s*:?-{2,}/.test(r));
      out.push(`<table>${body.map((r, k) => `<tr>${cells(r).map(c => k === 0 ? `<th dir="auto">${mdInline(c)}</th>` : `<td dir="auto">${mdInline(c)}</td>`).join("")}</tr>`).join("")}</table>`);
      continue;
    }
    if (/^\s*- /.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*- /.test(lines[i])) items.push(lines[i++]);
      out.push(`<ul>${items.map(it => {
        const depth = Math.floor(it.match(/^\s*/)[0].length / 2);
        return `<li dir="auto" style="margin-inline-start:${depth * 1.4}em">${mdInline(it.replace(/^\s*- /, ""))}</li>`;
      }).join("")}</ul>`);
      continue;
    }
    if (l.trim()) {
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4} |\s*\||\s*- )/.test(lines[i])) para.push(lines[i++]);
      out.push(`<p dir="auto">${mdInline(para.join(" "))}</p>`);
      continue;
    }
    i++;
  }
  return out.join("\n");
}

// --- start -------------------------------------------------------------------------------------------------------

(async () => {
  applyLang();
  try {
    [S.info, S.schema] = await Promise.all([api("/api/info"), api("/api/schema")]);
  } catch (e) { return; }
  route();
})();
