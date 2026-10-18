const basePath = new URL('.', document.baseURI).pathname.replace(/\/$/, '');

const input = document.querySelector('#files');
const button = document.querySelector('#send');
const selection = document.querySelector('#selection');
const status = document.querySelector('#status');
const form = document.querySelector('#upload-form');
const defaultTarget = document.body.dataset.target || 'ipad';
form.elements.target.value = defaultTarget;

input.addEventListener('change', () => {
  const files = [...input.files];
  button.disabled = files.length === 0;
  selection.hidden = files.length === 0;
  selection.textContent = files.length ? `${files.length} 个文件，共 ${formatBytes(files.reduce((n, f) => n + f.size, 0))}` : '';
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const target = form.elements.target.value;
  button.disabled = true;
  button.textContent = '上传中…';
  status.textContent = '';
  try {
    const response = await fetch(`${basePath}/upload`, {method: 'POST', body: new FormData(form)});
    if (response.status === 401) { location.reload(); return; }
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '上传失败');
    status.textContent = target === 'pc'
      ? `✓ 已上传 ${data.files.length} 个文件，电脑在线后会自动保存`
      : `✓ 已上传 ${data.files.length} 个文件，等待 iPad 接收`;
    status.className = 'status success';
    form.reset();
    form.elements.target.value = target;
    selection.hidden = true;
  } catch (error) {
    status.textContent = error.message;
    status.className = 'status error';
  } finally {
    button.textContent = '上传';
    button.disabled = input.files.length === 0;
  }
});

function formatBytes(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}
