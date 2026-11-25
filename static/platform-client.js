/* Shared identity bootstrap. Never caches authenticated API responses in a service worker. */
'use strict';
window.MarginPlatform={
 scope:'guest', preferences:{},
 ready:fetch('/api/platform/identity').then(r=>{if(!r.ok)throw Error('无法确认学习账户。');return r.json()}).then(info=>{
  MarginPlatform.scope=info.scope;MarginPlatform.preferences=info.preferences;
  const previous=sessionStorage.getItem('margin-active-account');
  if(previous&&previous!==info.scope){
   // Unsaved per-tab drafts must never be replayed into a different account.
   for(const key of Object.keys(sessionStorage))if(key.startsWith('margin'))sessionStorage.removeItem(key);
  }
  sessionStorage.setItem('margin-active-account',info.scope);
  localStorage.setItem('margin-offline-scope',info.scope);
  return info;
 }),
 date(value,withTime=false){
  const p=this.preferences, dateOnly=/^\d{4}-\d{2}-\d{2}$/.test(value);
  const d=new Date(dateOnly?value+'T12:00:00Z':value);if(!Number.isFinite(d.getTime()))return '—';
  const zone=dateOnly?'UTC':p.timezone||'Asia/Shanghai';
  const locale=p.date_format==='dmy'?'en-GB':p.date_format==='mdy'?'en-US':document.documentElement.lang;
  let result;
  if(p.date_format==='iso'){
   const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(d);
   result=['year','month','day'].map(k=>parts.find(x=>x.type===k).value).join('-');
  }else result=new Intl.DateTimeFormat(locale,{timeZone:zone,...(p.date_format==='locale'?{dateStyle:'medium'}:{year:'numeric',month:'2-digit',day:'2-digit'})}).format(d);
  return result+(withTime&&!dateOnly?' '+new Intl.DateTimeFormat(locale,{timeZone:zone,hour:'2-digit',minute:'2-digit'}).format(d):'');
 },
 mountReader(article){
  const target=document.querySelector('.reader-end');
  if(!target)return;
  const row=document.createElement('p'),save=document.createElement('button'),summary=document.createElement('a');
  save.className='button small';save.textContent='保存到离线阅读';save.type='button';
  save.onclick=async()=>{save.disabled=true;try{await MarginOffline.save(article.id,MarginPlatform.scope);save.textContent='已保存 · 可离线阅读';}catch(e){save.textContent=e.message;}finally{save.disabled=false;}};
  summary.className='button small';summary.href='/hub?article='+article.id+'#learning';summary.textContent='读后整理与复习';
  row.append(save,summary);target.append(row);
  fetch('/api/learning-coach/articles/'+article.id).then(r=>r.ok?r.json():null).then(data=>{
   if(!data?.transfer?.length||!row.isConnected)return;
   const link=document.createElement('a');link.className='button small';link.href='#lab/'+article.id;link.textContent='在新语境中复习 '+data.transfer.length+' 个已学词';row.append(link);
  }).catch(()=>{});
 }
};
// All module transports retain the account with which this page was opened.
// The server rejects stale tabs after a cookie/account change.
const marginFetch=window.fetch.bind(window);
window.fetch=(input,init={})=>{
 const url=new URL(input instanceof Request?input.url:String(input),location.href);
 if(url.origin===location.origin&&url.pathname.startsWith('/api/')&&!url.pathname.startsWith('/api/writing-workspace/')){
  const headers=new Headers(init.headers||(input instanceof Request?input.headers:undefined));
  if(!headers.has('X-Margin-Account'))headers.set('X-Margin-Account',MarginPlatform.scope);
  init={...init,headers};
 }
 return marginFetch(input,init);
};
window.addEventListener('storage',event=>{if(event.key==='margin-offline-scope'&&event.newValue!==MarginPlatform.scope){
 const old=document.getElementById('account-changed-notice');if(old)return;
 const notice=document.createElement('div');notice.id='account-changed-notice';notice.setAttribute('role','alert');notice.textContent='账户已在另一页面切换，请刷新后继续。';document.body.prepend(notice);
}});
if('serviceWorker'in navigator&&window.isSecureContext)navigator.serviceWorker.register('/sw.js').catch(()=>{});
