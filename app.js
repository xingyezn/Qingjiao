const categories = {
  "natural-science": "自然科学",
  "social-science": "社会科学",
  education: "教育",
  postdoctoral: "博士后"
};

const statusLabels = {
  open: "申报中",
  upcoming: "即将开放",
  announced: "已发布通知",
  closed: "已截止",
  reference: "常设项目 / 待核验批次"
};

let regions = [];
let projects = [];
let projectRecords = [];
let preferredRegion = "";
const pageSize = 12;
let visibleProjectCount = pageSize;
let visibleActiveCount = 6;
let sourceDirectory = [];
let visibleSourceCount = 18;

const provinceCodes = {
  BJ: "北京市", TJ: "天津市", HE: "河北省", SX: "山西省", NM: "内蒙古自治区",
  LN: "辽宁省", JL: "吉林省", HL: "黑龙江省", SH: "上海市", JS: "江苏省",
  ZJ: "浙江省", AH: "安徽省", FJ: "福建省", JX: "江西省", SD: "山东省",
  HA: "河南省", HB: "湖北省", HN: "湖南省", GD: "广东省", GX: "广西壮族自治区",
  HI: "海南省", CQ: "重庆市", SC: "四川省", GZ: "贵州省", YN: "云南省",
  XZ: "西藏自治区", SN: "陕西省", GS: "甘肃省", QH: "青海省",
  NX: "宁夏回族自治区", XJ: "新疆维吾尔自治区",
  11: "北京市", 12: "天津市", 13: "河北省", 14: "山西省", 15: "内蒙古自治区",
  21: "辽宁省", 22: "吉林省", 23: "黑龙江省", 31: "上海市", 32: "江苏省",
  33: "浙江省", 34: "安徽省", 35: "福建省", 36: "江西省", 37: "山东省",
  41: "河南省", 42: "湖北省", 43: "湖南省", 44: "广东省", 45: "广西壮族自治区",
  46: "海南省", 50: "重庆市", 51: "四川省", 52: "贵州省", 53: "云南省",
  54: "西藏自治区", 61: "陕西省", 62: "甘肃省", 63: "青海省",
  64: "宁夏回族自治区", 65: "新疆维吾尔自治区"
};
const provinceNames = {
  beijing: "北京市", tianjin: "天津市", hebei: "河北省", shanxi: "山西省",
  innermongolia: "内蒙古自治区", neimenggu: "内蒙古自治区", liaoning: "辽宁省",
  jilin: "吉林省", heilongjiang: "黑龙江省", shanghai: "上海市",
  jiangsu: "江苏省", zhejiang: "浙江省", anhui: "安徽省", fujian: "福建省",
  jiangxi: "江西省", shandong: "山东省", henan: "河南省", hubei: "湖北省",
  hunan: "湖南省", guangdong: "广东省", guangxi: "广西壮族自治区",
  hainan: "海南省", chongqing: "重庆市", sichuan: "四川省", guizhou: "贵州省",
  yunnan: "云南省", tibet: "西藏自治区", xizang: "西藏自治区",
  shaanxi: "陕西省", gansu: "甘肃省", qinghai: "青海省",
  ningxia: "宁夏回族自治区", xinjiang: "新疆维吾尔自治区"
};

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[char]));

function formatDate(value) {
  if (!value) return "未注明";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeZone: "Asia/Shanghai" }).format(date);
}

function populateOptions(availableRegions) {
  regions = ["国家级", ...availableRegions.filter((region) => region !== "国家级").sort((a, b) => a.localeCompare(b, "zh-CN"))];
  const regionSelect = document.querySelector("#region");
  regions.forEach((region) => regionSelect.add(new Option(region, region)));
  const preferredSelect = document.querySelector("#preferredRegion");
  regions.filter((region) => region !== "国家级").forEach((region) => preferredSelect.add(new Option(region, region)));
  const categorySelect = document.querySelector("#category");
  Object.entries(categories).forEach(([value, label]) => categorySelect.add(new Option(label, value)));
}

