/* Playback races and media lifecycle, using Node's standard library and the real controller. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeAudio extends EventTarget {
  constructor() { super(); this.paused=true;this.currentTime=0;this.duration=6;this.playbackRate=1;this.defaultPlaybackRate=1;this.plays=0; }
  load() { this.currentTime=0;this.playbackRate=this.defaultPlaybackRate; }
  play() { this.paused=false;this.plays++;return Promise.resolve(); }
  pause() { this.paused=true; }
  removeAttribute(name) { if(name==='src')this.src=''; }
}

function setup(api=async()=>({url:'/api/tts/audio/test',cached:true})) {
  const elements=new Map();
  const element=selector=>{
    if(!elements.has(selector))elements.set(selector,{textContent:'',innerHTML:'',value:'',hidden:false,disabled:false,
      setAttribute(){},classList:{add(){},remove(){},toggle(){}},querySelector:element,querySelectorAll:()=>[]});
    return elements.get(selector);
  };
  const window={addEventListener(){}}, document={addEventListener(){},querySelector:()=>null,activeElement:null};
  const context=vm.createContext({window,document,Audio:FakeAudio,AbortController,location:{hash:'#reader/1'}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/speech.js'),'utf8'),context);
  const player=new window.MarginSpeech({api:(route,options)=>route==='/tts/cache'
    ? Promise.resolve({files:0,bytes:0,limit_bytes:128*1024*1024}) : api(route,options),toast(){}});
  player.panel=element('panel');player.root=element('root');
  player.reader={body:element('body'),captureAnchor:()=>({}),restoreAnchor(){},current:0};
  player.article={id:1,tts_segments:[{index:0,paragraph:0,text:'First sentence.'},{index:1,paragraph:0,text:'Second sentence.'},{index:2,paragraph:1,text:'Third sentence.'}]};
  player.follow=()=>{};
  player.ready=Promise.resolve();
  player.sequence=player.units({scope:'article'});
  player.desired='playing';
  return {player,element,context};
}

const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('loading a new sentence retains speed despite the media load reset',async()=>{
  const {player}=setup();player.settings.rate=1.7;
  await player.loadSegment(0);assert.equal(player.audio.playbackRate,1.7);
  await player.loadSegment(1);assert.equal(player.audio.playbackRate,1.7);
});

test('pause and continue keep the same audio and its current time',async()=>{
  let calls=0;const {player}=setup(async()=>{calls++;return {url:'/same.mp3',cached:true};});
  await player.loadSegment(0);player.audio.currentTime=2.5;player.pause();
  assert.equal(player.audio.paused,true);player.togglePlay();await settle();
  assert.equal(player.audio.currentTime,2.5);assert.equal(player.audio.paused,false);assert.equal(calls,2); // current plus one prefetched sentence
});

test('pausing during generation leaves the prepared clip paused',async()=>{
  let finish;const {player}=setup(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=player.loadSegment(0);player.pause();finish({url:'/ready.mp3',cached:false});await pending;
  assert.equal(player.state,'paused');assert.equal(player.audio.plays,0);
  player.togglePlay();await settle();assert.equal(player.audio.plays,1);
});

test('stopping makes a late generation result unable to start audio',async()=>{
  let finish;const {player}=setup(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=player.loadSegment(0);player.stop();finish({url:'/late.mp3',cached:false});await pending;
  assert.equal(player.audio.plays,0);assert.equal(player.audio.src,'');assert.equal(player.state,'idle');
});

test('a superseded request cannot replace the newer sentence',async()=>{
  const finish=[];const {player}=setup(()=>new Promise(resolve=>finish.push(resolve)));
  const first=player.loadSegment(0),second=player.loadSegment(1);
  finish[1]({url:'/new.mp3',cached:true});await second;
  finish[0]({url:'/old.mp3',cached:true});await first;
  assert.equal(player.audio.src,'/new.mp3');assert.equal(player.current.index,1);
});

test('a failed fetch stays retryable and is not automatically retried',async()=>{
  let calls=0;const {player,element}=setup(async()=>{calls++;throw new Error('Offline');});
  await player.loadSegment(0);await settle();
  assert.equal(calls,1);assert.equal(player.state,'error');assert.equal(player.audio.paused,true);
  assert.match(element('#tts-status').textContent,/Offline/);
  player.togglePlay();await settle();assert.equal(calls,2);
});

test('sentence and paragraph loops advance only within the intended unit',async()=>{
  const {player}=setup();await player.loadSegment(0);
  player.settings.loop='sentence';await player.advance();await settle();assert.equal(player.current.index,0);
  player.settings.loop='paragraph';await player.advance();await settle();assert.equal(player.current.index,1);
  await player.advance();await settle();assert.equal(player.current.index,0);
  player.settings.loop='none';await player.advance();await settle();await player.advance();await settle();
  assert.equal(player.current.index,2);
});

test('starting a new cache job is not reset by the cancelled job finishing',async()=>{
  const finish=[];const {player,element}=setup(()=>new Promise(resolve=>finish.push(resolve)));
  player.article.tts_segments=player.article.tts_segments.slice(0,1);
  const old=player.cacheArticle();player.cancelCache();player.settings.voice='en-GB-SoniaNeural';
  const current=player.cacheArticle();finish[0]({url:'/old.mp3',cached:false});await old;
  assert.equal(player.caching,true);assert.equal(element('[data-tts-action="cache-article"]').disabled,true);
  finish[1]({url:'/new.mp3',cached:false});await current;assert.equal(player.caching,false);
});

test('a deferred start does not play an article opened while waiting',async()=>{
  const {player}=setup();let ready;player.ready=new Promise(resolve=>{ready=resolve;});
  const start=player.startScope();player.mountVersion++;ready();await start;
  assert.equal(player.audio.plays,0);
});

test('a previous failed settings save does not block loading the next article',async()=>{
  const {player,element}=setup();
  player.saveQueue=Promise.reject(new Error('Previous save failed'));
  player.api=async route=>route==='/tts/settings'
    ? {settings:{...player.settings,rate:.8},voices:[{id:'en-US-AriaNeural',accent:'en-US',name:'Aria'}]}
    : route==='/tts/playlist'?{items:[]}:{files:0,bytes:0,limit_bytes:128*1024*1024};
  await player.initialize(player.mountVersion);
  assert.equal(player.settings.rate,.8);assert.equal(element('#tts-voice').value,'en-US-AriaNeural');
});
