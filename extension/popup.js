const WEB_APP_URL = "http://127.0.0.1:8765/";

const titleElement = document.querySelector("#page-title");
const urlElement = document.querySelector("#page-url");
const button = document.querySelector("#open-dashboard");
const errorElement = document.querySelector("#error");

chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  const tab = tabs[0];
  const url = tab?.url || "";
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    titleElement.textContent = "当前页面不可用";
    urlElement.textContent = "请在 YouTube 视频页面点击插件。";
    showError("浏览器内部页面不能提交。");
    return;
  }
  titleElement.textContent = tab.title || "当前页面";
  urlElement.textContent = url;
  button.disabled = false;
  button.addEventListener("click", () => {
    const destination = `${WEB_APP_URL}?url=${encodeURIComponent(url)}`;
    chrome.tabs.create({ url: destination });
    window.close();
  });
});

function showError(message) {
  errorElement.textContent = message;
  errorElement.classList.remove("hidden");
}