function provinceFromIp(data) {
  if (!data || data.success === false || data.error || data.country_code !== "CN") return "";
  const code = String(data.region_code || "").toUpperCase().replace(/^CN-/, "");
  const name = String(data.region || "").toLowerCase().replace(/[^a-z\u4e00-\u9fff]/g, "");
  const found = provinceCodes[code] || provinceNames[name] || (name ? regions.find((region) => region.startsWith(name)) : "");
  return regions.includes(found) ? found : "";
}

function regionRank(project) {
  if (preferredRegion && project.region === preferredRegion) return 0;
  if (project.region === "国家级") return 1;
  return 2;
}

function setPreferredRegion(region, method) {
  preferredRegion = regions.includes(region) && region !== "国家级" ? region : "";
  document.querySelector("#preferredRegion").value = preferredRegion;
  document.querySelector("#locationHint").textContent = preferredRegion
    ? `${method === "auto" ? "根据 IP 推测：" : "手动选择："}${preferredRegion}，优先显示该地区及国家级项目。`
    : "国家级项目优先；可选择省份。";
  try {
    localStorage.setItem("qjzt-region-v1", JSON.stringify({ region: preferredRegion, method, expires: method === "auto" ? Date.now() + 86400000 : null }));
  } catch { /* Storage can be unavailable in private browsing. */ }
  visibleProjectCount = pageSize;
  visibleActiveCount = 6;
  renderActiveProjects();
  renderProjects();
}

async function lookupIpRegion(endpoint) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 4500);
  try {
    const response = await fetch(endpoint, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error(`IP lookup HTTP ${response.status}`);
    const data = await response.json();
    if (data.success === false || data.error) throw new Error("IP lookup returned an error");
    return data;
  } finally {
    clearTimeout(timeout);
  }
}

async function detectRegion() {
  const hint = document.querySelector("#locationHint");
  hint.textContent = "正在根据 IP 推测省份…";
  for (const endpoint of ["https://ipwho.is/", "https://ipapi.co/json/"]) {
    try {
      const region = provinceFromIp(await lookupIpRegion(endpoint));
      setPreferredRegion(region, "auto");
      if (!region) hint.textContent = "未识别到中国省份，国家级项目优先；可手动选择。";
      return;
    } catch { /* Try the next provider. */ }
  }
  hint.textContent = preferredRegion
    ? `定位暂不可用，继续优先显示${preferredRegion}和国家级项目。`
    : "定位暂不可用，国家级项目优先；可手动选择。";
}

function restoreRegionPreference() {
  try {
    const saved = JSON.parse(localStorage.getItem("qjzt-region-v1") || "null");
    if (saved && (saved.method === "manual" || (saved.method === "auto" && saved.expires > Date.now()))) {
      setPreferredRegion(saved.region, saved.method);
      return;
    }
  } catch { /* Use automatic lookup when storage is unavailable. */ }
  detectRegion();
}

function renderCoverage() {
  const html = regions.map((region) => {
    const inRegion = projects.filter((project) => project.region === region);
    const lines = Object.entries(categories).map(([key, label]) => {
      const entries = inRegion.filter((project) => project.category === key);
      const projectCount = entries.filter((project) => project.recordType !== "official-source-index").length;
      const sourceCount = entries.filter((project) => project.recordType === "official-source-index").length;
      const coverageText = [projectCount ? `${projectCount} 项` : "", sourceCount ? `${sourceCount} 个官方入口` : ""].filter(Boolean).join(" + ") || "待补充";
      return `<li>${label}<b>${coverageText}</b></li>`;
    }).join("");
    return `<article class="coverage-card"><h3>${esc(region)}</h3><ul>${lines}</ul></article>`;
  }).join("");
  document.querySelector("#coverage").innerHTML = html;
}

