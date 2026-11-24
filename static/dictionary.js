/* Shared, escaped dictionary presentation for the reading card and pending list. */
'use strict';
window.MarginDictionary = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const normalize = value => value.trim().toLowerCase().replaceAll('’', "'").replace(/\s+/g, ' ');
  const valid = value => value.length <= 60 && /^[A-Za-z]+(?:['’-][A-Za-z]+)*(?:\s+[A-Za-z]+(?:['’-][A-Za-z]+)*){0,5}$/.test(value);
  const controls = () => `<label class="dictionary-choice">查询词典<select data-reader-setting="dictionary_provider" aria-label="查询词典"><option value="auto">自动 · 离线优先</option><option value="offline">离线英汉 · ECDICT / Margin</option><option value="free">在线英英 · Free Dictionary</option></select></label>`;
  function pronunciation(result) {
    const sounds = result.phonetics || {other: result.phonetic};
    return `<div class="dictionary-pronunciations">${[['uk','英式'],['us','美式'],['other','词库音标 · 未区分地区']].filter(([key]) => sounds[key]).map(([key,label]) => `<div><span>${label}</span><strong lang="en">${escape(sounds[key])}</strong></div>`).join('')}</div>` +
      (result.lemma !== result.word ? `<p class="dictionary-note">原形参考：<strong lang="en">${escape(result.lemma)}</strong>。请结合原句确定词性和义项。</p>` : '');
  }
  function knowledge(result, interactive = true) {
    const info = result.knowledge;
    if (!info) return '';
    const term = word => interactive ? `<button type="button" class="dictionary-term" data-dictionary-term="${escape(word)}" aria-label="查词 ${escape(word)}" lang="en">${escape(word)}</button>` : `<span class="dictionary-term" lang="en">${escape(word)}</span>`;
    const chips = words => words.map(term).join('');
    const section = (label, content) => `<section class="dictionary-section"><h4>${label}</h4>${content}</section>`;
    const unavailable = '<p class="dictionary-note">此词暂无离线学习笔记。</p>';
    const ranks = info.frequency || {};
    const levels = [...(info.levels || []), ...(info.oxford_core ? ['Oxford 3000（词库标记）'] : []), ...(info.collins_stars ? [`Collins ${info.collins_stars} 星（词库标记）`] : [])];
    return `<details class="dictionary-knowledge"><summary>词汇知识 · 搭配、辨析与词族</summary>
      ${section('常见搭配', info.collocations?.length ? `<ul lang="en">${info.collocations.map(value => `<li>${escape(value)}</li>`).join('')}</ul>` : unavailable)}
      ${section('同义词与反义词', `<p class="dictionary-note">需结合义项；这些词不一定能在原句中直接替换。</p><p>近义：${info.synonyms?.length ? chips(info.synonyms) : '暂无数据'}</p><p>反义：${info.antonyms?.length ? chips(info.antonyms) : '暂无数据'}</p>`)}
      ${section('易混词辨析', info.confusables?.length ? info.confusables.map(group => `<div>${chips(group.words)}<p>${escape(group.note)}</p></div>`).join('') : unavailable)}
      ${section('词根词缀', info.roots?.length ? `<dl>${info.roots.map(([part,meaning]) => `<dt lang="en">${escape(part)}</dt><dd>${escape(meaning)}</dd>`).join('')}</dl><p class="dictionary-note">学习构词提示，不代表完整词源。</p>` : unavailable)}
      ${section('词族', info.family?.length ? chips(info.family) : unavailable)}
      ${section('词形变化', info.forms?.length ? info.forms.map(form => `<p>${escape(form.label)} ${term(form.word)}</p>`).join('') : '<p class="dictionary-note">未收录变化形式。</p>')}
      ${section('词频与词汇等级', `<p>BNC 词频排名：${ranks.bnc || '暂无数据'}<br>当代语料词频排名：${ranks.frq || '暂无数据'}</p><p>${escape(levels.join(' · ') || '暂无考试或核心词汇标记')}</p><p class="dictionary-note">来源：ECDICT 原始语料与考试标签；非实时词频、非 CEFR 等级，单一标记不代表个人难度。</p>`)}
      <p class="dictionary-note">学习笔记：Margin；语料标记与变化形式：ECDICT。英美音标仅在有明确地区数据时展示，同形词读音可能随词性、时态变化。</p>
    </details>`;
  }
  return {escape, normalize, valid, controls, pronunciation, knowledge};
})();
