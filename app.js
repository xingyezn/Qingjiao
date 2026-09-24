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
const pageSize = 12;
let visibleProjectCount = pageSize;

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
  const categorySelect = document.querySelector("#category");
  Object.entries(categories).forEach(([value, label]) => categorySelect.add(new Option(label, value)));
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
    <div class="card-top"><span class="pill">${esc(project.region)}</span><span class="pill">${esc(project.level)}</span><span class="pill">${esc(categories[project.category] || project.category)}</span><span class="pill ${statusClass}">${esc(project.statusText || statusLabels[project.status] || "状态待核验")}</span></div>
    <h3>${esc(project.name)}${project.year ? ` <span class="pill">${esc(project.year)}</span>` : ""}</h3>
    <p>${esc(project.summary)}</p>
    <dl><dt>主办单位</dt><dd>${esc(project.host)}</dd><dt>发布日期</dt><dd>${esc(formatDate(project.publishedAt))}</dd><dt>开放时间</dt><dd>${esc(start)}</dd><dt>截止时间</dt><dd>${esc(deadline)}</dd></dl>
    <div class="card-foot"><span>核验：${esc(project.checkedAt || "待核验")}</span><span class="card-links"><a href="${esc(project.sourceUrl)}" target="_blank" rel="noopener noreferrer">官方通知/来源 ↗</a>${project.applicationUrl ? `<a href="${esc(project.applicationUrl)}" target="_blank" rel="noopener noreferrer">申报入口 ↗</a>` : ""}</span></div>
  </article>`;
}

function renderProjects() {
  const query = document.querySelector("#search").value.trim().toLocaleLowerCase("zh-CN");
  const region = document.querySelector("#region").value;
  const category = document.querySelector("#category").value;
  const status = document.querySelector("#status").value;
  const filtered = projects.filter((project) => {
    const haystack = [project.name, project.host, project.region, project.summary, project.year].join(" ").toLocaleLowerCase("zh-CN");
    const active = isCurrentlyOpen(project);
    // Show current calls in the dedicated section above. The main list contains
    // other records by default, while an explicit status filter can include them.
    const statusMatches = !status || (status === "open" ? active : project.status === status);
    return (status ? statusMatches : !active) && (!query || haystack.includes(query)) && (!region || project.region === region) && (!category || project.category === category);
  }).sort((a, b) => {
    const rank = { open: 0, upcoming: 1, announced: 2, reference: 3, closed: 4 };
    return (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || (b.year || 0) - (a.year || 0) || a.name.localeCompare(b.name, "zh-CN");
  });
  document.querySelector("#resultsMeta").textContent = `显示 ${filtered.length} / ${projects.length} 项`;
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
  const active = projects.filter(isCurrentlyOpen).sort((a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999"));
  document.querySelector("#activeCount").textContent = active.length;
  document.querySelector("#activeCards").innerHTML = active.length
    ? active.map(projectCard).join("")
    : `<div class="empty">目前没有已确认仍在申报期内的项目。每周自动扫描新通知，符合规则的官方公告会自动收录。</div>`;
}

function renderSources(sources) {
  document.querySelector("#sourceList").innerHTML = sources.map((source) => `<a class="source" href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.host)}<span>${esc(source.region)} · 打开公告页 ↗</span></a>`).join("");
}

async function init() {
  const [projectResponse, sourceResponse] = await Promise.all([fetch("data/projects.json"), fetch("data/sources.json")]);
  if (!projectResponse.ok || !sourceResponse.ok) throw new Error("项目数据或来源目录读取失败");
  const projectData = await projectResponse.json();
  const sourceData = await sourceResponse.json();
  projects = projectData.projects;
  document.querySelector("#totalCount").textContent = projects.length;
  document.querySelector("#lastVerified").textContent = projectData.lastVerified || "—";
  populateOptions([...new Set([...projects.map((project) => project.region), ...sourceData.sources.map((source) => source.region)])]);
  renderCoverage();
  renderActiveProjects();
  renderProjects();
  renderSources(sourceData.sources);
  ["#search", "#region", "#category", "#status"].forEach((selector) => document.querySelector(selector).addEventListener("input", () => {
    visibleProjectCount = pageSize;
    renderProjects();
  }));
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
