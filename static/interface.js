/* Interface preferences belong to this browser, independently of reading data and accounts. */
'use strict';

const MarginInterface = (() => {
  const key = 'margin-interface';
  const root = document.documentElement;
  const messages = {
    skip: ['跳到主要内容', 'Skip to main content'],
    'display.settings': ['语言与显示', 'Language and display'],
    'display.language': ['界面语言', 'Interface language'],
    'display.large': ['放大界面文字', 'Larger interface text'],
    'display.contrast': ['高对比度', 'High contrast'],
    'display.motion': ['减少动画', 'Reduce motion'],
    'display.close': ['完成', 'Done'],
    'display.scope': ['主导航、基础阅读流程和写作空间支持中英文。部分学习扩展保留中文。', 'The main navigation, core reader and writing workspace support Chinese and English. Some learning tools are available in Chinese.'],
    'writing.page_title': ['多端写作 · Margin', 'Writing workspace · Margin'],
    'writing.title': ['多端写作', 'Writing workspace'],
    'writing.intro': ['在连接同一写作服务的设备上登录同一账户，即可继续编辑草稿。', 'Sign in to the same writing service on your devices to continue a draft.'],
    'writing.sign_in': ['登录', 'Sign in'],
    'writing.username': ['用户名', 'Username'],
    'writing.password': ['密码', 'Password'],
    'writing.invite': ['注册邀请码', 'Registration invite code'],
    'writing.registration_help': ['用户名可使用 3–32 位字母、数字、点、下划线和连字符。密码至少 8 位。', 'Use 3–32 letters, numbers, dots, underscores or hyphens for the username and at least 8 characters for the password.'],
    'writing.register': ['创建账户', 'Create account'],
    'writing.sign_out': ['退出此设备', 'Sign out on this device'],
    'writing.sign_out_all': ['退出所有设备', 'Sign out on all devices'],
    'writing.drafts': ['草稿', 'Drafts'],
    'writing.new': ['新建草稿', 'New draft'],
    'writing.sync': ['同步', 'Sync'],
    'writing.editor': ['编辑草稿', 'Draft editor'],
    'writing.select_draft': ['请选择一份草稿，或新建草稿。', 'Open a draft or create one.'],
    'writing.draft_title': ['标题', 'Title'],
    'writing.content': ['正文', 'Body'],
    'writing.save': ['保存草稿', 'Save draft'],
    'writing.download': ['下载文本', 'Download text'],
    'writing.delete': ['删除草稿', 'Delete draft'],
    'writing.conflict': ['草稿已在另一设备上修改', 'Another device updated this draft'],
    'writing.see_remote': ['查看服务器版本', 'View server version'],
    'writing.use_remote': ['载入服务器版本', 'Load server version'],
    'writing.copy': ['将本机版本另存为草稿', 'Save your version as a new draft'],
    'writing.empty': ['还没有已保存的草稿。', 'You have no saved drafts.'],
    'writing.count': ['{count} / 20,000 字符', '{count} / 20,000 characters'],
    'writing.confirm_sign_out': ['退出此账户的所有设备？', 'Sign out of this account on all devices?'],
    'writing.confirm_remote': ['载入服务器版本会清除当前编辑框中的本机修改。继续？', 'Loading the server version will discard the edits in this editor. Continue?'],
    'writing.confirm_delete': ['删除这份草稿？', 'Delete this draft?'],
    'writing.untitled': ['未命名草稿', 'Untitled draft'],
    'writing.signed_in': ['已登录。', 'Signed in.'],
    'writing.signed_out': ['已退出。', 'Signed out.'],
    'writing.opened': ['已打开草稿。', 'Draft opened.'],
    'writing.saved': ['草稿已保存并同步。', 'Draft saved and synced.'],
    'writing.pending': ['有修改待保存。', 'Changes pending.'],
    'writing.recovered': ['已恢复本标签页的未同步修改。', 'Recovered unsynced edits from this tab.'],
    'writing.synced': ['已同步服务器草稿。', 'Drafts synced.'],
    'writing.deleted': ['草稿已删除。', 'Draft deleted.'],
    'writing.connection': ['暂时无法连接写作服务。未同步修改仍在此标签页。', 'The writing service is unavailable. Unsynced edits are still in this tab.'],
    'writing.invalid_credentials_format': ['请检查用户名和密码格式。', 'Check the username and password format.'],
    'writing.invalid_credentials': ['用户名或密码有误。', 'The username or password is incorrect.'],
    'writing.username_taken': ['用户名已被使用。', 'That username is already in use.'],
    'writing.invalid_invite': ['注册邀请码有误。', 'That registration code is incorrect.'],
    'writing.registration_closed': ['此写作服务暂未开放注册。', 'Registration is closed on this writing service.'],
    'writing.rate_limited': ['尝试次数过多，请稍后重试。', 'Too many attempts. Try again later.'],
    'writing.secure_required': ['请使用 HTTPS 连接写作服务。', 'Connect to the writing service over HTTPS.'],
    'writing.sign_in_required': ['请重新登录写作账户。', 'Sign in to your writing account again.'],
    'writing.title_required': ['请先填写标题。', 'Enter a title first.'],
    'writing.unresolved': ['当前草稿仍有未同步修改，请保存或另存后再切换。', 'Save your current changes or save a copy before switching drafts.'],
    'writing.invalid_draft': ['标题需为 1–140 字符，正文最多 20,000 字符。', 'Use 1–140 characters for the title and up to 20,000 for the body.'],
    'writing.draft_missing': ['这份草稿已不存在。', 'This draft no longer exists.'],
  };

  const english = {
    'Margin 知刊 · 外刊学习助手': 'Margin · English reading companion',
    '主导航': 'Main navigation',
    'Margin 知刊首页': 'Margin home',
    '我的学习空间': 'My learning space',
    '发现文章': 'Article library',
    '沉浸阅读': 'Reader',
    '我的生词': 'Vocabulary',
    '间隔复习': 'Spaced review',
    '听说训练': 'Listening & speaking',
    '阅读测验': 'Reading quizzes',
    '难度与推荐': 'Level & recommendations',
    '学习数据分析': 'Learning analytics',
    '离线与数据管理': 'Offline & data management',
    '学习计划': 'Study plans',
    '学习足迹': 'Learning activity',
    '每天留一点时间给世界': 'Make time to read',
    '今日阅读': 'Reading today',
    '使用说明': 'Help',
    '我的阅读手记': 'My reading journal',
    '本地存储 · 点滴积累': 'Stored on this device',
    '本地学习空间': 'Local workspace',
    '离线模式 · 本机服务': 'Offline mode · local service',
    '导入文章': 'Import article',
    '学习数据已在另一页面还原，本页旧操作已暂停。重新载入后继续使用。': 'Learning data was restored in another tab. Reload this page to resume.',
    '重新载入': 'Reload',
    '正在翻开你的阅读空间…': 'Opening your learning space…',
    '正在打开你的阅读手记…': 'Opening your reading journal…',
    '把好文章，带到这里': 'Import an article',
    '关闭导入窗口': 'Close import dialog',
    '粘贴正文': 'Paste text',
    '网页链接': 'Web link',
    '文章标题': 'Article title',
    '英文正文': 'English article text',
    '粘贴至少 40 个英文单词。空行会保留为段落。': 'Paste at least 40 English words. Blank lines separate paragraphs.',
    '来源（选填）': 'Source (optional)',
    '例如：BBC / The Guardian / 个人笔记': 'For example, BBC, The Guardian or personal notes',
    '文章链接': 'Article URL',
    '自动提取公开网页的正文。付费、登录或动态加载的页面，请使用粘贴正文；仅导入你有权使用的内容。': 'Extract text from a public page. Paste text for paywalled, sign-in or dynamically loaded pages. Import content you have permission to use.',
    '文章分类': 'Category',
    '全部': 'All',
    '科技': 'Technology',
    '文化': 'Culture',
    '科学': 'Science',
    '生活': 'Life',
    '环境': 'Environment',
    '其他': 'Other',
    '导入后将自动估算阅读难度': 'Reading difficulty is estimated automatically.',
    '导入并开始阅读': 'Import and read',
    '正在提取正文…': 'Extracting article text…',
    '正在导入…': 'Importing…',
    '让阅读成为日常': 'Using Margin',
    '关闭使用说明': 'Close help',
    '开始你的阅读': 'Getting started',
    '关于这个阅读空间': 'About this workspace',
    '导入文章或选择示例文章，在阅读工具栏调整字体、排版和主题，也可打开段落目录、专注模式及全屏。': 'Import or open a sample article. Use the reader toolbar to adjust type, layout, theme, focus mode or full screen, or open the contents.',
    '流畅模式点词先暂存，读完一段再集中处理；精读模式点词打开查词卡片，按需展开释义并加入生词本。阅读器的「操作帮助」中列出了快捷键和手机手势。': 'In fluent mode, queue words for lookup after a paragraph. In close-reading mode, open the lookup card and expand its definition on demand. Reader help lists keyboard shortcuts and gestures.',
    '在「我的生词」中整理词书、义项、原句和媒体卡片，批量导入导出；在「间隔复习」中进行卡片、挖空、听音和拼写练习，按记忆表现评分，也可手动标记掌握度，在「学习足迹」中查看阅读记录和打卡日历。': 'Organize wordbooks and example sentences under Vocabulary. Practice with cards, cloze, listening and spelling in Spaced review. Open Learning activity to see reading records and check-ins.',
    '内置英汉词库优先查询，未收录的词联网查询英英词典。文章难度由阅读易读度估算，仅供选文参考。': 'Dictionary lookup tries the built-in English–Chinese dictionary first and can check an online English dictionary. Article levels are estimates based on readability.',
    '添加生词': 'Add a word',
    '添加一个想记住的词': 'Add a word to remember',
    '关闭生词编辑': 'Close vocabulary editor',
    '英文单词或短语': 'English word or phrase',
    '例如：perspective': 'For example, perspective',
    '释义': 'Definition',
    '写下你对这个词的理解': 'Describe what the word means',
    '学习笔记（选填）': 'Study notes (optional)',
    '搭配、联想，或你自己的例句': 'Collocations, associations or an example sentence',
    '可在生词本中标记掌握程度': 'You can track mastery in Vocabulary.',
    '保存生词': 'Save word',
    '删除这条记录？': 'Delete this record?',
    '取消删除': 'Cancel deletion',
    '保留记录': 'Keep record',
    '确认删除': 'Delete',
    '阅读设置': 'Reader settings',
    '关闭阅读设置': 'Close reader settings',
    '正文字体': 'Article font',
    'Georgia · 书卷衬线': 'Georgia · serif',
    'Times New Roman · 经典衬线': 'Times New Roman · serif',
    'Arial · 清晰无衬线': 'Arial · sans serif',
    'Consolas · 等宽字体': 'Consolas · monospace',
    '正文排版': 'Article layout',
    '单栏': 'One column',
    '双栏 · 每行先左后右': 'Two columns · left to right',
    '字号': 'Text size',
    '行距': 'Line spacing',
    '正文最大宽度': 'Maximum article width',
    '段落间距': 'Paragraph spacing',
    '阅读主题': 'Reader theme',
    '浅色': 'Light',
    '深色': 'Dark',
    '护眼': 'Sepia',
    '显示段落编号': 'Show paragraph numbers',
    '专注模式': 'Focus mode',
    '启用阅读快捷键': 'Enable reader shortcuts',
    '启用手机左右轻扫': 'Enable horizontal swipe gestures',
    '宽度会适应当前窗口；正文区域不足 680 px 时，双栏自动显示为单栏。字体使用本机可用字体，无需下载。': 'The layout adapts to the window. Two columns become one when the article area is narrower than 680 px. Fonts are taken from this device.',
    '阅读与学习节奏': 'Reading and study pace',
    '阅读模式': 'Reading mode',
    '流畅阅读 · 点词先暂存': 'Fluent reading · queue words',
    '精读模式 · 点词打开查词卡片': 'Close reading · open word lookup',
    '学习提示密度': 'Study hint density',
    '安静 · 手动操作，无主动提示': 'Quiet · open tools manually',
    '适中 · 每段首次遇见时标记': 'Balanced · mark first use in each paragraph',
    '详细 · 全部学习词与操作提示': 'Detailed · show all study words and hints',
    '阶段提醒间隔（分钟）': 'Reminder interval (minutes)',
    '查词卡片默认折叠释义': 'Collapse definitions by default',
    '已掌握词减少标记': 'Show fewer hints for mastered words',
    '设置自动保存在本机': 'Settings are saved on this device',
    '重试保存': 'Retry save',
    '恢复默认': 'Restore defaults',
    '完成': 'Done',
    '段落目录': 'Contents',
    '关闭目录': 'Close contents',
    '按原文段落定位，当前阅读的段落会高亮显示。': 'Jump to a paragraph. The current paragraph is highlighted.',
    '文章段落目录': 'Article contents',
    '阅读操作帮助': 'Reader help',
    '关闭阅读操作帮助': 'Close reader help',
    '切换专注模式': 'Toggle focus mode',
    '进入或退出全屏': 'Toggle full screen',
    '段落目录 / 阅读设置': 'Contents / Reader settings',
    '段落编号 / 单双栏': 'Paragraph numbers / Columns',
    '上一段 / 下一段': 'Previous / Next paragraph',
    '减小 / 增大字号': 'Decrease / Increase text size',
    '向下 / 向上翻一屏': 'Page down / Page up',
    '文章顶部 / 文末': 'Beginning / End of article',
    '显示本帮助': 'Show reader help',
    '关闭弹窗或查词面板；退出全屏或专注模式': 'Close a dialog or word lookup. Exit full screen or focus mode.',
    '手机手势': 'Touch gestures',
    '先读懂，再深究': 'Lookup as you read',
    '集中处理待查词': 'Review queued words',
    '关闭待查词': 'Close queued words',
    '待查词词典': 'Dictionary for queued words',
    '自动 · 离线优先': 'Automatic · offline first',
    '离线英汉 · ECDICT / Margin': 'Offline English–Chinese · ECDICT / Margin',
    '在线英英 · Free Dictionary': 'Online English–English · Free Dictionary',
    '处理范围': 'Scope',
    '待查词段落范围': 'Paragraph range for queued words',
    '完成，继续阅读': 'Done, return to reading',
    'AI 语境释义与翻译辅助': 'AI context and translation',
    '关闭 AI 学习面板': 'Close AI study panel',
    'AI 语境释义': 'AI contextual meaning',
    '翻译辅助': 'Translation',
    '配置 AI 服务': 'Configure AI service',
    '生成语境释义': 'Explain in context',
    '停止后续生成': 'Stop subsequent requests',
    '重新读取': 'Reload',
    '仅看收藏译文': 'Saved translations only',
    '原文段落': 'Source paragraph',
    'AI 处理范围': 'AI scope',
    '当前划选 / 单词': 'Selection / Word',
    '句子': 'Sentence',
    '整段': 'Paragraph',
    '全文逐段翻译': 'Full article, paragraph by paragraph',
    '原文句子': 'Source sentence',
    '解释深度': 'Explanation level',
    '入门 · 简明解释': 'Beginner · concise',
    '进阶 · 语法与语境': 'Intermediate · grammar and context',
    '深入 · 语义与修辞': 'Advanced · meaning and rhetoric',
    '译文按需展开，原文始终保留。': 'Expand translations on demand, with the source text kept visible.',
    '学习提醒': 'Study reminders',
    '把世界，读成自己的见识。': 'Explore English articles',
    '从一篇好文章开始，让每一次阅读都有所收获。': 'Choose an article and build your reading skills.',
    '下一篇好文章，等你带来': 'Add your first article',
    '导入一篇文章，开启你的阅读手记。': 'Paste an article or import one from a link.',
    '在字里行间，发现新的视角。': 'Read the featured article.',
    '开始阅读': 'Start reading',
    '一点一滴，正在发生': 'Daily progress',
    '天': ' days',
    '连续学习的日子': 'Current streak',
    '今日已打卡 · 继续保持': 'Checked in today',
    '今天，也来读一点': 'Check in today',
    '为你选文': 'Recommended articles',
    '查看个性化选文 →': 'View recommendations →',
    '发现好文章': 'Browse articles',
    '搜索文章': 'Search articles',
    '寻找感兴趣的标题或主题…': 'Search by title or topic…',
    '筛选文章难度': 'Filter by reading level',
    '所有阅读难度': 'All reading levels',
    '内置内容为原创学习示例': 'Built-in articles are original practice material',
    '阅读不必匆忙，理解自有回响。': 'Read at your own pace.',
    '根据阅读易读度估算，并非正式 CEFR 测评': 'Estimated from readability',
    '轻松阅读': 'Easy reading',
    '进阶阅读': 'Intermediate reading',
    '挑战阅读': 'Challenging reading',
    '暂时没有找到匹配文章': 'No matching articles',
    '换一个关键词或分类，也许会有新的发现。': 'Try another keyword or category.',
    '查看全部文章': 'Show all articles',
    '去阅读': 'Read',
    '再读一遍': 'Read again',
    '继续阅读': 'Continue reading',
    '从这篇文章，遇见一个新的视角。': 'Open this article to read more.',
    '返回文章库': 'Back to article library',
    '阅读工具栏': 'Reader toolbar',
    '减小字号': 'Decrease text size',
    '增大字号': 'Increase text size',
    '段落目录（T）': 'Contents (T)',
    '阅读设置（S）': 'Reader settings (S)',
    '专注模式（F）': 'Focus mode (F)',
    '全屏阅读（G）': 'Full screen (G)',
    '打开查词面板': 'Open word lookup',
    '目录': 'Contents',
    '专注': 'Focus',
    '全屏': 'Full screen',
    '查词': 'Look up',
    'AI 语境': 'AI context',
    '翻译': 'Translate',
    '快捷键与手机手势（?）': 'Shortcuts and gestures (?)',
    '模式': 'Mode',
    '流畅阅读': 'Fluent reading',
    '精读模式': 'Close reading',
    '本段生词': 'Words in this paragraph',
    '待查词': 'Queued words',
    '重试暂存': 'Retry queue save',
    '文章阅读区域': 'Article reading area',
    '阅读原文 ↗': 'Open original ↗',
    '原创学习示例': 'Original practice article',
    '个人导入': 'Imported article',
    '删除文章': 'Delete article',
    '听力与口语训练': 'Listening and speaking',
    '阅读练习与测验': 'Reading practice and quizzes',
    '个人难度与读后反馈': 'Difficulty and reading feedback',
    '根据文章写作': 'Write about this article',
    '单击或划选一个英文单词，查看释义并收藏': 'Select a word or open Look up to enter a word with the keyboard.',
    '已完成阅读': 'Reading complete',
    '标记为已读 · 完成打卡': 'Mark as read and check in',
    '又多了解了一点世界，也离目标近了一点。': 'You reached the end of the article.',
    '阅读的留白': 'Word lookup',
    '关闭查词面板': 'Close word lookup',
    '输入英文单词或短语查词': 'Look up an English word or phrase',
    '输入单词，或直接点击正文': 'Type a word or select one in the article',
    '查询单词': 'Look up word',
    '上一段': 'Previous paragraph',
    '下一段': 'Next paragraph',
    '上一段（[）': 'Previous paragraph ([)',
    '下一段（]）': 'Next paragraph (])',
    '进度自动保存在本机': 'Progress is saved on this device',
    '给学习留一点空隙': 'Study reminder',
    '整理待查词': 'Review queued words',
    '5 分钟后提醒': 'Remind me in 5 minutes',
    'AI 释义所选': 'Explain selection',
    '翻译所选': 'Translate selection',
    '这页暂时没有翻开': 'This page is unavailable',
    '暂时无法打开阅读空间': 'The workspace is unavailable',
    '重试连接': 'Retry connection',
    '展开释义': 'Show definition',
    '收起释义': 'Hide definition',
    '添加到生词本': 'Add to vocabulary',
    '保存进度': 'Save progress',
  };

  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(key) || '{}') || {}; }
  catch { /* Browser storage is optional. */ }
  const preferences = {
    language: stored.language === 'en' ? 'en' : 'zh-CN',
    large: stored.large === true,
    contrast: stored.contrast === true,
    motion: stored.motion === true,
  };
  const t = (id, params = {}) => {
    const translation = Object.hasOwn(messages, id) ? messages[id][preferences.language === 'en' ? 1 : 0] :
      preferences.language === 'en' && Object.hasOwn(english, id) ? english[id] : id;
    return translation.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(params, name) ? params[name] : match);
  };
  const number = value => new Intl.NumberFormat(preferences.language).format(value);
  const dateTime = value => new Intl.DateTimeFormat(preferences.language, {
    dateStyle: 'medium', timeStyle: 'short',
  }).format(new Date(value));

  function localize(scope = document) {
    scope.querySelectorAll('[data-i18n]').forEach(element => {
      const value = t(element.dataset.i18n);
      if (element.textContent !== value) element.textContent = value;
    });
    for (const attribute of ['aria-label', 'placeholder', 'title']) {
      scope.querySelectorAll('[data-i18n-' + attribute + ']').forEach(element => {
        const value = t(element.getAttribute('data-i18n-' + attribute));
        if (element.getAttribute(attribute) !== value) element.setAttribute(attribute, value);
      });
    }
  }

  const settings = document.createElement('dialog');
  settings.className = 'modal display-settings';
  settings.setAttribute('aria-labelledby', 'display-settings-title');
  settings.innerHTML = `
    <h2 id="display-settings-title" data-i18n="display.settings"></h2>
    <label><span id="interface-language-label" data-i18n="display.language"></span>
      <select name="language" aria-labelledby="interface-language-label"><option value="zh-CN" lang="zh-CN">简体中文</option><option value="en" lang="en">English</option></select>
    </label>
    <p data-i18n="display.scope"></p>
    <label><input type="checkbox" name="large"> <span data-i18n="display.large"></span></label>
    <label><input type="checkbox" name="contrast"> <span data-i18n="display.contrast"></span></label>
    <label><input type="checkbox" name="motion"> <span data-i18n="display.motion"></span></label>
    <div class="modal-footer"><button class="button primary" type="button" data-i18n="display.close"></button></div>`;
  settings.querySelector('button').addEventListener('click', () => settings.close());
  document.body.append(settings);

  function apply() {
    root.lang = preferences.language;
    root.toggleAttribute('data-large-text', preferences.large);
    root.toggleAttribute('data-high-contrast', preferences.contrast);
    root.toggleAttribute('data-reduced-motion', preferences.motion);
    settings.querySelector('select').value = preferences.language;
    settings.querySelectorAll('input').forEach(input => { input.checked = preferences[input.name]; });
    localize();
    document.dispatchEvent(new CustomEvent('margin-interface-change'));
  }

  settings.addEventListener('change', event => {
    const field = event.target;
    preferences[field.name] = field.type === 'checkbox' ? field.checked : field.value;
    try { localStorage.setItem(key, JSON.stringify(preferences)); }
    catch { /* Continue with in-memory preferences. */ }
    apply();
  });
  document.addEventListener('click', event => {
    if (event.target.closest('[data-display-settings]')) {
      settings.showModal();
      settings.querySelector('select').focus();
    }
    const skip = event.target.closest('[data-skip]');
    if (skip) {
      const main = document.getElementById(skip.dataset.skip);
      if (main) {
        event.preventDefault();
        main.focus();
      }
    }
  });
  apply();

  return {t, number, dateTime, localize, language: () => preferences.language};
})();
