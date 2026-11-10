/*
 * 原作者水印文件，严禁更改、删除或替换作者署名与链接，严禁隐藏或遮挡水印。
 * 二次开发必须保留 StarAiri 的 B站链接及原 GitHub 仓库链接。严禁售卖软件。
 * Copyright (c) 2026 StarAiri / strawberry0321. See LICENSE and AUTHORSHIP.md.
 */
(() => {
  'use strict';
  const links = [
    ['B站：StarAiri', 'https://space.bilibili.com/437268141'],
    ['github：strawberry0321', 'https://github.com/strawberry0321/HD2-Vehicle-Paint-Studio']
  ];
  const watermark = document.createElement('nav');
  watermark.className = 'author-watermark';
  watermark.setAttribute('aria-label', '原作者主页');
  for (const [label, url] of links) {
    const link = document.createElement('a');
    link.textContent = label;
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.title = label + '（在浏览器中打开）';
    link.addEventListener('click', event => {
      if (window.chrome?.webview) {
        event.preventDefault();
        window.chrome.webview.postMessage({type: 'open-author-link', url});
      }
    });
    watermark.append(link);
  }
  document.body.append(watermark);
})();
