window.avatarError = '';

function reportAvatarError(error) {
  const message = String(error && (error.message || error.reason) || error || '3Dモデルを初期化できません');
  window.avatarError = message;
  const box = document.querySelector('#error');
  if (box) {
    box.textContent = `3Dモデルを表示できません: ${message}`;
    box.classList.add('is-visible');
  }
}

window.addEventListener('error', event => {
  if (event.target && event.target !== window) {
    reportAvatarError(`ファイルを読み込めません: ${event.target.src || event.target.href || '不明なファイル'}`);
  } else {
    reportAvatarError(event.error || event.message);
  }
});
window.addEventListener('unhandledrejection', event => reportAvatarError(event.reason));