function projectCard(project) {
  const deadline = project.deadline ? formatDate(project.deadline) : "以通知为准";
  const start = project.startAt ? formatDate(project.startAt) : "—";
  const statusClass = `pill-${project.status}`;
  return `<article class="card">
    <div class="card-top"><span class="pill">${esc(project.region)}</span><span class="pill">${esc(project.level)}</span><span class="pill">${esc(categories[project.category] || project.category)}</span>${project.sourceType === "university" ? '<span class="pill">高校通知</span>' : ""}<span class="pill ${statusClass}">${esc(project.statusText || statusLabels[project.status] || "状态待核验")}</span></div>
    <h3>${esc(project.name)}${project.year ? ` <span class="pill">${esc(project.year)}</span>` : ""}</h3>
    <p>${esc(project.summary)}</p>
    <dl><dt>主管单位</dt><dd>${esc(project.host)}</dd>${project.sourceType === "university" && project.sourceInstitution ? `<dt>通知高校</dt><dd>${esc(project.sourceInstitution)}</dd>` : ""}<dt>发布日期</dt><dd>${esc(formatDate(project.publishedAt))}</dd><dt>开放时间</dt><dd>${esc(start)}</dd><dt>截止时间</dt><dd>${esc(deadline)}</dd></dl>
    <div class="card-foot"><span>信息核验：${esc(project.checkedAt || "待核验")}${project.sourceCheckedAt ? ` · 来源可读：${esc(project.sourceCheckedAt)}` : ""}</span><span class="card-links"><a href="${esc(project.sourceUrl)}" target="_blank" rel="noopener noreferrer">${project.sourceType === "university" ? "高校通知" : "主管部门通知/来源"} ↗</a>${project.applicationUrl ? `<a href="${esc(project.applicationUrl)}" target="_blank" rel="noopener noreferrer">申报入口 ↗</a>` : ""}</span></div>
  </article>`;
}

