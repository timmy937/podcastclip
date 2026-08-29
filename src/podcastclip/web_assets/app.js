const state = { language: "zh", duration: "standard" };
const apiToken = document.querySelector('meta[name="podcastclip-api-token"]')?.content || "";
const stateChangeHeaders = {
  "Content-Type": "application/json",
  "X-PodcastClip-Token": apiToken,
};

const form = document.querySelector("#job-form");
const urlInput = document.querySelector("#url");
const queue = document.querySelector("#queue");
const count = document.querySelector("#job-count");
const captureNote = document.querySelector("#capture-note");

const statusLabels = {
  queued: "排队中",
  running: "处理中",
  cancelling: "取消中",
  cancelled: "已取消",
  completed: "已完成",
  failed: "失败",
};

const durationLabels = {
  quick: "2 分钟",
  standard: "5 分钟",
  deep: "8 分钟",
  long: "15 分钟",
};

const languageLabels = { zh: "中文", en: "English" };

for (const button of document.querySelectorAll("[data-value]")) {
  button.addEventListener("click", () => {
    const group = button.closest(".segmented");
    for (const sibling of group.querySelectorAll(".segment")) sibling.classList.remove("active");
    button.classList.add("active");
    if (group.id === "language-options") state.language = button.dataset.value;
    if (group.id === "duration-options") state.duration = button.dataset.value;
  });
}

const incomingUrl = new URLSearchParams(window.location.search).get("url");
if (incomingUrl) {
  urlInput.value = incomingUrl;
  captureNote.classList.remove("hidden");
  urlInput.focus();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = form.querySelector("button[type=submit]");
  submit.disabled = true;
  submit.querySelector("span:first-child").textContent = "正在加入";
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: stateChangeHeaders,
      body: JSON.stringify({ url: urlInput.value.trim(), duration: state.duration, target_language: state.language }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "提交失败");
    urlInput.value = "";
    captureNote.classList.add("hidden");
    await refreshJobs();
  } catch (error) {
    window.alert(error.message);
  } finally {
    submit.disabled = false;
    submit.querySelector("span:first-child").textContent = "加入队列";
  }
});

queue.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-cancel-job]");
  if (!button || button.disabled) return;
  if (!window.confirm("取消这个生成任务？")) return;

  button.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(button.dataset.cancelJob)}`, {
      method: "DELETE",
      headers: { "X-PodcastClip-Token": apiToken },
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "取消失败");
    await refreshJobs();
  } catch (error) {
    button.disabled = false;
    window.alert(error.message);
  }
});

async function refreshJobs() {
  try {
    const response = await fetch("/api/jobs", { cache: "no-store" });
    const data = await response.json();
    renderJobs(data.jobs || []);
  } catch (error) {
    console.error(error);
  }
}

function renderJobs(jobs) {
  count.textContent = jobs.length;
  if (!jobs.length) {
    queue.innerHTML = `<div class="empty-state"><span class="empty-mark">+</span><p>还没有生成任务</p><span>从上方提交一个 YouTube 链接</span></div>`;
    return;
  }
  queue.innerHTML = jobs.map(renderJob).join("");
}

function renderJob(job) {
  const status = statusLabels[job.status] || job.status;
  const statusClass = `status-${job.status}`;
  const latestLog = job.logs?.at(-1) || "等待开始";
  const result = job.result;
  const links = result ? `
    <div class="result-links">
      <a class="audio-link" href="${escapeHtml(result.audio_url)}" target="_blank" rel="noopener noreferrer">播放 MP3 <span>↗</span></a>
      <a href="${escapeHtml(result.script_url)}" target="_blank" rel="noopener noreferrer">脚本</a>
      <a href="${escapeHtml(result.transcript_url)}" target="_blank" rel="noopener noreferrer">转录</a>
      ${result.feed_url ? `<a href="${escapeHtml(result.feed_url)}" target="_blank" rel="noopener noreferrer">RSS</a>` : ""}
    </div>` : "";
  const duration = result?.duration_seconds ? formatDuration(result.duration_seconds) : durationLabels[job.duration];
  const error = job.error ? `<p class="job-error">${escapeHtml(job.error)}</p>` : "";
  const cancelButton = job.can_cancel ? `
    <button class="cancel-job" type="button" data-cancel-job="${escapeHtml(job.id)}" aria-label="取消任务" title="取消任务">
      <span aria-hidden="true">■</span>
    </button>` : "";
  return `
    <article class="job-item">
      <div class="job-topline">
        <span class="job-status ${statusClass}"><span class="mini-dot"></span>${status}</span>
        <div class="job-actions"><span class="job-duration">${duration}</span>${cancelButton}</div>
      </div>
      <h3>${escapeHtml(job.title)}</h3>
      <p class="job-url">${escapeHtml(job.url)}</p>
      <div class="job-meta"><span>${languageLabels[job.target_language] || job.target_language}</span><span>${escapeHtml(latestLog)}</span></div>
      ${error}
      ${links}
    </article>`;
}

function formatDuration(seconds) {
  const value = Math.round(Number(seconds));
  const minutes = Math.floor(value / 60);
  const remainder = String(value % 60).padStart(2, "0");
  return `${minutes}:${remainder}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

refreshJobs();
window.setInterval(refreshJobs, 2500);
