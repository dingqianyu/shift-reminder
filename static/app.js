const $ = s => document.querySelector(s);
let month = new Date();
month.setDate(1);
let shifts = {};
let pickedDay = null;
let filterShift = null;
const holidayCache = {};
const lunarFormatter = new Intl.DateTimeFormat("zh-CN-u-ca-chinese", {month: "long", day: "numeric"});

const editor = $("#editor");
const picker = $("#shift-picker");

editor.value = localStorage.editor || "";
editor.oninput = () => localStorage.editor = editor.value;

async function api(path, opt = {}) {
  const r = await fetch(path, {headers: {"Content-Type": "application/json"}, ...opt});
  const data = await r.json();
  if (r.status === 401) {
    $("#login").classList.remove("hidden");
    throw Error("请登录");
  }
  if (!r.ok) throw Error(data.error || "请求失败");
  return data;
}

function toast(t) {
  const e = $("#toast");
  e.textContent = t;
  e.classList.add("show");
  setTimeout(() => e.classList.remove("show"), 1800);
}

function escapeHtml(s = "") {
  return s.replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

$("#login-form").onsubmit = async e => {
  e.preventDefault();
  try {
    await api("/api/login", {method: "POST", body: JSON.stringify({password: $("#password").value})});
    $("#login").classList.add("hidden");
    loadAll();
  } catch (x) {
    $("#login-error").textContent = x.message;
  }
};

document.querySelectorAll(".brush").forEach(b => {
  b.onclick = () => {
    const shift = b.dataset.brush;
    filterShift = filterShift === shift ? null : shift;
    document.querySelectorAll(".brush").forEach(x => x.classList.toggle("active", x.dataset.brush === filterShift));
    $("#mode-hint").textContent = filterShift
      ? `正在查看${filterShift} · 其他日期已暗下去，再点一次取消`
      : "点击日期后选择班次 · 支持早班、白班、夜班、转班";
    render();
  };
});

$("#prev").onclick = () => {
  hidePicker();
  month.setMonth(month.getMonth() - 1);
  loadShifts();
};

$("#next").onclick = () => {
  hidePicker();
  month.setMonth(month.getMonth() + 1);
  loadShifts();
};

function key(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function addHoliday(map, date, name) {
  map[key(date)] = name;
}

function qingmingDay(year) {
  const y = year % 100;
  return Math.floor(y * 0.2422 + 4.81) - Math.floor(y / 4);
}

function getLegalHolidays(year) {
  if (holidayCache[year]) return holidayCache[year];
  const map = {};

  addHoliday(map, new Date(year, 0, 1), "元旦");
  addHoliday(map, new Date(year, 3, qingmingDay(year)), "清明");
  addHoliday(map, new Date(year, 4, 1), "劳动");
  addHoliday(map, new Date(year, 4, 2), "劳动");
  addHoliday(map, new Date(year, 9, 1), "国庆");
  addHoliday(map, new Date(year, 9, 2), "国庆");
  addHoliday(map, new Date(year, 9, 3), "国庆");

  for (let d = new Date(year, 0, 1); d.getFullYear() === year; d.setDate(d.getDate() + 1)) {
    const lunar = lunarFormatter.format(d);
    if (lunar === "正月1日") {
      const eve = new Date(d);
      eve.setDate(eve.getDate() - 1);
      if (eve.getFullYear() === year) addHoliday(map, eve, "除夕");
      addHoliday(map, d, "春节");
    } else if (lunar === "正月2日" || lunar === "正月3日") {
      addHoliday(map, d, "春节");
    } else if (lunar === "五月5日") {
      addHoliday(map, d, "端午");
    } else if (lunar === "八月15日") {
      addHoliday(map, d, "中秋");
    }
  }

  holidayCache[year] = map;
  return map;
}

function render() {
  const cal = $("#calendar");
  cal.innerHTML = "";
  $("#month-title").textContent = `${month.getFullYear()} 年 ${month.getMonth() + 1} 月`;
  const legalHolidays = getLegalHolidays(month.getFullYear());

  const offset = (new Date(month.getFullYear(), month.getMonth(), 1).getDay() + 6) % 7;
  const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();

  for (let i = 0; i < offset; i++) {
    const e = document.createElement("i");
    e.className = "day-cell empty";
    cal.append(e);
  }

  for (let n = 1; n <= days; n++) {
    const d = new Date(month.getFullYear(), month.getMonth(), n);
    const k = key(d);
    const s = shifts[k];
    const holiday = legalHolidays[k];
    const b = document.createElement("button");
    const dimmed = filterShift && s?.shift !== filterShift ? "dimmed" : "";
    b.className = `day-cell ${s?.shift || ""} ${holiday ? "legal-holiday" : ""} ${k === key(new Date()) ? "today" : ""} ${dimmed}`;
    b.title = holiday ? `${k} · 法定节假日：${holiday}` : k;
    b.innerHTML = `<span class="date-number">${n}</span>${holiday ? `<span class="holiday-tag">${holiday}</span>` : ""}`;
    b.onclick = e => openPicker(e, k, s);
    cal.append(b);
  }
}

function openPicker(e, day, s) {
  pickedDay = day;

  $("#picker-day").textContent = day;
  $("#picker-current").textContent = s
    ? `当前：${s.shift} · ${s.updated_by} 于 ${s.updated_at.replace("T", " ")} 修改`
    : "当前：暂未排班";
  $("#selected-info").textContent = s
    ? `${day} · ${s.shift} · ${s.updated_by} 于 ${s.updated_at.replace("T", " ")} 修改`
    : `${day} · 暂未排班`;

  picker.hidden = false;
  const rect = e.currentTarget.getBoundingClientRect();
  const top = Math.min(window.innerHeight - picker.offsetHeight - 14, rect.bottom + 10);
  const left = Math.min(window.innerWidth - picker.offsetWidth - 14, Math.max(14, rect.left + rect.width / 2 - picker.offsetWidth / 2));
  picker.style.top = `${Math.max(14, top)}px`;
  picker.style.left = `${left}px`;
}

function hidePicker() {
  picker.hidden = true;
  pickedDay = null;
}

async function setShift(shift) {
  if (!pickedDay) return;
  if (!editor.value.trim()) {
    toast("请先填写你的名字");
    editor.focus();
    return;
  }

  try {
    if (shift === "清除") {
      await api(`/api/shift?day=${pickedDay}&editor=${encodeURIComponent(editor.value)}`, {method: "DELETE"});
      toast("已清除排班");
    } else {
      await api("/api/shift", {method: "POST", body: JSON.stringify({day: pickedDay, shift, editor: editor.value})});
      toast(`已设为${shift}`);
    }
    hidePicker();
    await loadShifts();
    await loadAudit();
  } catch (e) {
    toast(e.message);
  }
}

document.querySelectorAll("[data-pick]").forEach(b => {
  b.onclick = () => setShift(b.dataset.pick);
});

$("#picker-close").onclick = hidePicker;
document.addEventListener("keydown", e => {
  if (e.key === "Escape") hidePicker();
});
document.addEventListener("click", e => {
  if (picker.hidden || picker.contains(e.target) || e.target.closest(".day-cell")) return;
  hidePicker();
});

async function loadShifts() {
  const m = `${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, "0")}`;
  const rows = await api(`/api/shifts?month=${m}`);
  shifts = Object.fromEntries(rows.map(x => [x.day, x]));
  render();
}

async function loadSettings() {
  const s = await api("/api/settings");
  $("#times").value = s.reminder_times || "18:00";
  $("#send-key").placeholder = s.server_chan_key ? "••••••••（已保存，输入新值可替换）" : "粘贴 SendKey";
  const status = $("#settings-status");
  status.textContent = s.server_chan_key ? "✓ SendKey 已安全保存，刷新后不会显示明文" : "尚未保存 SendKey";
  status.classList.toggle("saved", !!s.server_chan_key);
}

async function loadAudit() {
  const rows = await api("/api/audit");
  $("#audit").innerHTML = rows.map(x => `<div class="audit-item"><b>${escapeHtml(x.editor)}</b> ${x.action === "delete" ? "清除了" : `设为 ${x.shift}`} ${x.day}<br><span>${x.created_at.replace("T", " ")}</span></div>`).join("") || "<span>暂无修改</span>";
}

$("#save-times").onclick = async () => {
  try {
    await api("/api/settings", {method: "POST", body: JSON.stringify({reminder_times: $("#times").value, server_chan_key: ""})});
    await loadSettings();
    toast("提醒时间已保存");
  } catch (e) {
    $("#settings-status").classList.remove("saved");
    $("#settings-status").textContent = e.message;
  }
};

$("#save-settings").onclick = async () => {
  try {
    await api("/api/settings", {method: "POST", body: JSON.stringify({reminder_times: $("#times").value, server_chan_key: $("#send-key").value})});
    $("#send-key").value = "";
    await loadSettings();
    toast("提醒设置已保存");
  } catch (e) {
    $("#settings-status").classList.remove("saved");
    $("#settings-status").textContent = e.message;
  }
};

$("#test-push").onclick = async () => {
  try {
    await api("/api/test-push", {method: "POST", body: JSON.stringify({server_chan_key: $("#send-key").value})});
    toast("测试消息已发送");
  } catch (e) {
    toast(e.message);
  }
};

async function loadAll() {
  await Promise.all([loadShifts(), loadSettings(), loadAudit()]);
}

(async () => {
  try {
    const s = await api("/api/session");
    if (s.authenticated) {
      $("#login").classList.add("hidden");
      loadAll();
    }
  } catch {}
})();

setInterval(() => {
  if ($("#login").classList.contains("hidden")) {
    loadShifts();
    loadAudit();
  }
}, 10000);
