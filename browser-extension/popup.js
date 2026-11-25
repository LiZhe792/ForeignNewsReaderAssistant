'use strict';
chrome.storage.local.get({server:'http://127.0.0.1:5000'}).then(data=>document.querySelector('#server').value=data.server);
document.querySelector('#collect').addEventListener('click',async()=>{
 try{
  const server=new URL(document.querySelector('#server').value),[tab]=await chrome.tabs.query({active:true,currentWindow:true}),source=new URL(tab.url);
  if(!['http:','https:'].includes(source.protocol)||!['http:','https:'].includes(server.protocol)||server.username||server.password)throw Error('请选择网页文章，并填写正确的学习助手地址。');
  await chrome.storage.local.set({server:server.origin});
  await chrome.tabs.create({url:server.origin+'/#import?url='+encodeURIComponent(source.href)});
  window.close();
 }catch(error){document.querySelector('#status').textContent=error.message}
});