function renderProjects() {
  const query = document.querySelector("#search").value.trim().toLocaleLowerCase("zh-CN");
  const region = document.querySelector("#region").value;
  const category = document.querySelector("#category").value;
  const status = document.querySelector("#status").value;
  const filtered = projectRecords.filter((project) => {
    const haystack = [project.name, project.host, project.region, project.summary, project.year].join(" ").toLocaleLowerCase("zh-CN");
    const active = isCurrentlyOpen(project);
    // Show current calls in the dedicated section above. The main list contains
    // other records by default, while an explicit status filter can include them.
    const statusMatches = !status || (status === "open" ? active : project.status === status);
    return (status ? statusMatches : !active) && (!query || haystack.includes(query)) && (!region || project.region === region) && (!category || project.category === category);
  }).sort((a, b) => {
    const rank = { open: 0, upcoming: 1, announced: 2, reference: 3, closed: 4 };
    return regionRank(a) - regionRank(b) || (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || (b.year || 0) - (a.year || 0) || a.name.localeCompare(b.name, "zh-CN");
  });
  document.querySelector("#resultsMeta").textContent = `符合条件 ${filtered.length} / ${projectRecords.length} 项`;
  const pageItems = filtered.slice(0, visibleProjectCount);
  document.querySelector("#cards").innerHTML = pageItems.length ? pageItems.map(projectCard).join("") : `<div class="empty">没有符合筛选条件的项目。</div>`;
  const pagination = document.querySelector("#pagination");
  pagination.innerHTML = filtered.length > pageItems.length
    ? `<button class="button" id="loadMore">加载更多（${pageItems.length}/${filtered.length}）</button>`
    : filtered.length > pageSize
      ? `<button class="button" id="showLess">收起列表</button><span>已显示全部 ${filtered.length} 项</span>`
      : filtered.length ? `<span>共 ${filtered.length} 项</span>` : "";
  document.querySelector("#loadMore")?.addEventListener("click", () => {
    visibleProjectCount += pageSize;
    renderProjects();
  });
  document.querySelector("#showLess")?.addEventListener("click", () => {
    visibleProjectCount = pageSize;
    renderProjects();
    document.querySelector("#projects").scrollIntoView({ behavior: "smooth" });
  });
}

function isCurrentlyOpen(project) {
  if (project.status !== "open") return false;
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
  return (!project.startAt || project.startAt.slice(0, 10) <= today) && (!project.deadline || project.deadline.slice(0, 10) >= today);
}

function renderActiveProjects() {
  const active = projectRecords.filter(isCurrentlyOpen).sort((a, b) => regionRank(a) - regionRank(b) || (a.deadline || "9999").localeCompare(b.deadline || "9999"));
  document.querySelector("#activeCount").textContent = active.length;
  document.querySelector("#activeCards").innerHTML = active.length
    ? active.slice(0, visibleActiveCount).map(projectCard).join("")
    : `<div class="empty">目前没有已确认仍在申报期内的项目。每周自动扫描新通知，符合规则的官方公告会自动收录。</div>`;
  const shown = Math.min(visibleActiveCount, active.length);
  document.querySelector("#activePagination").innerHTML = shown < active.length
    ? `<button class="button" id="loadMoreActive">加载更多申报项目（${shown}/${active.length}）</button>` : "";
  document.querySelector("#loadMoreActive")?.addEventListener("click", () => {
    visibleActiveCount += 6;
    renderActiveProjects();
  });
}

function renderSources(sources) {
  sourceDirectory = [...sources].sort((a, b) => Number(b.sourceType === "university") - Number(a.sourceType === "university") || a.region.localeCompare(b.region, "zh-CN"));
  renderSourcePage();
}

function renderSourcePage() {
  const shown = sourceDirectory.slice(0, visibleSourceCount);
  document.querySelector("#sourceList").innerHTML = shown.map((source) => `<a class="source" href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.host)}<span>${esc(source.region)} · ${source.sourceType === "university" ? "高校通知" : "主管部门公告"} ↗</span></a>`).join("");
  document.querySelector("#sourcePagination").innerHTML = shown.length < sourceDirectory.length
    ? `<button class="button" id="loadMoreSources">加载更多来源（${shown.length}/${sourceDirectory.length}）</button>` : "";
  document.querySelector("#loadMoreSources")?.addEventListener("click", () => {
    visibleSourceCount += 18;
    renderSourcePage();
  });
}

async function init() {
  const [projectResponse, sourceResponse] = await Promise.all([fetch("data/projects.json"), fetch("data/sources.json")]);
  if (!projectResponse.ok || !sourceResponse.ok) throw new Error("项目数据或来源目录读取失败");
  const projectData = await projectResponse.json();
  const sourceData = await sourceResponse.json();
  projects = projectData.projects;
  projectRecords = projects.filter((project) => project.recordType !== "official-source-index");
  document.querySelector("#totalCount").textContent = projectRecords.length;
  document.querySelector("#lastVerified").textContent = projectData.lastVerified || "—";
  populateOptions([...new Set([...projects.map((project) => project.region), ...sourceData.sources.map((source) => source.region)])]);
  renderCoverage();
  renderActiveProjects();
  renderProjects();
  renderSources(sourceData.sources);
  document.querySelector("#preferredRegion").addEventListener("change", (event) => setPreferredRegion(event.target.value, "manual"));
  document.querySelector("#detectRegion").addEventListener("click", detectRegion);
  ["#search", "#region", "#category", "#status"].forEach((selector) => document.querySelector(selector).addEventListener("input", () => {
    visibleProjectCount = pageSize;
    renderProjects();
  }));
  restoreRegionPreference();
}

document.querySelector("#themeToggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("qjzt-theme", next);
});
document.documentElement.dataset.theme = localStorage.getItem("qjzt-theme") || "dark";
init().catch((error) => {
  document.querySelector("#cards").innerHTML = `<div class="empty">项目数据暂时无法加载。请刷新页面，或在 GitHub 仓库查看 data/projects.json。</div>`;
  document.querySelector("#coverage").innerHTML = "";
  document.querySelector("#sourceList").innerHTML = "";
  console.error(error);
});
