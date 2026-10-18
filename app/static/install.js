(() => {
  const base = new URL('.', document.baseURI).pathname.replace(/\/$/, '');
  const button = document.querySelector('#install');
  const hint = document.querySelector('#install-hint');
  let prompt;
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    prompt = event;
    if (button) button.hidden = false;
    if (hint) hint.textContent = '点击安装后，它会出现在 Android 系统分享菜单。';
  });
  window.addEventListener('appinstalled', () => {
    if (button) button.hidden = true;
    if (hint) hint.textContent = '已安装，现在可以从系统分享菜单发送。';
  });
  button?.addEventListener('click', async () => {
    if (!prompt) return;
    await prompt.prompt();
    const result = await prompt.userChoice;
    if (result.outcome === 'accepted') button.hidden = true;
    prompt = undefined;
  });
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register(`${base}/sw.js`, {scope: `${base}/`, updateViaCache: 'none'})
      .then(registration => registration.update())
      .catch(() => { if (hint) hint.textContent = '安装组件加载失败，请联网刷新本页后重试。'; });
  }
})();
