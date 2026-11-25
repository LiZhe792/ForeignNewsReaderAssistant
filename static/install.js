'use strict';
(()=>{
  let prompt=null;
  const button=document.createElement('button');button.type='button';button.className='button small';button.hidden=true;button.textContent='安装阅读助手';
  document.querySelector('.top-actions,header')?.append(button);
  window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();prompt=event;button.hidden=false;});
  button.addEventListener('click',async()=>{if(!prompt)return;await prompt.prompt();await prompt.userChoice;prompt=null;button.hidden=true;});
  window.addEventListener('appinstalled',()=>{prompt=null;button.hidden=true;});
  if('serviceWorker'in navigator&&window.isSecureContext)navigator.serviceWorker.register('/sw.js').catch(()=>{});
})();
